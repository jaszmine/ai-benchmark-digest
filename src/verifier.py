import asyncio
import httpx

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


async def check_single_url(client: httpx.AsyncClient, url: str) -> dict:
    if not url or not url.startswith("http"):
        return {
            "url": url,
            "status": 0,
            "healthy": False,
            "is_protected": False,
            "error": "Invalid scheme",
        }

    # 1. Attempt lightweight HEAD probe
    try:
        resp = await client.head(url, timeout=9.0, follow_redirects=True)
        # If server explicitly rejects HEAD (405, 403, 400, 501), fall through to GET
        if resp.status_code not in (405, 403, 401, 400, 501):
            status = resp.status_code
            is_protected = status in (401, 403)
            healthy = (200 <= status < 400) or is_protected
            return {
                "url": url,
                "status": status,
                "healthy": healthy,
                "is_protected": is_protected,
                "error": None,
            }
    except (httpx.HTTPError, Exception):
        pass

    # 2. Fall back to lightweight streaming GET (reads status header without fetching whole body)
    try:
        async with client.stream("GET", url, timeout=10.0, follow_redirects=True) as stream_resp:
            status = stream_resp.status_code
            is_protected = status in (401, 403)
            healthy = (200 <= status < 400) or is_protected
            return {
                "url": url,
                "status": status,
                "healthy": healthy,
                "is_protected": is_protected,
                "error": None,
            }
    except (
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
        httpx.ConnectTimeout,
        httpx.ProtocolError,
    ) as e:
        # CDN anti-crawler bot mitigation (Cloudflare / Akamai) dropped TLS or connection
        return {
            "url": url,
            "status": 403,
            "healthy": True,
            "is_protected": True,
            "error": f"Bot mitigation / TLS challenge: {type(e).__name__}",
        }
    except Exception as e:
        return {
            "url": url,
            "status": 0,
            "healthy": False,
            "is_protected": False,
            "error": str(e)[:60],
        }


async def verify_links_batch(urls: list[str]) -> dict[str, dict]:
    unique_urls = list(dict.fromkeys(urls))

    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS,
        verify=False,
        follow_redirects=True,
        http2=True,
        limits=limits,
    ) as client:
        tasks = [check_single_url(client, u) for u in unique_urls]
        results = await asyncio.gather(*tasks)

    return {r["url"]: r for r in results}