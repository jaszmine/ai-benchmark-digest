import asyncio
from datetime import datetime, timezone
import re
import urllib.parse

import feedparser
import httpx

from src.config import get_settings
from src.schemas import RawCandidate

settings = get_settings()


async def fetch_techmeme(client: httpx.AsyncClient) -> list[RawCandidate]:
    candidates = []
    url = "https://www.techmeme.com/feed.xml"
    try:
        resp = await client.get(url, timeout=settings.http_timeout_seconds)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.text)
            for entry in feed.entries:
                pub_time = datetime.fromtimestamp(
                    entry.get("published_parsed")
                    and datetime(*entry.published_parsed[:6]).timestamp()
                    or datetime.now(timezone.utc).timestamp(),
                    tz=timezone.utc,
                )

                # Extract first outbound news link, bypassing Techmeme's internal anchor
                desc = entry.get("summary") or entry.get("description") or ""
                all_links = re.findall(r'href=[\"\'](https?://[^\"\']+)[\"\']', desc)
                external_links = [
                    u
                    for u in all_links
                    if "techmeme.com" not in u
                    and "twitter.com" not in u
                    and "x.com" not in u
                ]
                target_url = external_links[0] if external_links else entry.get("link", "")

                candidates.append(
                    RawCandidate(
                        title=entry.get("title", ""),
                        url=target_url,
                        source="Techmeme",
                        published_at=pub_time,
                        raw_text=desc or entry.get("title", ""),
                        score=50.0,
                    )
                )
    except Exception as e:
        print(f"[Ingest] Techmeme fetch warning: {e}")
    return candidates


async def fetch_hf_daily_papers(client: httpx.AsyncClient) -> list[RawCandidate]:
    """Fetches trending AI research preprints ranked by community upvotes via Hugging Face Daily Papers."""
    candidates = []
    url = "https://huggingface.co/api/daily_papers?limit=25"
    try:
        resp = await client.get(url, timeout=settings.http_timeout_seconds)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("results", data) if isinstance(data, dict) else data
            for item in items:
                paper = item.get("paper", item)
                arxiv_id = paper.get("id") or paper.get("arxivId")
                title = paper.get("title", "").replace("\n", " ").strip()
                summary = paper.get("summary", "").replace("\n", " ").strip()
                upvotes = float(paper.get("upvotes", 0))

                pub_raw = paper.get("publishedAt")
                if pub_raw:
                    try:
                        pub_time = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                    except Exception:
                        pub_time = datetime.now(timezone.utc)
                else:
                    pub_time = datetime.now(timezone.utc)

                if arxiv_id and title:
                    hf_discussion_url = f"https://huggingface.co/papers/{arxiv_id}"
                    annotated_summary = (
                        f"{summary} | HF Discussion: {hf_discussion_url} | Upvotes: {int(upvotes)}"
                    )
                    candidates.append(
                        RawCandidate(
                            title=title,
                            url=f"https://arxiv.org/abs/{arxiv_id}",
                            source="ArXiv (via Hugging Face)",
                            published_at=pub_time,
                            raw_text=annotated_summary,
                            score=upvotes * 1.5,
                        )
                    )
    except Exception as e:
        print(f"[Ingest] Hugging Face Daily Papers warning: {e}")
    return candidates


async def fetch_hacker_news(client: httpx.AsyncClient) -> list[RawCandidate]:
    candidates = []
    lookback_secs = settings.lookback_days * 86400
    cutoff = int(datetime.now(timezone.utc).timestamp()) - lookback_secs
    query = "AI OR LLM OR model OR architecture OR open-source"
    url = (
        f"https://hn.algolia.com/api/v1/search_by_date?"
        f"query={urllib.parse.quote(query)}&tags=story&numericFilters=created_at_i>{cutoff},points>5&hitsPerPage=20"
    )
    try:
        resp = await client.get(url, timeout=settings.http_timeout_seconds)
        if resp.status_code == 200:
            hits = resp.json().get("hits", [])
            for hit in hits:
                story_url = (
                    hit.get("url")
                    or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                )
                created_ts = hit.get(
                    "created_at_i", int(datetime.now(timezone.utc).timestamp())
                )
                pub_time = datetime.fromtimestamp(created_ts, tz=timezone.utc)
                candidates.append(
                    RawCandidate(
                        title=hit.get("title", ""),
                        url=story_url,
                        source="Hacker News",
                        published_at=pub_time,
                        raw_text=hit.get("title", ""),
                        score=float(hit.get("points", 0)),
                    )
                )
    except Exception as e:
        print(f"[Ingest] Hacker News Algolia fetch warning: {e}")
    return candidates


