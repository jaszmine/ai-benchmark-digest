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

    # # 1. Balanced source sampling
    # arxiv_candidates = [c for c in ranked if "ArXiv" in c.source][:4]
    # hn_candidates = [c for c in ranked if "Hacker News" in c.source][:4]
    # techmeme_candidates = [c for c in ranked if "Techmeme" in c.source][:4]

    # top_candidates = arxiv_candidates + hn_candidates + techmeme_candidates

    # # 2. Pad to guarantee 10-12 diverse candidates reach prompt
    # if len(top_candidates) < 10:
    #     seen = {c.url for c in top_candidates}
    #     for c in ranked:
    #         if c.url not in seen:
    #             top_candidates.append(c)
    #             seen.add(c.url)
    #         if len(top_candidates) >= 12:
    #             break

    # Pure deterministic ranking: take the absolute top candidates by composite score
    CANDIDATE_POOL_SIZE = 20  # Increased from 12 (16-20 is ideal)
    top_candidates = ranked[:CANDIDATE_POOL_SIZE]

    # Build an upvote lookup map keyed by canonical URL and ArXiv ID
    upvote_map: dict[str, int] = {}
    for c in top_candidates:
        if "ArXiv" in c.source:
            # Score was assigned as upvotes * 1.5 in ingest
            raw_upvotes = int(round(c.score / 1.5)) if c.score else 0
            upvote_map[c.url.rstrip("/")] = raw_upvotes
            aid = extract_arxiv_id(c.url)
            if aid:
                upvote_map[aid] = raw_upvotes

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

        # Populate upvotes only if originating from ArXiv / Hugging Face
        if "arxiv" in url_val.lower() or "arxiv" in s.get("source", "").lower():
            aid = extract_arxiv_id(url_val)
            s["upvotes"] = upvote_map.get(url_val) or (upvote_map.get(aid) if aid else None)
        else:
            s["upvotes"] = None

        stories.append(StoryItem(**s))

    return TrackOutput(track_name="Track A", stories=stories), telemetry

# import json
# import time
# from langchain_core.messages import HumanMessage, SystemMessage
# from langchain_openai import ChatOpenAI
# from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# from src.config import get_settings
# from src.schemas import StoryItem, TrackOutput
# from src.track_a.heuristics import filter_and_rank
# from src.track_a.ingest import ingest_all

# settings = get_settings()


# def get_llm() -> ChatOpenAI:
#     return ChatOpenAI(
#         model=settings.model_name,
#         api_key=settings.openrouter_api_key,
#         base_url=settings.openrouter_base_url,
#         default_headers={
#             "HTTP-Referer": settings.app_referer,
#             "X-Title": settings.app_title,
#         },
#         temperature=0.1,
#         max_retries=1,
#     )


# EXTRACTION_PROMPT = """You are an expert AI systems curator.
# Given the following pre-ranked list of AI developments from the last 7 days, you MUST select exactly 5 most impactful and technical stories. Do not return fewer than 5 stories.

# Return a valid JSON object matching this schema:
# {
#   "stories": [
#     {
#       "title": "Clear technical headline",
#       "url": "Canonical link from the candidate",
#       "category": "Research & Papers | Industry & Models | Infrastructure & Tools | Policy & Ethics",
#       "summary": "2-3 dense sentences explaining what it is, why it matters, and technical specs.",
#       "source": "ArXiv | Techmeme | Hacker News"
#     }
#   ]
# }

# Candidates:
# """


# @retry(
#     stop=stop_after_attempt(4),
#     wait=wait_exponential(multiplier=3, min=4, max=30),
#     retry=retry_if_exception_type(Exception),
#     reraise=True,
# )
# async def invoke_llm_with_retry(llm, messages):
#     return await llm.ainvoke(messages)


# async def run_track_a() -> tuple[TrackOutput, dict]:
#     start_time = time.perf_counter()
#     raw = await ingest_all()
#     ranked = filter_and_rank(raw, lookback_days=settings.lookback_days)

