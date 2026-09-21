from datetime import datetime
import json
import re
import time
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_settings
from src.schemas import StoryItem, TrackOutput
from src.track_a.heuristics import filter_and_rank
from src.track_a.ingest import ingest_all

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
Given the following pre-ranked list of AI developments from the last 7 days, you MUST select exactly 5 most impactful and technical stories. Do not return fewer than 5 stories.

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


def extract_arxiv_id(url: str) -> str | None:
    match = re.search(r"(\d{4}\.\d{4,5})", url or "")
    return match.group(1) if match else None


async def run_track_a() -> tuple[TrackOutput, dict]:
    start_time = time.perf_counter()
    raw = await ingest_all()
    ranked = filter_and_rank(raw, lookback_days=settings.lookback_days)

    # Pure deterministic ranking: top candidates strictly by composite score
    CANDIDATE_POOL_SIZE = 20
    top_candidates = ranked[:CANDIDATE_POOL_SIZE]

    # Map signals: ArXiv upvotes, Hacker News community points, and publication dates
    signal_map: dict[str, int] = {}
    date_map: dict[str, datetime] = {}

    for c in top_candidates:
        clean_url = c.url.rstrip("/")
        date_map[clean_url] = c.published_at

        aid = extract_arxiv_id(c.url)
        if aid:
            date_map[aid] = c.published_at

        if "ArXiv" in c.source:
            raw_upvotes = int(round(c.score / 1.5)) if c.score else 0
            signal_map[clean_url] = raw_upvotes
            if aid:
                signal_map[aid] = raw_upvotes
        elif "Hacker News" in c.source:
            # Matches: "(HN Points: 333)" or "(HN Community Points: 333)"
            match = re.search(r"HN (?:Community )?Points:\s*(\d+)", c.raw_text)
            if match:
                signal_map[clean_url] = int(match.group(1))

    payload = []
    for idx, c in enumerate(top_candidates, 1):
        payload.append(
            f"{idx}. [{c.source}] {c.title} (URL: {c.url})\nSnippet: {c.raw_text[:200]}"
        )

    formatted_input = "\n\n".join(payload)
    llm = get_llm()

    messages = [
        SystemMessage(
            content="You extract high-signal technical AI breakthroughs into strict JSON format. You must select exactly 5 stories."
        ),
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
        "reasoning_tokens": usage.get("completion_tokens_details", {}).get(
            "reasoning_tokens", 0
        ),
        "total_tokens": usage.get("total_tokens", 0),
    }

    content = response.content.strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            parsed = json.loads(content[start : end + 1])
        else:
            raise

    raw_stories = parsed.get("stories", [])[: settings.story_cap]
    stories = []
    for s in raw_stories:
        url_val = s.get("url", "").rstrip("/")
        if not s.get("source"):
            if "arxiv" in url_val.lower():
                s["source"] = "ArXiv"
            elif any(k in url_val.lower() for k in ["techmeme", "theinformation", "techcrunch"]):
                s["source"] = "Techmeme"
            else:
                s["source"] = "Industry & Models"

        # Populate community signal (ArXiv upvotes or HN points)
        aid = extract_arxiv_id(url_val)
        if url_val in signal_map:
            s["upvotes"] = signal_map[url_val]
        elif aid and aid in signal_map:
            s["upvotes"] = signal_map[aid]
        else:
            s["upvotes"] = None

        # Populate publication timestamp and formatted date
        pub_dt = date_map.get(url_val) or (date_map.get(aid) if aid else None)
        if pub_dt:
            s["published_at"] = pub_dt
            s["published_date_str"] = pub_dt.strftime("%a, %d %b")

        stories.append(StoryItem(**s))

    return TrackOutput(track_name="Track A", stories=stories), telemetry