async def ingest_all() -> list[RawCandidate]:
    headers = {"User-Agent": f"{settings.app_title}/1.0"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        results = await asyncio.gather(
            fetch_techmeme(client),
            fetch_hf_daily_papers(client),
            fetch_hacker_news(client),
            return_exceptions=False,
        )
    return [item for sublist in results for item in sublist]

# import asyncio
# import urllib.parse
# from datetime import datetime, timezone
# import feedparser
# import httpx
# from src.config import get_settings
# from src.schemas import RawCandidate
# import re

# settings = get_settings()


# # async def fetch_techmeme(client: httpx.AsyncClient) -> list[RawCandidate]:
# #     candidates = []
# #     url = "https://www.techmeme.com/feed.xml"
# #     try:
# #         resp = await client.get(url, timeout=settings.http_timeout_seconds)
# #         if resp.status_code == 200:
# #             feed = feedparser.parse(resp.text)
# #             for entry in feed.entries:
# #                 pub_time = datetime.fromtimestamp(
# #                     entry.get("published_parsed")
# #                     and datetime(*entry.published_parsed[:6]).timestamp()
# #                     or datetime.now(timezone.utc).timestamp(),
# #                     tz=timezone.utc,
# #                 )
# #                 candidates.append(
# #                     RawCandidate(
# #                         title=entry.get("title", ""),
# #                         url=entry.get("link", ""),
# #                         source="Techmeme",
# #                         published_at=pub_time,
# #                         raw_text=entry.get("summary", "") or entry.get("title", ""),
# #                         score=50.0,
# #                     )
# #                 )
# #     except Exception as e:
# #         print(f"[Ingest] Techmeme fetch warning: {e}")
# #     return candidates
# async def fetch_techmeme(client: httpx.AsyncClient) -> list[RawCandidate]:
#     candidates = []
#     url = "https://www.techmeme.com/feed.xml"
#     try:
#         resp = await client.get(url, timeout=settings.http_timeout_seconds)
#         if resp.status_code == 200:
#             feed = feedparser.parse(resp.text)
#             for entry in feed.entries:
#                 pub_time = datetime.fromtimestamp(
#                     entry.get("published_parsed")
#                     and datetime(*entry.published_parsed[:6]).timestamp()
#                     or datetime.now(timezone.utc).timestamp(),
#                     tz=timezone.utc,
#                 )
                
#                 # Extract first outbound link that isn't Techmeme itself
#                 desc = entry.get("summary") or entry.get("description") or ""
#                 all_links = re.findall(r'href=[\"\'](https?://[^\"\']+)[\"\']', desc)
#                 external_links = [
#                     u for u in all_links
#                     if "techmeme.com" not in u and "twitter.com" not in u and "x.com" not in u
#                 ]
#                 target_url = external_links[0] if external_links else entry.get("link", "")

#                 candidates.append(
#                     RawCandidate(
#                         title=entry.get("title", ""),
#                         url=target_url,
#                         source="Techmeme",
#                         published_at=pub_time,
#                         raw_text=desc or entry.get("title", ""),
#                         score=50.0,
#                     )
#                 )
#     except Exception as e:
#         print(f"[Ingest] Techmeme fetch warning: {e}")
#     return candidates

# async def fetch_hf_daily_papers(client: httpx.AsyncClient) -> list[RawCandidate]:
#     """Fetches trending AI research preprints ranked by community upvotes via Hugging Face Daily Papers."""
#     candidates = []
#     # GET /api/daily_papers returns curated arXiv papers ranked by community upvotes
#     url = "https://huggingface.co/api/daily_papers?limit=25"
#     try:
#         resp = await client.get(url, timeout=settings.http_timeout_seconds)
#         if resp.status_code == 200:
#             data = resp.json()
#             items = data.get("results", data) if isinstance(data, dict) else data
#             for item in items:
#                 paper = item.get("paper", item)
#                 arxiv_id = paper.get("id") or paper.get("arxivId")
#                 title = paper.get("title", "").replace("\n", " ").strip()
#                 summary = paper.get("summary", "").replace("\n", " ").strip()
#                 upvotes = float(paper.get("upvotes", 0))

#                 # Parse publishedAt timestamp (ISO format) if available
#                 pub_raw = paper.get("publishedAt")
#                 if pub_raw:
#                     try:
#                         pub_time = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
#                     except Exception:
#                         pub_time = datetime.now(timezone.utc)
#                 else:
#                     pub_time = datetime.now(timezone.utc)

#                 if arxiv_id and title:
#                     candidates.append(
#                         RawCandidate(
#                             title=title,
#                             url=f"https://arxiv.org/abs/{arxiv_id}",
#                             source="ArXiv (via Hugging Face)",
#                             published_at=pub_time,
#                             raw_text=summary or title,
#                             score=upvotes,
#                         )
#                     )
#     except Exception as e:
#         print(f"[Ingest] Hugging Face Daily Papers warning: {e}")
#     return candidates


# # async def fetch_hacker_news(client: httpx.AsyncClient) -> list[RawCandidate]:
# #     candidates = []
# #     # Query AI/LLM posts from the past 7 days with engagement >= 10 points
# #     lookback_secs = settings.lookback_days * 86400
# #     cutoff = int(datetime.now(timezone.utc).timestamp()) - lookback_secs
# #     url = f"https://hn.algolia.com/api/v1/search?query=AI%20OR%20LLM&numericFilters=created_at_i>{cutoff},points>10&hitsPerPage=30"
# #     try:
# #         resp = await client.get(url, timeout=settings.http_timeout_seconds)
# #         if resp.status_code == 200:
# #             hits = resp.json().get("hits", [])
# #             for hit in hits:
# #                 story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
# #                 created_ts = hit.get("created_at_i", int(datetime.now(timezone.utc).timestamp()))
# #                 pub_time = datetime.fromtimestamp(created_ts, tz=timezone.utc)
# #                 candidates.append(
# #                     RawCandidate(
# #                         title=hit.get("title", ""),
# #                         url=story_url,
# #                         source="Hacker News",
# #                         published_at=pub_time,
# #                         raw_text=hit.get("title", ""),
# #                         score=float(hit.get("points", 0)),
# #                     )
# #                 )
# #     except Exception as e:
# #         print(f"[Ingest] Hacker News Algolia fetch warning: {e}")
# #     return candidates
# async def fetch_hacker_news(client: httpx.AsyncClient) -> list[RawCandidate]:
#     candidates = []
#     # Broaden query to catch AI/ML tools and preprints with engagement >= 5
#     lookback_secs = settings.lookback_days * 86400
#     cutoff = int(datetime.now(timezone.utc).timestamp()) - lookback_secs
#     query = "AI OR LLM OR model OR architecture OR open-source"
#     url = f"https://hn.algolia.com/api/v1/search_by_date?query={urllib.parse.quote(query)}&tags=story&numericFilters=created_at_i>{cutoff},points>5&hitsPerPage=20"
#     try:
#         resp = await client.get(url, timeout=settings.http_timeout_seconds)
#         if resp.status_code == 200:
#             hits = resp.json().get("hits", [])
#             for hit in hits:
#                 story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
#                 created_ts = hit.get("created_at_i", int(datetime.now(timezone.utc).timestamp()))
#                 pub_time = datetime.fromtimestamp(created_ts, tz=timezone.utc)
#                 candidates.append(
#                     RawCandidate(
#                         title=hit.get("title", ""),
#                         url=story_url,
#                         source="Hacker News",
#                         published_at=pub_time,
#                         raw_text=hit.get("title", ""),
#                         score=float(hit.get("points", 0)),
#                     )
#                 )
#     except Exception as e:
#         print(f"[Ingest] Hacker News Algolia fetch warning: {e}")
#     return candidates

# async def ingest_all() -> list[RawCandidate]:
#     headers = {"User-Agent": f"{settings.app_title}/1.0"}
#     async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
#         results = await asyncio.gather(
#             fetch_techmeme(client),
#             fetch_hf_daily_papers(client),
#             fetch_hacker_news(client),
#             return_exceptions=False,
#         )
#     return [item for sublist in results for item in sublist]