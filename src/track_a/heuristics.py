import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse
from src.schemas import RawCandidate

TECH_KEYWORDS = {
    "llm": 3.0, "weights": 2.5, "benchmark": 2.5, "transformer": 3.0,
    "quantization": 3.0, "vllm": 3.5, "inference": 2.5, "fine-tuning": 2.5,
    "dataset": 2.0, "diffusion": 2.5, "agent": 2.0, "reasoning": 2.5,
    "architecture": 2.0, "gpu": 2.0, "cuda": 2.5, "open-weights": 3.5,
}

EXCLUDE_PATTERNS = [
    r"^ask hn:",
    r"^tell hn:",
    r"\bwho is hiring\b",
    r"\bhiring\b",
    r"\bcareer\b",
    r"\bsalary\b",
    r"\blawoff\b",
    r"\blawsuit\b",
]


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def filter_and_rank(candidates: list[RawCandidate], lookback_days: int = 7) -> list[RawCandidate]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    seen_urls = set()
    cleaned = []

    for c in candidates:
        if c.published_at < cutoff:
            continue

        title_lower = c.title.lower()
        if any(re.search(pat, title_lower) for pat in EXCLUDE_PATTERNS):
            continue

        canon_url = normalize_url(c.url)
        if canon_url in seen_urls or not canon_url:
            continue
        seen_urls.add(canon_url)

        # Base technical keyword scoring
        score = c.score * 0.1  # hackernews engagement points (weight)
        combined_text = f"{c.title} {c.raw_text}".lower()
        for kw, weight in TECH_KEYWORDS.items():
            if kw in combined_text:
                score += weight

        if c.source == "ArXiv":
            score += 2.0  # Technical baseline for preprints

        c.score = score
        cleaned.append(c)

    cleaned.sort(key=lambda x: x.score, reverse=True)
    return cleaned
