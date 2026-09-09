import re
from src.schemas import TrackOutput

STOPWORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "with", "of",
    "by", "from", "is", "are", "was", "were", "new", "ai", "llm", "using", "via"
}


def tokenize_title(title: str) -> set[str]:
    words = re.findall(r"\b[a-zA-Z0-9_\-\.]{3,}\b", title.lower())
    return {w for w in words if w not in STOPWORDS}


def calculate_jaccard_convergence(track_a: TrackOutput, track_b: TrackOutput) -> dict:
    tokens_a = [tokenize_title(s.title) for s in track_a.stories]
    tokens_b = [tokenize_title(s.title) for s in track_b.stories]

    matched_pairs = []
    # look for an overlap threshold >= 0.33 indicating identical impactful-event coverage
    for idx_a, set_a in enumerate(tokens_a):
        for idx_b, set_b in enumerate(tokens_b):
            intersection = set_a.intersection(set_b)
            union = set_a.union(set_b)
            similarity = len(intersection) / len(union) if union else 0.0
            if similarity >= 0.33:
                matched_pairs.append({
                    "track_a_title": track_a.stories[idx_a].title,
                    "track_b_title": track_b.stories[idx_b].title,
                    "similarity": round(similarity, 3),
                })

    convergence_pct = (len(matched_pairs) / 5.0) * 100.0 if track_a.stories else 0.0

    return {
        "matched_story_count": len(matched_pairs),
        "convergence_percentage": round(convergence_pct, 1),
        "matches": matched_pairs,
    }


def build_telemetry_scorecard(
    telemetry_a: dict,
    telemetry_b: dict,
    convergence: dict,
    link_results: dict,
) -> dict:
    healthy_links = sum(1 for v in link_results.values() if v.get("healthy"))
    total_links = len(link_results)

    return {
        "track_a": telemetry_a,
        "track_b": telemetry_b,
        "convergence": convergence,
        "link_health": {
            "healthy": healthy_links,
            "total": total_links,
            "pass_rate": round((healthy_links / total_links * 100) if total_links else 0.0, 1),
        },
        "total_experiment_tokens": (
            telemetry_a.get("total_tokens", 0) + telemetry_b.get("total_tokens", 0)
        ),
        "total_experiment_wall_time": round(
            telemetry_a.get("wall_time_seconds", 0) + telemetry_b.get("wall_time_seconds", 0), 2
        ),
    }