#     # 1. Balanced source sampling using substring matching
#     arxiv_candidates = [c for c in ranked if "ArXiv" in c.source][:4]
#     hn_candidates = [c for c in ranked if "Hacker News" in c.source][:4]
#     techmeme_candidates = [c for c in ranked if "Techmeme" in c.source][:4]

#     top_candidates = arxiv_candidates + hn_candidates + techmeme_candidates

#     # 2. Pad to guarantee 10-12 diverse candidates reach the prompt
#     if len(top_candidates) < 10:
#         seen = {c.url for c in top_candidates}
#         for c in ranked:
#             if c.url not in seen:
#                 top_candidates.append(c)
#                 seen.add(c.url)
#             if len(top_candidates) >= 12:
#                 break

#     payload = []
#     for idx, c in enumerate(top_candidates, 1):
#         payload.append(
#             f"{idx}. [{c.source}] {c.title} (URL: {c.url})\nSnippet: {c.raw_text[:200]}"
#         )

#     formatted_input = "\n\n".join(payload)
#     llm = get_llm()

#     messages = [
#         SystemMessage(
#             content="You extract high-signal technical AI breakthroughs into strict JSON format. You must select exactly 5 stories."
#         ),
#         HumanMessage(content=EXTRACTION_PROMPT + formatted_input),
#     ]

#     response = await invoke_llm_with_retry(llm, messages)
#     elapsed = time.perf_counter() - start_time

#     usage = response.response_metadata.get("token_usage", {})
#     telemetry = {
#         "track": "Track A",
#         "wall_time_seconds": round(elapsed, 2),
#         "prompt_tokens": usage.get("prompt_tokens", 0),
#         "completion_tokens": usage.get("completion_tokens", 0),
#         "reasoning_tokens": usage.get("completion_tokens_details", {}).get(
#             "reasoning_tokens", 0
#         ),
#         "total_tokens": usage.get("total_tokens", 0),
#     }

#     content = response.content.strip()

#     # Handle markdown fences
#     if "```json" in content:
#         content = content.split("```json")[1].split("```")[0].strip()
#     elif "```" in content:
#         content = content.split("```")[1].split("```")[0].strip()

#     # Slicing boundaries to protect against trailing commentary
#     try:
#         parsed = json.loads(content)
#     except json.JSONDecodeError:
#         start = content.find("{")
#         end = content.rfind("}")
#         if start != -1 and end != -1 and end > start:
#             parsed = json.loads(content[start : end + 1])
#         else:
#             raise

#     # Defensively construct StoryItems with missing-field fallbacks
#     raw_stories = parsed.get("stories", [])[: settings.story_cap]
#     stories = []
#     for s in raw_stories:
#         if not s.get("source"):
#             url_val = s.get("url", "").lower()
#             if "arxiv" in url_val:
#                 s["source"] = "ArXiv"
#             elif "techmeme" in url_val or "theinformation" in url_val or "techcrunch" in url_val:
#                 s["source"] = "Techmeme"
#             else:
#                 s["source"] = "Industry & Models"
#         stories.append(StoryItem(**s))

#     return TrackOutput(track_name="Track A", stories=stories), telemetry

# # import json
# # import time
# # from langchain_openai import ChatOpenAI
# # from langchain_core.messages import SystemMessage, HumanMessage
# # from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
# # from src.config import get_settings
# # from src.schemas import TrackOutput, StoryItem
# # from src.track_a.ingest import ingest_all
# # from src.track_a.heuristics import filter_and_rank

# # settings = get_settings()


# # def get_llm() -> ChatOpenAI:
# #     return ChatOpenAI(
# #         model=settings.model_name,
# #         api_key=settings.openrouter_api_key,
# #         base_url=settings.openrouter_base_url,
# #         default_headers={
# #             "HTTP-Referer": settings.app_referer,
# #             "X-Title": settings.app_title,
# #         },
# #         temperature=0.1,
# #         max_retries=1, 
# #     )


