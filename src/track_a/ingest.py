import asyncio
from datetime import datetime, timezone
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
                    entry.get("published_parsed") and datetime(*entry.published_parsed[:6]).timestamp()
                    or datetime.now(timezone.utc).timestamp(),
                    tz=timezone.utc,
                )
                candidates.append(
                    RawCandidate(
                        title=entry.get("title", ""),
                        url=entry.get("link", ""),
                        source="Techmeme",
                        published_at=pub_time,
                        raw_text=entry.get("summary", "") or entry.get("title", ""),
                    )
                )
    except Exception as e:
        print(f"[Ingest] Techmeme fetch warning: {e}")
    return candidates


async def fetch_arxiv(client: httpx.AsyncClient) -> list[RawCandidate]:
    candidates = []
    # Fetch recent AI/ML papers (cs.AI + cs.CL + cs.LG)
    url = "https://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL&sortBy=submittedDate&sortOrder=descending&max_results=25"
    try:
        resp = await client.get(url, timeout=settings.http_timeout_seconds)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.text)
            for entry in feed.entries:
                pub_time = datetime.fromtimestamp(
                    entry.get("published_parsed") and datetime(*entry.published_parsed[:6]).timestamp()
                    or datetime.now(timezone.utc).timestamp(),
                    tz=timezone.utc,
                )
                candidates.append(
                    RawCandidate(
                        title=entry.get("title", "").replace("\n", " ").strip(),
                        url=entry.get("link", ""),
                        source="ArXiv",
                        published_at=pub_time,
                        raw_text=entry.get("summary", "").replace("\n", " ").strip(),
                    )
                )
    except Exception as e:
        print(f"[Ingest] ArXiv fetch warning: {e}")
    return candidates


async def fetch_hacker_news(client: httpx.AsyncClient) -> list[RawCandidate]:
    candidates = []
    # Query AI/LLM posts from the past 7 days with engagement >= 10 points
    lookback_secs = settings.lookback_days * 86400
    cutoff = int(datetime.now(timezone.utc).timestamp()) - lookback_secs
    url = f"https://hn.algolia.com/api/v1/search?query=AI%20OR%20LLM&numericFilters=created_at_i>{cutoff},points>10&hitsPerPage=30"
    try:
        resp = await client.get(url, timeout=settings.http_timeout_seconds)
        if resp.status_code == 200:
            hits = resp.json().get("hits", [])
            for hit in hits:
                story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                created_ts = hit.get("created_at_i", int(datetime.now(timezone.utc).timestamp()))
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
            fetch_arxiv(client),
            fetch_hacker_news(client),
            return_exceptions=False,
        )
    return [item for sublist in results for item in sublist]
