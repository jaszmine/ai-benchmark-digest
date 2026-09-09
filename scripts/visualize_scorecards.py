import re
from pathlib import Path
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
import numpy as np


def clean_int(val_str: str) -> int:
    digits = re.sub(r"[^\d]", "", val_str)
    return int(digits) if digits else 0


def clean_float(val_str: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)", val_str)
    return float(match.group(1)) if match else 0.0


def extract_scorecard_from_html(html_path: Path) -> dict | None:
    content = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(content, "html.parser")
    table = soup.find("table", class_="table-bench")

    if not table:
        return None

    raw_metrics = {}
    rows = table.find_all("tr")
    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) == 3:
            metric_label = cells[0].get_text(strip=True).lower()
            val_a = cells[1].get_text(strip=True)
            val_b = cells[2].get_text(strip=True)
            raw_metrics[metric_label] = (val_a, val_b)

    # Map parsed HTML table values into structured telemetry numbers
    try:
        lat_a, lat_b = raw_metrics.get("wall clock latency", ("0", "0"))
        pt_a, pt_b = raw_metrics.get("input / prompt tokens", ("0", "0"))
        rt_a, rt_b = raw_metrics.get("reasoning (cot) tokens", ("0", "0"))
        ct_a, ct_b = raw_metrics.get("completion tokens", ("0", "0"))
        tot_a, tot_b = raw_metrics.get("total tokens consumed", ("0", "0"))

        # Extract timestamp from filename pattern digest_preview_YYYYMMDD_HHMMSS.html
        ts_match = re.search(r"(\d{8}_\d{6})", html_path.stem)
        label = ts_match.group(1)[-6:] if ts_match else html_path.stem[:10]

        return {
            "label": label,
            "filename": html_path.name,
            "track_a": {
                "wall_time": clean_float(lat_a),
                "prompt": clean_int(pt_a),
                "reasoning": clean_int(rt_a),
                "completion": clean_int(ct_a),
                "total": clean_int(tot_a),
            },
            "track_b": {
                "wall_time": clean_float(lat_b),
                "prompt": clean_int(pt_b),
                "reasoning": clean_int(rt_b),
                "completion": clean_int(ct_b),
                "total": clean_int(tot_b),
            },
        }
    except Exception as e:
        print(f"Error parsing metrics from {html_path.name}: {e}")
        return None


def plot_html_artifacts():
    artifacts_dir = Path(__file__).resolve().parent.parent / "artifacts"
    html_files = sorted(artifacts_dir.glob("digest_preview_*.html"))

    if not html_files:
        # Fallback to general search in artifacts/
        html_files = sorted(artifacts_dir.glob("*.html"))

    runs = []
    for hf in html_files:
        parsed = extract_scorecard_from_html(hf)
        if parsed:
            runs.append(parsed)

    if not runs:
        print(f"No valid scorecard tables found in {artifacts_dir}/*.html")
        return

    print(f"Loaded {len(runs)} historical run(s) from HTML snapshots:")
    for r in runs:
        print(f" - {r['filename']} (Label: {r['label']})")

    labels = [f"Run {i+1}\n({r['label']})" for i, r in enumerate(runs)]
    x = np.arange(len(labels))
    width = 0.35

    # Series values
    a_prompt = [r["track_a"]["prompt"] for r in runs]
    a_reason = [r["track_a"]["reasoning"] for r in runs]
    a_comp = [r["track_a"]["completion"] for r in runs]

    b_prompt = [r["track_b"]["prompt"] for r in runs]
    b_reason = [r["track_b"]["reasoning"] for r in runs]
    b_comp = [r["track_b"]["completion"] for r in runs]

    a_lat = [r["track_a"]["wall_time"] for r in runs]
    b_lat = [r["track_b"]["wall_time"] for r in runs]

    # Render Dark-Theme Comparison Chart
    plt.style.use("dark_background")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    fig.patch.set_facecolor("#0d1117")
    ax1.set_facecolor("#161b22")
    ax2.set_facecolor("#161b22")

    # 1. Stacked Token Breakdown
    ax1.bar(x - width / 2, a_prompt, width, label="A: Prompt", color="#1f6feb")
    ax1.bar(x - width / 2, a_reason, width, bottom=a_prompt, label="A: Reasoning", color="#388bfd")
    ax1.bar(
        x - width / 2,
        a_comp,
        width,
        bottom=np.array(a_prompt) + np.array(a_reason),
        label="A: Completion",
        color="#79c0ff",
    )

    ax1.bar(x + width / 2, b_prompt, width, label="B: Prompt", color="#8957e5")
    ax1.bar(x + width / 2, b_reason, width, bottom=b_prompt, label="B: Reasoning", color="#ab7df8")
    ax1.bar(
        x + width / 2,
        b_comp,
        width,
        bottom=np.array(b_prompt) + np.array(b_reason),
        label="B: Completion",
        color="#d2a8ff",
    )

    ax1.set_title("Token Breakdown (From HTML Snapshots)", fontsize=13, color="#f0f6fc", pad=12)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, color="#c9d1d9", fontsize=9)
    ax1.set_ylabel("Tokens Consumed", color="#8b949e")
    ax1.grid(axis="y", linestyle="--", alpha=0.15)
    ax1.legend(loc="upper left", fontsize=8, framealpha=0.3)

    # 2. Wall Clock Execution Latencies
    bars_a = ax2.bar(x - width / 2, a_lat, width, label="Track A (Deterministic)", color="#58a6ff")
    bars_b = ax2.bar(x + width / 2, b_lat, width, label="Track B (Agent)", color="#bc8cff")

    ax2.bar_label(bars_a, fmt="%.1fs", padding=3, color="#58a6ff", fontsize=8)
    ax2.bar_label(bars_b, fmt="%.1fs", padding=3, color="#bc8cff", fontsize=8)

    ax2.set_title("Wall-Clock Latency (Seconds)", fontsize=13, color="#f0f6fc", pad=12)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, color="#c9d1d9", fontsize=9)
    ax2.set_ylabel("Seconds", color="#8b949e")
    ax2.grid(axis="y", linestyle="--", alpha=0.15)
    ax2.legend(loc="upper left", fontsize=8, framealpha=0.3)

    plt.tight_layout()
    output_path = artifacts_dir / "scorecard_html_trends.png"
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    print(f"\n[Success] Telemetry trend figure compiled and saved to: {output_path}")
    plt.show()


if __name__ == "__main__":
    plot_html_artifacts()
