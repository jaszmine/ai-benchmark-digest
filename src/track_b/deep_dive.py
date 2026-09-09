import json
import time
from urllib.parse import urlparse, urlunparse
from langchain_core.messages import SystemMessage, HumanMessage
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from src.config import get_settings
from src.schemas import TrackOutput, StoryItem
from src.track_a.pipeline import get_llm
from src.track_b.agent import build_research_graph, update_token_metrics
from src.track_b.state import AgentState, TokenTracker

settings = get_settings()

SYNTHESIS_PROMPT_TEMPLATE = """You are a senior AI research analyst.
Analyze the collected research notes and synthesize:
1. Exactly the Top 5 most significant AI developments, models, or research findings formatted into the target JSON structure.
2. A ~200-word architectural deep-dive focused on the single most significant development (#1). Detail how it works under the hood, key technical decisions, tradeoffs, and implications for practitioners.

Strict Constraints:
- Every story MUST have a completely UNIQUE canonical URL.
- Retain the exact "Original Title" and "Published Date" verbatim from the research notes.
- Grounding: Your 2-3 sentence summary MUST reflect only what is actually reported in the excerpt. Do NOT invent evaluations, synthetic model links, or claims not present in the notes.

Return valid JSON matching this schema:
{{
  "stories": [
    {{
      "title": "Your enhanced technical headline",
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
        SystemMessage(content="You format synthesized AI research into strict structured JSON with unique URLs."),
        HumanMessage(content=formatted_synth_prompt),
    ]

    synth_resp = await invoke_synth_with_retry(llm, synth_messages)
    synth_usage = getattr(synth_resp, "response_metadata", {}).get("token_usage", {})
    total_telemetry = update_token_metrics(final_state["telemetry"], synth_usage)

    elapsed = time.perf_counter() - start_time

    content = synth_resp.content
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    parsed = json.loads(content)

    seen_urls = set()
    deduped_stories: list[StoryItem] = []
    for raw_item in parsed.get("stories", []):
        canon_url = normalize_url(raw_item.get("url", ""))
        if canon_url and canon_url not in seen_urls:
            seen_urls.add(canon_url)
            deduped_stories.append(StoryItem(**raw_item))
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

    return TrackOutput(track_name="Track B", stories=deduped_stories, deep_dive=deep_dive), telemetry_report
