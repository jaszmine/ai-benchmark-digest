import json
import time
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from src.config import get_settings
from src.schemas import TrackOutput, StoryItem
from src.track_a.ingest import ingest_all
from src.track_a.heuristics import filter_and_rank

settings = get_settings()


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.model_name,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        default_headers={
            "HTTP-Referer": settings.app_referer,
            "X-Title": settings.app_title,
        },
        temperature=0.1,
        max_retries=1, 
    )


EXTRACTION_PROMPT = """You are an expert AI systems curator.
Given the following pre-ranked list of AI developments from the last 7 days, select exactly the TOP 5 most impactful and technical stories.

Return a valid JSON object matching this schema:
{
  "stories": [
    {
      "title": "Clear technical headline",
      "url": "Canonical link from the candidate",
      "category": "Research & Papers | Industry & Models | Infrastructure & Tools | Policy & Ethics",
      "summary": "2-3 dense sentences explaining what it is, why it matters, and technical specs.",
      "source": "ArXiv | Techmeme | Hacker News"
    }
  ]
}

Candidates:
"""


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=3, min=4, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def invoke_llm_with_retry(llm, messages):
    return await llm.ainvoke(messages)


async def run_track_a() -> tuple[TrackOutput, dict]:
    start_time = time.perf_counter()
    raw = await ingest_all()
    ranked = filter_and_rank(raw, lookback_days=settings.lookback_days)

    # top_candidates = ranked[:12]

    # Balanced source selection (replaces: top_candidates = ranked[:12])
    arxiv_candidates = [c for c in ranked if c.source == "ArXiv"][:4]
    hn_candidates = [c for c in ranked if c.source == "Hacker News"][:4]
    techmeme_candidates = [c for c in ranked if c.source == "Techmeme"][:4]

    top_candidates = arxiv_candidates + hn_candidates + techmeme_candidates
    

    payload = []
    for idx, c in enumerate(top_candidates, 1):
        payload.append(f"{idx}. [{c.source}] {c.title} (URL: {c.url})\nSnippet: {c.raw_text[:200]}")

    formatted_input = "\n\n".join(payload)
    llm = get_llm()

    messages = [
        SystemMessage(content="You extract high-signal technical AI breakthroughs into strict JSON format."),
        HumanMessage(content=EXTRACTION_PROMPT + formatted_input),
    ]

    response = await invoke_llm_with_retry(llm, messages)
    elapsed = time.perf_counter() - start_time

    usage = response.response_metadata.get("token_usage", {})
    telemetry = {
        "track": "Track A",
        "wall_time_seconds": round(elapsed, 2),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "reasoning_tokens": usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }

    content = response.content
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    parsed = json.loads(content)
    stories = [StoryItem(**s) for s in parsed.get("stories", [])[:settings.story_cap]]

    return TrackOutput(track_name="Track A", stories=stories), telemetry
