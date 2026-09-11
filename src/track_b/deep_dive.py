import json
import re
import time
from urllib.parse import urlparse, urlunparse
from json_repair import repair_json
from langchain_core.messages import HumanMessage, SystemMessage
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_settings
from src.schemas import StoryItem, TrackOutput
from src.track_a.pipeline import get_llm
from src.track_b.agent import build_research_graph, update_token_metrics
from src.track_b.state import AgentState, TokenTracker

settings = get_settings()

SYNTHESIS_PROMPT_TEMPLATE = """You are a senior AI research analyst.
Analyze the collected research notes and synthesize:
1. Exactly the Top 5 most significant AI developments, models, or research findings formatted into the target JSON structure.
2. A ~200-word architectural deep-dive focused on the single most significant development (#1). Detail how it works under the hood, key technical decisions, tradeoffs, and implications for practitioners.

Strict Constraints:
- ACCURATE HEADLINES: The 'title' field must be the actual, factual headline of the published article. Do NOT invent fanciful names, unannounced model versions, or extrapolate beyond what is documented in the source.
- Every story MUST have a completely UNIQUE canonical URL.
- Retain the exact "Original Title" and "Published Date" verbatim from the research notes.
- Grounding: Your 2-3 sentence summary MUST reflect only what is actually reported in the excerpt. Do NOT invent evaluations, synthetic model links, or claims not present in the notes.
- CRITICAL REQUIREMENT: You must extract and return exactly 5 distinct stories in the 'stories' list. Never return fewer than 5.
- SYNTAX: Return strictly valid JSON. Avoid trailing commas and properly escape double quotes inside text.
- ABSOLUTE PROHIBITION ON GENERATED URLS: You are strictly forbidden from fabricating, predicting, or constructing URL paths. The 'url' field MUST be copied character-for-character from the 'URL: ...' field in the Research Notes. If a candidate in the notes lacks a full URL, do not include it.

Return valid JSON matching this schema:
{{
  "stories": [
    {{
      "title": "Exact or faithful headline from the article",
      "original_title": "Original Title copied verbatim from notes",
      "published_date_str": "Published Date copied verbatim (e.g. Sept. 8, 2026)",
      "url": "Canonical URL from findings",
      "category": "Research & Papers | Industry & Models | Infrastructure & Tools | Policy & Ethics",
      "summary": "2-3 dense sentences strictly grounded in the excerpt.",
      "source": "Publisher Name, Lab, or Community"
    }}
  ],
  "deep_dive": "The ~200-word architectural deep-dive text..."
}}

Research Notes:
{notes}
"""


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def robust_json_decode(raw_text: str) -> dict:
    """Tolerant JSON parser tailored for LLM outputs."""
    cleaned = raw_text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].split("```")[0].strip()

    # Pass 1: Standard load
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Pass 2: Outermost boundary slicing
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        sliced = cleaned[start : end + 1]
        try:
            return json.loads(sliced)
        except Exception:
            pass

        # Pass 3: Strip trailing commas before braces or brackets
        sans_trailing_commas = re.sub(r",\s*([\]}])", r"\1", sliced)
        try:
            return json.loads(sans_trailing_commas)
        except Exception:
            pass

        # Pass 4: json-repair engine
        try:
            repaired = repair_json(sliced, return_objects=True)
            if isinstance(repaired, dict):
                return repaired
        except Exception:
            pass

    # Pass 5: Direct string repair as last line of defense
    repaired_all = repair_json(cleaned, return_objects=True)
    if isinstance(repaired_all, dict):
        return repaired_all

    raise ValueError(f"Failed to parse or repair JSON from model output:\n{cleaned[:500]}...")


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=3, min=4, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def invoke_synth_with_retry(llm, messages):
    return await llm.ainvoke(messages)


async def run_track_b() -> tuple[TrackOutput, dict]:
    start_time = time.perf_counter()

    initial_telemetry: TokenTracker = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }

    initial_state: AgentState = {
        "messages": [
            HumanMessage(
                content=(
                    "Investigate the most notable recent AI developments, model releases, "
                    "and research papers. Gather at least 6 distinct articles with different URLs."
                )
            )
        ],
        "turn_count": 0,
        "max_turns": settings.max_track_b_turns,
        "telemetry": initial_telemetry,
        "accumulated_findings": [],
    }

    graph = build_research_graph()
    final_state = await graph.ainvoke(initial_state)

    findings = final_state.get("accumulated_findings", [])
    pruned_context = (
        "\n\n".join(findings)
        if findings
        else "No external search tools succeeded; summarize recent breakthroughs from internal knowledge."
    )

    llm = get_llm()
    formatted_synth_prompt = SYNTHESIS_PROMPT_TEMPLATE.format(notes=pruned_context)

    synth_messages = [
        SystemMessage(
            content="You format synthesized AI research into strict structured JSON with unique URLs."
        ),
        HumanMessage(content=formatted_synth_prompt),
    ]

    synth_resp = await invoke_synth_with_retry(llm, synth_messages)
    synth_usage = getattr(synth_resp, "response_metadata", {}).get("token_usage", {})
    total_telemetry = update_token_metrics(final_state["telemetry"], synth_usage)

    elapsed = time.perf_counter() - start_time

    # Robust parsing
    parsed = robust_json_decode(synth_resp.content)

    seen_urls = set()
    deduped_stories: list[StoryItem] = []
    for raw_item in parsed.get("stories", []):
        if not raw_item.get("source"):
            raw_item["source"] = "Industry & Models"
        if not raw_item.get("category"):
            raw_item["category"] = "Industry & Models"

        canon_url = normalize_url(raw_item.get("url", ""))
        if canon_url and canon_url not in seen_urls:
            seen_urls.add(canon_url)
            try:
                deduped_stories.append(StoryItem(**raw_item))
            except Exception as item_err:
                print(f"[Track B] Skipping malformed story item: {item_err}")

        if len(deduped_stories) == settings.story_cap:
            break

    deep_dive = parsed.get("deep_dive", "Deep dive synthesis completed.")

    telemetry_report = {
        "track": "Track B",
        "wall_time_seconds": round(elapsed, 2),
        "prompt_tokens": total_telemetry["prompt_tokens"],
        "completion_tokens": total_telemetry["completion_tokens"],
        "reasoning_tokens": total_telemetry["reasoning_tokens"],
        "total_tokens": total_telemetry["total_tokens"],
        "agent_turns": final_state["turn_count"],
    }

    return TrackOutput(
        track_name="Track B", stories=deduped_stories, deep_dive=deep_dive
    ), telemetry_report