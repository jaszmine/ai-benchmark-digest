import asyncio
import httpx

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


async def check_single_url(client: httpx.AsyncClient, url: str) -> dict:
    if not url.startswith("http"):
        return {
            "url": url,
            "status": 0,
            "healthy": False,
            "is_protected": False,
            "error": "Invalid scheme",
        }

    try:
        # Attempt lightweight HEAD first
        resp = await client.head(url, timeout=7.0, follow_redirects=True)

        # Fall back to GET if server refuses HEAD or issues anti-bot status on HEAD
        if resp.status_code in (405, 403, 401, 501):
            resp = await client.get(
                url,
                timeout=7.0,
                follow_redirects=True,
            )

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

    except (
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
        httpx.ConnectTimeout,
    ) as e:
        # Edge CDN or wire service closed TCP connection on bot detection -> Protected, not dead
        return {
            "url": url,
            "status": 403,
            "healthy": True,
            "is_protected": True,
            "error": f"Bot mitigation / TLS handshake drop: {type(e).__name__}",
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
    # Deduplicate candidate URLs while preserving order
    unique_urls = list(dict.fromkeys(urls))

    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS,
        verify=False,
        follow_redirects=True,
    ) as client:
        tasks = [check_single_url(client, u) for u in unique_urls]
        results = await asyncio.gather(*tasks)

    return {r["url"]: r for r in results}

# import asyncio
# import httpx

# BROWSER_HEADERS = {
#     "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
#     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
#     "Accept-Language": "en-US,en;q=0.9",
#     "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
#     "Sec-Ch-Ua-Mobile": "?0",
#     "Sec-Ch-Ua-Platform": '"macOS"',
#     "Sec-Fetch-Dest": "document",
#     "Sec-Fetch-Mode": "navigate",
#     "Sec-Fetch-Site": "none",
# }

# async def check_single_url(client: httpx.AsyncClient, url: str) -> dict:
#     if not url.startswith("http"):
#         return {
#             "url": url,
#             "status": 0,
#             "healthy": False,
#             "is_protected": False,
#             "error": "Invalid scheme",
#         }

#     try:
#         # Attempt lightweight HEAD first
#         resp = await client.head(url, timeout=7.0, follow_redirects=True)

#         # Fall back to ranged GET if server refuses HEAD or issues anti-bot status on HEAD
#         if resp.status_code in (405, 403, 401, 501):
#             resp = await client.get(
#                 url,
#                 headers={"Range": "bytes=0-2048"},
#                 timeout=7.0,
#                 follow_redirects=True,
#             )

#         status = resp.status_code
#         # 2xx/3xx are healthy; 401/403 indicate an existing, live host with anti-bot/paywall protection
#         is_protected = status in (401, 403)
#         healthy = (200 <= status < 400) or is_protected

#         return {
#             "url": url,
#             "status": status,
#             "healthy": healthy,
#             "is_protected": is_protected,
#             "error": None,
#         }
#     except Exception as e:
#         return {
#             "url": url,
#             "status": 0,
#             "healthy": False,
#             "is_protected": False,
#             "error": str(e)[:60],
#         }


# async def verify_links_batch(urls: list[str]) -> dict[str, dict]:
#     # Deduplicate candidate URLs while preserving order
#     unique_urls = list(dict.fromkeys(urls))

#     async with httpx.AsyncClient(
#         headers=BROWSER_HEADERS,
#         verify=False,
#         follow_redirects=True,
#     ) as client:
#         tasks = [check_single_url(client, u) for u in unique_urls]
#         results = await asyncio.gather(*tasks)

#     return {r["url"]: r for r in results}
