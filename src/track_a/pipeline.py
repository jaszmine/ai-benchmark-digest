import asyncio
from datetime import datetime
import json
import re
import time
from zoneinfo import ZoneInfo
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_settings
from src.schemas import StoryItem, TrackOutput
from src.track_a.heuristics import filter_and_rank
from src.track_a.ingest import ingest_all

settings = get_settings()
CENTRAL_TZ = ZoneInfo("America/Chicago")


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


EXTRACTION_PROMPT = """You are an expert AI systems curator and technical editor.
Your job is to select the 5 most technically significant, diverse, and impactful AI stories from the provided candidate list.

Rules:
1. Select EXACTLY 5 stories.
2. Maintain balance across categories:
   - "Research & Papers" (breakthrough architectures, theoretical results)
   - "Industry & Models" (major frontier model releases, frontier lab announcements)
   - "Infrastructure & Tools" (runtimes, training clusters, inference optimization, developer tooling)
   - "Policy & Ethics" (governance, evals, empirical AI capability assessments, copyright)
3. For each selected story, write a concise 2-sentence summary explaining what was achieved and why it matters.

Return a valid JSON object matching this schema:
{
  "stories": [
    {
      "title": "Exact candidate title",
      "url": "Exact candidate URL",
      "category": "Research & Papers | Industry & Models | Infrastructure & Tools | Policy & Ethics",
      "summary": "2-3 dense sentences explaining what it is, why it matters, and technical specs.",
      "source": "Exact source from candidate (e.g. ArXiv (via Hugging Face), Techmeme, Hacker News, AI as Normal Technology)"
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

    # Pure deterministic ranking: take top 20 candidates strictly by heuristic score
    CANDIDATE_POOL_SIZE = 20
    top_candidates = ranked[:CANDIDATE_POOL_SIZE]

    # Map signals and publication dates
    signal_map: dict[str, int] = {}
    date_map: dict[str, tuple[datetime, str]] = {}
    source_map: dict[str, str] = {}

    for c in top_candidates:
        clean_url = c.url.rstrip("/")
        pub_dt = c.published_at
        formatted_date = pub_dt.strftime("%a, %d %b %Y")
        
        date_map[clean_url] = (pub_dt, formatted_date)
        source_map[clean_url] = c.source

        aid = extract_arxiv_id(c.url)
        if aid:
            date_map[aid] = (pub_dt, formatted_date)
            source_map[aid] = c.source

        if "ArXiv" in c.source:
            raw_upvotes = int(round(c.score / 1.5)) if c.score else 0
            signal_map[clean_url] = raw_upvotes
            if aid:
                signal_map[aid] = raw_upvotes
        elif "Hacker News" in c.source:
            match = re.search(r"HN (?:Community )?Points:\s*(\d+)", c.raw_text)
            if match:
                signal_map[clean_url] = int(match.group(1))
        elif "Normal Technology" in c.source:
            match = re.search(r"Likes:\s*(\d+)", c.raw_text)
            if match:
                signal_map[clean_url] = int(match.group(1))

    payload = []
    for idx, c in enumerate(top_candidates, 1):
        payload.append(
            f"[{idx}] Source: {c.source}\nTitle: {c.title}\nURL: {c.url}\nScore: {c.score}\nInfo: {c.raw_text}"
        )

    formatted_input = "\n\n".join(payload)
    llm = get_llm()

    messages = [
        SystemMessage(
            content="You extract high-signal technical AI breakthroughs across research, industry, tooling, and policy into strict JSON format. You must select exactly 5 stories."
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
        aid = extract_arxiv_id(url_val)

        # Retain original candidate source name if matched
        matched_source = source_map.get(url_val) or (source_map.get(aid) if aid else None)
        if not matched_source:
            matched_source = s.get("source", "Techmeme")
        s["source"] = matched_source

        # Map community signal
        if url_val in signal_map:
            s["upvotes"] = signal_map[url_val]
        elif aid and aid in signal_map:
            s["upvotes"] = signal_map[aid]
        else:
            s["upvotes"] = None

        # Populate publication timestamp and formatted date string
        pub_info = date_map.get(url_val) or (date_map.get(aid) if aid else None)
        if pub_info:
            pub_dt, pub_str = pub_info
            s["published_at"] = pub_dt
            s["published_date_str"] = pub_str

        stories.append(StoryItem(**s))

    return TrackOutput(track_name="Track A", stories=stories), telemetry


if __name__ == "__main__":
    out, tel = asyncio.run(run_track_a())
    print(f"Track A produced {len(out.stories)} stories in {tel['wall_time_seconds']}s")
    for st in out.stories:
        print(f"- [{st.category}] {st.title} ({st.source}) - {getattr(st, 'published_date_str', 'No date')}")