# # EXTRACTION_PROMPT = """You are an expert AI systems curator.
# # Given the following pre-ranked list of AI developments from the last 7 days, select exactly the TOP 5 most impactful and technical stories.

# # Return a valid JSON object matching this schema:
# # {
# #   "stories": [
# #     {
# #       "title": "Clear technical headline",
# #       "url": "Canonical link from the candidate",
# #       "category": "Research & Papers | Industry & Models | Infrastructure & Tools | Policy & Ethics",
# #       "summary": "2-3 dense sentences explaining what it is, why it matters, and technical specs.",
# #       "source": "ArXiv | Techmeme | Hacker News"
# #     }
# #   ]
# # }

# # Candidates:
# # """


# # @retry(
# #     stop=stop_after_attempt(4),
# #     wait=wait_exponential(multiplier=3, min=4, max=30),
# #     retry=retry_if_exception_type(Exception),
# #     reraise=True,
# # )
# # async def invoke_llm_with_retry(llm, messages):
# #     return await llm.ainvoke(messages)


# # async def run_track_a() -> tuple[TrackOutput, dict]:
# #     start_time = time.perf_counter()
# #     raw = await ingest_all()
# #     ranked = filter_and_rank(raw, lookback_days=settings.lookback_days)

# #     # top_candidates = ranked[:12]

# #     # Balanced source selection (replaces: top_candidates = ranked[:12])
# #     arxiv_candidates = [c for c in ranked if c.source == "ArXiv"][:4]
# #     hn_candidates = [c for c in ranked if c.source == "Hacker News"][:4]
# #     techmeme_candidates = [c for c in ranked if c.source == "Techmeme"][:4]

# #     top_candidates = arxiv_candidates + hn_candidates + techmeme_candidates

# #     if len(top_candidates) < 10:
# #         seen = {c.url for c in top_candidates}
# #         for c in ranked:
# #             if c.url not in seen:
# #                 top_candidates.append(c)
# #                 seen.add(c.url)
# #             if len(top_candidates) >= 12:
# #                 break
    

# #     payload = []
# #     for idx, c in enumerate(top_candidates, 1):
# #         payload.append(f"{idx}. [{c.source}] {c.title} (URL: {c.url})\nSnippet: {c.raw_text[:200]}")

# #     formatted_input = "\n\n".join(payload)
# #     llm = get_llm()

# #     messages = [
# #         SystemMessage(content="You extract high-signal technical AI breakthroughs into strict JSON format."),
# #         HumanMessage(content=EXTRACTION_PROMPT + formatted_input),
# #     ]

# #     response = await invoke_llm_with_retry(llm, messages)
# #     elapsed = time.perf_counter() - start_time

# #     usage = response.response_metadata.get("token_usage", {})
# #     telemetry = {
# #         "track": "Track A",
# #         "wall_time_seconds": round(elapsed, 2),
# #         "prompt_tokens": usage.get("prompt_tokens", 0),
# #         "completion_tokens": usage.get("completion_tokens", 0),
# #         "reasoning_tokens": usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
# #         "total_tokens": usage.get("total_tokens", 0),
# #     }

# #     content = response.content
# #     if "```json" in content:
# #         content = content.split("```json")[1].split("```")[0].strip()
# #     elif "```" in content:
# #         content = content.split("```")[1].split("```")[0].strip()

# #     parsed = json.loads(content)
# #     # stories = [StoryItem(**s) for s in parsed.get("stories", [])[:settings.story_cap]]
# #     raw_stories = parsed.get("stories", [])[: settings.story_cap]
# #     stories = []
# #     for s in raw_stories:
# #         if "source" not in s or not s["source"]:
# #             # Fallback to general category or domain if LLM skipped it
# #             s["source"] = "Techmeme" if "techmeme" in s.get("url", "") else "ArXiv (via Hugging Face)"
# #         stories.append(StoryItem(**s))

# #     return TrackOutput(track_name="Track A", stories=stories), telemetry
