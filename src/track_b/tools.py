from datetime import datetime
from langchain_core.tools import tool
from tavily import TavilyClient
from src.config import get_settings

settings = get_settings()


def get_tavily_client() -> TavilyClient:
    return TavilyClient(api_key=settings.tavily_api_key)


def format_date(raw_date: str | None) -> str:
    if not raw_date:
        return "Recent"
    try:
        # Parse ISO or standard date strings from Tavily
        dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        return dt.strftime("%b. %d, %Y")
    except Exception:
        return raw_date[:12]


@tool
def lean_web_search(query: str) -> str:
    """Search the web for major recent AI developments, breakthroughs, papers, or models.
    Prioritizes the past 7 days, falling back to the past 30 days.
    Returns concise, sanitized snippets with titles, dates, and URLs.
    """
    client = get_tavily_client()
    results = []

    # Attempt 1: 7-day window
    try:
        response = client.search(
            query=query,
            search_depth="basic",
            topic="news",
            days=7,
            max_results=3,
            include_answer=False,
            include_raw_content=False,
        )
        results = response.get("results", [])
    except Exception:
        results = []

    # Attempt 2: 30-day window
    if not results:
        try:
            response = client.search(
                query=query,
                search_depth="basic",
                topic="news",
                days=30,
                max_results=3,
                include_answer=False,
                include_raw_content=False,
            )
            results = response.get("results", [])
        except Exception as e:
            return f"Search engine temporarily unavailable: {str(e)[:80]}"

    if not results:
        return "No relevant results found from the past month."

    formatted_snippets = []
    for r in results[:3]:
        raw_snippet = r.get("content", "").replace("\n", " ").strip()
        sliced_snippet = raw_snippet[:250]
        title = r.get("title", "Untitled").strip()
        url = r.get("url", "").strip()
        pub_date = format_date(r.get("published_date"))
        formatted_snippets.append(
            f"- Original Title: {title}\n  Published Date: {pub_date}\n  URL: {url}\n  Excerpt: {sliced_snippet}"
        )

    return "\n\n".join(formatted_snippets)
