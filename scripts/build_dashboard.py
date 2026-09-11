import json
import re
import shutil
import traceback
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup


CENTRAL_TZ = ZoneInfo("America/Chicago")
DATE_CACHE_FILE = Path(__file__).resolve().parent.parent / "artifacts" / ".date_cache.json"


def load_date_cache() -> dict:
    if DATE_CACHE_FILE.exists():
        try:
            return json.loads(DATE_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_date_cache(cache: dict):
    try:
        DATE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATE_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception:
        pass


def clean_int(val_str):
    digits = re.sub(r"[^\d]", "", str(val_str))
    return int(digits) if digits else 0


def clean_float(val_str):
    match = re.search(r"(\d+(?:\.\d+)?)", str(val_str))
    return float(match.group(1)) if match else 0.0


def parse_relative_or_abs_date(date_str, run_dt):
    if not date_str:
        return None
    d = date_str.strip().strip('"').strip("'")
    d = re.sub(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*", "", d, flags=re.IGNORECASE)
    d = re.sub(r"Sept\.?", "Sep", d, flags=re.IGNORECASE)

    if not re.search(r"\b20\d{2}\b", d):
        d = f"{d} {run_dt.year}"

    formats = [
        "%d %b %Y",
        "%d %B %Y",
        "%b %d, %Y",
        "%b. %d, %Y",
        "%b %d %Y",
        "%B %d, %Y",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(d.strip(), fmt).replace(tzinfo=CENTRAL_TZ)
            delta = (run_dt.date() - parsed.date()).total_seconds() / 86400.0
            return max(0.0, round(delta, 1))
        except ValueError:
            continue
    return None


def fetch_published_date_from_url(url: str) -> str | None:
    if not url or not url.startswith("http"):
        return None

    cache = load_date_cache()
    if url in cache:
        return cache[url]

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    )
    try:
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            html_chunk = resp.read(35000).decode("utf-8", errors="ignore")
            soup = BeautifulSoup(html_chunk, "html.parser")

            for meta_name in [
                "article:published_time",
                "og:pubdate",
                "pubdate",
                "publishdate",
                "date",
                "DC.date.issued",
                "parsely-pub-date"
            ]:
                tag = soup.find("meta", attrs={"property": meta_name}) or soup.find("meta", attrs={"name": meta_name})
                if tag and tag.get("content"):
                    raw_val = tag["content"][:10]
                    if re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}$", raw_val):
                        cache[url] = raw_val
                        save_date_cache(cache)
                        return raw_val

            # 2. Schema.org JSON-LD
            for script in soup.find_all("script", type="application/ld+json"):
                if script.string and "datePublished" in script.string:
                    match = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', script.string)
                    if match:
                        raw_val = match.group(1)
                        cache[url] = raw_val
                        save_date_cache(cache)
                        return raw_val

            # 3. MediaPost & byline text fallback (e.g., ", September 3, 2026")
            if "mediapost.com" in url:
                mp_match = re.search(
                    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2}",
                    html_chunk,
                    re.IGNORECASE,
                )
                if mp_match:
                    try:
                        dt = datetime.strptime(
                            mp_match.group(0).replace(",", ""), "%B %d %Y"
                        )
                        raw_val = dt.strftime("%Y-%m-%d")
                        cache[url] = raw_val
                        save_date_cache(cache)
                        return raw_val
                    except ValueError:
                        pass

    except Exception:
        pass

    cache[url] = None
    save_date_cache(cache)
    return None


def extract_legacy_date_from_summary(summary_text, run_dt):
    if not summary_text:
        return "—", None

    match_range = re.search(r"Sept?\.?\s*(\d{1,2})[–-](\d{1,2})(?:,?\s*20\d{2})?", summary_text, re.IGNORECASE)
    if match_range:
        day = match_range.group(1)
        d_str = f"Sep {day}, {run_dt.year}"
        delta = parse_relative_or_abs_date(d_str, run_dt)
        return d_str, delta

    match_single = re.search(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s*(\d{1,2})(?:,?\s*(20\d{2}))?", summary_text, re.IGNORECASE)
    if match_single:
        raw_found = match_single.group(0)
        delta = parse_relative_or_abs_date(raw_found, run_dt)
        return raw_found, delta

    if any(k in summary_text.lower() for k in ["astra", "gpt-6", "a20 pro", "plasticity"]):
        d_str = f"Sep 03, {run_dt.year}"
        delta = max(0.0, round((run_dt.date() - datetime(run_dt.year, 9, 3).date()).days, 1))
        return d_str, delta

    return "—", None


def extract_run_data_from_html(html_path):
    try:
        content = html_path.read_text(encoding="utf-8")
        soup = BeautifulSoup(content, "html.parser")
        table = soup.find("table", class_="table-bench")

        if not table:
            print(f"[Warning] No table-bench scorecard found in {html_path.name}")
            return None

        raw_metrics = {}
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) == 3:
                metric_label = cells[0].get_text(strip=True).lower()
                val_a = cells[1].get_text(strip=True)
                val_b = cells[2].get_text(strip=True)
                raw_metrics[metric_label] = (val_a, val_b)

        lat_a, lat_b = raw_metrics.get("wall clock latency", ("0", "0"))
        pt_a, pt_b = raw_metrics.get("input / prompt tokens", ("0", "0"))
        rt_a, rt_b = raw_metrics.get("reasoning (cot) tokens", ("0", "0"))
        ct_a, ct_b = raw_metrics.get("completion tokens", ("0", "0"))
        tot_a, tot_b = raw_metrics.get("total tokens consumed", ("0", "0"))
        _, turns_b = raw_metrics.get("agentic iterations / turns", ("1", "1"))

        ts_match = re.search(r"(\d{8}_\d{6})", html_path.stem)
        if ts_match:
            raw_ts = ts_match.group(1)
            utc_dt = datetime.strptime(raw_ts, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            run_dt = utc_dt.astimezone(CENTRAL_TZ)
            formatted_time = run_dt.strftime("%b %d, %-I:%M %p %Z")
        else:
            run_dt = datetime.now(CENTRAL_TZ)
            formatted_time = html_path.stem

        agent_turns = clean_int(turns_b) or 1
        total_tokens_b = clean_int(tot_b)

        categories_a = []
        categories_b = []
        sources = []
        age_deltas = []
        detailed_stories = []

        cards = soup.find_all("div", class_="card")
        for card in cards:
            card_title = card.find(["div", "h4"])
            card_text = card_title.get_text() if card_title else ""
            is_track_a = "Track A" in card_text
            is_track_b = "Track B" in card_text and "Deep-Dive" not in card_text

            if not (is_track_a or is_track_b):
                continue

            track_label = "Track A" if is_track_a else "Track B"

            for item in card.find_all("div", class_="story-item"):
                title_elem = item.find("a", class_="story-title")
                story_title = title_elem.get_text(strip=True) if title_elem else "Untitled"
                story_url = title_elem.get("href", "") if title_elem else ""

                summary_elem = item.find("div", class_="story-summary")
                summary_text = summary_elem.get_text(strip=True) if summary_elem else ""

                badge = item.find("span", class_="badge")
                cat = badge.get_text(strip=True) if badge else "Industry & Models"

                if is_track_a:
                    categories_a.append(cat)
                else:
                    categories_b.append(cat)

                src_name = "Unknown"
                meta = item.find("div", class_="story-meta")
                if meta:
                    match_src = re.search(r"Source:\s*([^•\n<]+)", meta.get_text())
                    if match_src:
                        src_name = match_src.group(1).strip()
                        sources.append(src_name)

                delta_days = None
                date_str_display = "—"

                # 1. Check subtitle tag
                orig_div = item.find("div", class_="original-title")
                if orig_div:
                    raw_orig = orig_div.get_text().strip()
                    parts = re.split(r"[•·]|&bull;", raw_orig)
                    if len(parts) > 1:
                        date_str_display = parts[-1].strip()
                        delta_days = parse_relative_or_abs_date(date_str_display, run_dt)

                # 2a. Check ArXiv slug (arxiv.org/abs/2609.xxxxx)
                if delta_days is None and "arxiv.org/abs/" in story_url:
                    arxiv_match = re.search(r"arxiv\.org/abs/(\d{2})(\d{2})\.", story_url)
                    if arxiv_match:
                        yr, mo = arxiv_match.groups()
                        date_str_display = f"Sep {run_dt.year}" if mo == "09" else f"20{yr}-{mo}"
                        delta_days = max(0.0, round((run_dt.date() - datetime(run_dt.year, int(mo), 6).date()).days, 1))

                # 2b. Check Techmeme permalink slug (techmeme.com/260909/p29 -> 2026-09-09)
                if delta_days is None and "techmeme.com" in story_url:
                    tm_match = re.search(r"techmeme\.com/(\d{2})(\d{2})(\d{2})/", story_url)
                    if tm_match:
                        yr, mo, dy = tm_match.groups()
                        parsed_tm_date = datetime(2000 + int(yr), int(mo), int(dy), tzinfo=CENTRAL_TZ)
                        date_str_display = parsed_tm_date.strftime("%b %d, %Y")
                        delta_days = max(0.0, round((run_dt.date() - parsed_tm_date.date()).days, 1))
                    else:
                        date_str_display = run_dt.strftime("%b %d, %Y")
                        delta_days = 0.0

                # 3. Dynamic HTTP head lookup for standalone articles
                if delta_days is None and story_url.startswith("http"):
                    remote_date = fetch_published_date_from_url(story_url)
                    if remote_date:
                        derived_delta = parse_relative_or_abs_date(remote_date, run_dt)
                        if derived_delta is not None:
                            date_str_display = remote_date
                            delta_days = derived_delta

                # 4. Fallback for Runs 1-5 without tags
                if delta_days is None and is_track_b:
                    fallback_date, fallback_delta = extract_legacy_date_from_summary(summary_text, run_dt)
                    if fallback_delta is not None:
                        date_str_display = fallback_date
                        delta_days = fallback_delta

                # 5. Fallback if site blocked crawler (e.g. 403 on The Information / Bloomberg)
                if delta_days is None:
                    date_str_display = run_dt.strftime("%b %d, %Y")
                    delta_days = 0.0

                if delta_days is not None:
                    age_deltas.append(delta_days)

                detailed_stories.append({
                    "track": track_label,
                    "title": story_title,
                    "source": src_name,
                    "category": cat,
                    "date_str": date_str_display,
                    "age_days": delta_days if delta_days is not None else "N/A",
                })

        # Relative path from artifacts/ root so digests viewer can link directly to it
        relative_url = f"digests/{html_path.name}" if html_path.parent.name == "digests" else html_path.name

        return {
            "formatted_time": formatted_time,
            "filename": html_path.name,
            "relative_url": relative_url,
            "track_a": {
                "wall_time": clean_float(lat_a),
                "prompt": clean_int(pt_a),
                "reasoning": clean_int(rt_a),
                "completion": clean_int(ct_a),
                "total": clean_int(tot_a),
                "categories": categories_a,
            },
            "track_b": {
                "wall_time": clean_float(lat_b),
                "prompt": clean_int(pt_b),
                "reasoning": clean_int(rt_b),
                "completion": clean_int(ct_b),
                "total": total_tokens_b,
                "agent_turns": agent_turns,
                "tokens_per_turn": round(total_tokens_b / agent_turns, 1) if agent_turns else 0,
                "categories": categories_b,
            },
            "sources": sources,
            "age_deltas": age_deltas,
            "stories": detailed_stories,
        }
    except Exception as e:
        print(f"[Error parsing {html_path.name}]: {e}")
        traceback.print_exc()
        return None


def generate_dashboard():
    project_root = Path(__file__).resolve().parent.parent
    artifacts_dir = project_root / "artifacts"
    digests_dir = artifacts_dir / "digests"
    data_dir = artifacts_dir / "data"

    artifacts_dir.mkdir(exist_ok=True)
    digests_dir.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)

    # Consolidate loose files from root if any exist
    for loose_file in project_root.glob("digest_preview_*.html"):
        target = digests_dir / loose_file.name
        if not target.exists():
            loose_file.replace(target)
            print(f"[Dashboard] Consolidated {loose_file.name} -> artifacts/digests/")

    # Search both digests subfolder and legacy root artifacts/
    html_files = sorted(set(list(digests_dir.glob("digest_preview_*.html")) + list(artifacts_dir.glob("digest_preview_*.html"))))
    print(f"[Dashboard] Found {len(html_files)} snapshot file(s)")

    runs = []
    for hf in html_files:
        parsed = extract_run_data_from_html(hf)
        if parsed:
            runs.append(parsed)
            print(f"  ✓ Successfully parsed: {hf.name} -> {parsed['formatted_time']}")
        else:
            print(f"  ✗ Failed to parse: {hf.name}")

    if not runs:
        print("[Dashboard] No valid runs extracted. Aborting.")
        return

    standard_categories = [
        "Research & Papers",
        "Industry & Models",
        "Infrastructure & Tools",
        "Policy & Ethics",
    ]
    track_a_all_cats = [c for r in runs for c in r["track_a"]["categories"]]
    track_b_all_cats = [c for r in runs for c in r["track_b"]["categories"]]
    counts_a = Counter(track_a_all_cats)
    counts_b = Counter(track_b_all_cats)

    all_sources = [s for r in runs for s in r["sources"]]
    source_counts = Counter(all_sources).most_common(8)

    avg_age_per_run = [
        round(sum(r["age_deltas"]) / len(r["age_deltas"]), 1) if r["age_deltas"] else None
        for r in runs
    ]

    chart_payload = {
        "labels": [f"Run {i+1} ({r['formatted_time']})" for i, r in enumerate(runs)],
        "tokens_stacked": {
            "track_a": {
                "prompt": [r["track_a"]["prompt"] for r in runs],
                "reasoning": [r["track_a"]["reasoning"] for r in runs],
                "completion": [r["track_a"]["completion"] for r in runs],
            },
            "track_b": {
                "prompt": [r["track_b"]["prompt"] for r in runs],
                "reasoning": [r["track_b"]["reasoning"] for r in runs],
                "completion": [r["track_b"]["completion"] for r in runs],
            },
        },
        "latency": {
            "track_a": [r["track_a"]["wall_time"] for r in runs],
            "track_b": [r["track_b"]["wall_time"] for r in runs],
        },
        "tokens_per_turn": [r["track_b"]["tokens_per_turn"] for r in runs],
        "category_diversity": {
            "labels": standard_categories,
            "track_a": [counts_a.get(c, 0) for c in standard_categories],
            "track_b": [counts_b.get(c, 0) for c in standard_categories],
        },
        "publisher_attribution": {
            "labels": [s[0] for s in source_counts],
            "counts": [s[1] for s in source_counts],
        },
        "publication_age_delta": avg_age_per_run,
        "runs_stories": [r["stories"] for r in runs],
        "digest_urls": [r["relative_url"] for r in runs],
    }

    raw_json = json.dumps(chart_payload)

    # -------------------------------------------------------------
    # 1. GENERATE DASHBOARD HTML (artifacts/index.html & dashboard.html)
    # -------------------------------------------------------------
    dashboard_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Benchmark Telemetry & Longitudinal Analysis</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body {
      margin: 0;
      padding: 24px;
      background-color: #0d1117;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      color: #c9d1d9;
    }
    .container { max-width: 1200px; margin: 0 auto; }
    .header { 
      border-bottom: 1px solid #30363d; 
      padding-bottom: 14px; 
      margin-bottom: 24px; 
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      flex-wrap: wrap;
      gap: 12px;
    }
    .nav-links {
      display: flex;
      gap: 10px;
    }
    .nav-btn {
      background: #21262d;
      color: #58a6ff;
      border: 1px solid #30363d;
      padding: 6px 14px;
      border-radius: 6px;
      text-decoration: none;
      font-size: 13px;
      font-weight: 500;
      transition: all 0.2s ease;
    }
    .nav-btn:hover {
      background: #30363d;
      border-color: #8b949e;
    }
    .nav-btn.active {
      background: #1f6feb;
      color: #ffffff;
      border-color: #388bfd;
    }
    h1 { color: #58a6ff; font-size: 24px; margin: 0; }
    .subtitle { color: #8b949e; font-size: 13px; margin-top: 6px; }
    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      margin-bottom: 20px;
    }
    .chart-card {
      background-color: #161b22;
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 20px;
    }
    h2 { color: #f0f6fc; font-size: 15px; margin: 0 0 16px 0; }
    select {
      background: #21262d;
      color: #f0f6fc;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 13px;
    }
    table.detail-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      margin-top: 8px;
    }
    table.detail-table th, table.detail-table td {
      padding: 8px 12px;
      text-align: left;
      border-bottom: 1px solid #21262d;
    }
    table.detail-table th { color: #8b949e; }
    .tag-a { color: #58a6ff; font-weight: 600; }
    .tag-b { color: #bc8cff; font-weight: 600; }
    @media (max-width: 850px) {
      .grid-2 { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div>
        <h1>AI Benchmark Telemetry & Longitudinal Analysis</h1>
        <div class="subtitle">Aggregated across """ + str(len(runs)) + """ benchmark snapshot(s) in <code>artifacts/</code></div>
      </div>
      <div class="nav-links">
        <a href="index.html" class="nav-btn active">Dashboard</a>
        <a href="digests.html" class="nav-btn">View Daily Digests ↗</a>
      </div>
    </div>

    <div class="chart-card">
      <h2>1. Token Breakdown by Track (Hover for Prompt / CoT / Completion details)</h2>
      <canvas id="tokensChart" height="90"></canvas>
    </div>

    <div class="grid-2">
      <div class="chart-card">
        <h2>2. Wall-Clock Execution Latency (Seconds)</h2>
        <canvas id="latencyChart" height="150"></canvas>
      </div>

      <div class="chart-card">
        <h2>3. Track B Agent Search Efficiency (Tokens / Turn)</h2>
        <canvas id="efficiencyChart" height="150"></canvas>
      </div>
    </div>

    <div class="grid-2">
      <div class="chart-card">
        <h2>4. Topic & Domain Diversity Bias (Radar)</h2>
        <canvas id="radarChart" height="150"></canvas>
      </div>

      <div class="chart-card">
        <h2>5. Top Publisher Attribution Frequency</h2>
        <canvas id="publisherChart" height="150"></canvas>
      </div>
    </div>

    <div class="chart-card">
      <h2>6. Publication Age Delta (Average Days Between Story Publish Date & Benchmark Run)</h2>
      <canvas id="ageChart" height="75"></canvas>
    </div>

    <div class="chart-card">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 8px;">
        <h2>Article Recency Breakdown</h2>
        <div style="display: flex; gap: 8px; align-items: center;">
          <select id="runSelect" onchange="updateStoryTable(this.value)"></select>
          <a id="viewDigestLink" href="digests.html" class="nav-btn" style="font-size: 12px; padding: 5px 10px;">Open Digest ↗</a>
        </div>
      </div>
      <table class="detail-table">
        <thead>
          <tr>
            <th>Track</th>
            <th>Headline</th>
            <th>Source</th>
            <th>Published Date</th>
            <th>Age Delta</th>
          </tr>
        </thead>
        <tbody id="storyTableBody"></tbody>
      </table>
    </div>
  </div>

  <script>
    const data = """ + raw_json + """;

    new Chart(document.getElementById('tokensChart').getContext('2d'), {
      type: 'bar',
      data: {
        labels: data.labels,
        datasets: [
          { label: 'Track A: Prompt', data: data.tokens_stacked.track_a.prompt, backgroundColor: '#1f6feb', stack: 'Track A' },
          { label: 'Track A: Reasoning', data: data.tokens_stacked.track_a.reasoning, backgroundColor: '#388bfd', stack: 'Track A' },
          { label: 'Track A: Completion', data: data.tokens_stacked.track_a.completion, backgroundColor: '#79c0ff', stack: 'Track A' },
          { label: 'Track B: Prompt', data: data.tokens_stacked.track_b.prompt, backgroundColor: '#8957e5', stack: 'Track B' },
          { label: 'Track B: Reasoning', data: data.tokens_stacked.track_b.reasoning, backgroundColor: '#ab7df8', stack: 'Track B' },
          { label: 'Track B: Completion', data: data.tokens_stacked.track_b.completion, backgroundColor: '#d2a8ff', stack: 'Track B' }
        ]
      },
      options: {
        responsive: true,
        plugins: {
          tooltip: { callbacks: { label: (c) => c.dataset.label + ': ' + c.parsed.y.toLocaleString() + ' tokens' } },
          legend: { labels: { color: '#c9d1d9', font: { size: 10 } } }
        },
        scales: {
          x: { stacked: true, ticks: { color: '#8b949e', maxRotation: 20 }, grid: { color: '#21262d' } },
          y: { stacked: true, ticks: { color: '#8b949e' }, grid: { color: '#21262d' } }
        }
      }
    });

    new Chart(document.getElementById('latencyChart').getContext('2d'), {
      type: 'bar',
      data: {
        labels: data.labels,
        datasets: [
          { label: 'Track A (Deterministic)', data: data.latency.track_a, backgroundColor: '#58a6ff' },
          { label: 'Track B (Autonomous ReAct)', data: data.latency.track_b, backgroundColor: '#bc8cff' }
        ]
      },
      options: {
        responsive: true,
        plugins: {
          tooltip: { callbacks: { label: (c) => c.dataset.label + ': ' + c.parsed.y.toFixed(2) + 's' } },
          legend: { labels: { color: '#c9d1d9' } }
        },
        scales: {
          x: { ticks: { color: '#8b949e', maxRotation: 25 }, grid: { color: '#21262d' } },
          y: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' } }
        }
      }
    });

    new Chart(document.getElementById('efficiencyChart').getContext('2d'), {
      type: 'line',
      data: {
        labels: data.labels,
        datasets: [{
          label: 'Track B Tokens / Agent Turn',
          data: data.tokens_per_turn,
          borderColor: '#f0883e',
          backgroundColor: 'rgba(240, 136, 62, 0.15)',
          fill: true,
          tension: 0.3,
          pointRadius: 5
        }]
      },
      options: {
        responsive: true,
        plugins: {
          tooltip: { callbacks: { label: (c) => c.parsed.y.toLocaleString() + ' tokens / turn' } },
          legend: { labels: { color: '#c9d1d9' } }
        },
        scales: {
          x: { ticks: { color: '#8b949e', maxRotation: 25 }, grid: { color: '#21262d' } },
          y: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' } }
        }
      }
    });

    new Chart(document.getElementById('radarChart').getContext('2d'), {
      type: 'radar',
      data: {
        labels: data.category_diversity.labels,
        datasets: [
          {
            label: 'Track A (Deterministic)',
            data: data.category_diversity.track_a,
            backgroundColor: 'rgba(88, 166, 255, 0.25)',
            borderColor: '#58a6ff',
            pointBackgroundColor: '#58a6ff'
          },
          {
            label: 'Track B (Autonomous ReAct)',
            data: data.category_diversity.track_b,
            backgroundColor: 'rgba(188, 140, 255, 0.25)',
            borderColor: '#bc8cff',
            pointBackgroundColor: '#bc8cff'
          }
        ]
      },
      options: {
        responsive: true,
        scales: {
          r: {
            grid: { color: '#30363d' },
            angleLines: { color: '#30363d' },
            pointLabels: { color: '#c9d1d9', font: { size: 11 } },
            ticks: { display: false }
          }
        },
        plugins: {
          legend: { labels: { color: '#c9d1d9' } }
        }
      }
    });

    new Chart(document.getElementById('publisherChart').getContext('2d'), {
      type: 'bar',
      data: {
        labels: data.publisher_attribution.labels,
        datasets: [{
          label: 'Stories Surfaced',
          data: data.publisher_attribution.counts,
          backgroundColor: '#238636',
          borderRadius: 4
        }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (c) => c.parsed.x + ' stories' } }
        },
        scales: {
          x: { ticks: { color: '#8b949e', stepSize: 1 }, grid: { color: '#21262d' } },
          y: { ticks: { color: '#c9d1d9' }, grid: { display: false } }
        }
      }
    });

    new Chart(document.getElementById('ageChart').getContext('2d'), {
      type: 'bar',
      data: {
        labels: data.labels,
        datasets: [{
          label: 'Avg Days Since Published',
          data: data.publication_age_delta,
          backgroundColor: '#d29922',
          borderRadius: 4
        }]
      },
      options: {
        responsive: true,
        plugins: {
          tooltip: {
            callbacks: {
              label: (c) => c.parsed.y !== null ? c.parsed.y + ' days old' : 'No date data available'
            }
          },
          legend: { labels: { color: '#c9d1d9' } }
        },
        scales: {
          x: { ticks: { color: '#8b949e', maxRotation: 20 }, grid: { color: '#21262d' } },
          y: {
            min: 0,
            suggestedMax: 7,
            ticks: { color: '#8b949e' },
            grid: { color: '#21262d' },
            title: { display: true, text: 'Days', color: '#8b949e' }
          }
        }
      }
    });

    const selectElem = document.getElementById('runSelect');
    const viewDigestLink = document.getElementById('viewDigestLink');

    data.labels.forEach((label, idx) => {
      const opt = document.createElement('option');
      opt.value = idx;
      opt.textContent = label;
      selectElem.appendChild(opt);
    });

    selectElem.value = data.labels.length - 1;

    function updateStoryTable(runIdx) {
      const tbody = document.getElementById('storyTableBody');
      tbody.innerHTML = '';
      const stories = data.runs_stories[runIdx] || [];
      stories.forEach(s => {
        const row = document.createElement('tr');
        const trackClass = s.track === 'Track A' ? 'tag-a' : 'tag-b';
        const ageDisplay = s.age_days !== 'N/A' ? s.age_days + 'd' : '—';
        row.innerHTML = `
          <td class="${trackClass}">${s.track}</td>
          <td>${s.title}</td>
          <td>${s.source}</td>
          <td>${s.date_str}</td>
          <td><strong>${ageDisplay}</strong></td>
        `;
        tbody.appendChild(row);
      });

      if (viewDigestLink && data.digest_urls && data.digest_urls[runIdx]) {
        viewDigestLink.href = 'digests.html?run=' + runIdx;
      }
    }

    updateStoryTable(selectElem.value);
  </script>
</body>
</html>"""

    # -------------------------------------------------------------
    # 2. GENERATE DIGEST ARCHIVE VIEWER HTML (artifacts/digests.html)
    # -------------------------------------------------------------
    digest_items = [
        {"idx": i, "label": f"Run {i+1} ({r['formatted_time']})", "url": r["relative_url"]}
        for i, r in enumerate(runs)
    ]
    digests_json = json.dumps(digest_items)

    digests_viewer_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Benchmark Digest Archive</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 0;
      background-color: #0d1117;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      color: #c9d1d9;
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .top-bar {
      background-color: #161b22;
      border-bottom: 1px solid #30363d;
      padding: 12px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }
    .brand-group {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .brand-title {
      font-size: 16px;
      font-weight: 600;
      color: #58a6ff;
    }
    .nav-btn {
      background: #21262d;
      color: #58a6ff;
      border: 1px solid #30363d;
      padding: 6px 12px;
      border-radius: 6px;
      text-decoration: none;
      font-size: 13px;
      font-weight: 500;
      transition: all 0.2s ease;
      cursor: pointer;
    }
    .nav-btn:hover {
      background: #30363d;
      border-color: #8b949e;
    }
    .controls {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    select {
      background: #21262d;
      color: #f0f6fc;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 13px;
      max-width: 320px;
    }
    .iframe-container {
      flex: 1;
      width: 100%;
      height: 100%;
      background: #ffffff;
      position: relative;
    }
    iframe {
      width: 100%;
      height: 100%;
      border: none;
    }
  </style>
</head>
<body>
  <div class="top-bar">
    <div class="brand-group">
      <span class="brand-title">AI Benchmark Digest Archive</span>
      <a href="index.html" class="nav-btn">← Back to Telemetry Dashboard</a>
    </div>

    <div class="controls">
      <button class="nav-btn" onclick="stepRun(-1)" title="Previous Run">‹ Prev</button>
      <select id="digestSelect" onchange="loadDigest(this.value)"></select>
      <button class="nav-btn" onclick="stepRun(1)" title="Next Run">Next ›</button>
      <a id="externalLink" href="#" target="_blank" class="nav-btn" style="color: #7ee787;">Open in New Tab ↗</a>
    </div>
  </div>

  <div class="iframe-container">
    <iframe id="digestFrame" src="about:blank"></iframe>
  </div>

  <script>
    const digests = """ + digests_json + """;
    const selectElem = document.getElementById('digestSelect');
    const frame = document.getElementById('digestFrame');
    const extLink = document.getElementById('externalLink');

    // Populate dropdown
    digests.forEach((item, idx) => {
      const opt = document.createElement('option');
      opt.value = idx;
      opt.textContent = item.label;
      selectElem.appendChild(opt);
    });

    // Handle deep-linking via query param (?run=X), or default to the most recent run
    const params = new URLSearchParams(window.location.search);
    let currentIdx = digests.length - 1;

    if (params.has('run')) {
      const parsedIdx = parseInt(params.get('run'), 10);
      if (!isNaN(parsedIdx) && parsedIdx >= 0 && parsedIdx < digests.length) {
        currentIdx = parsedIdx;
      }
    }

    function loadDigest(idx) {
      currentIdx = parseInt(idx, 10);
      selectElem.value = currentIdx;
      const targetUrl = digests[currentIdx].url;
      frame.src = targetUrl;
      extLink.href = targetUrl;

      // Update URL query string without reloading page
      const newUrl = new URL(window.location);
      newUrl.searchParams.set('run', currentIdx);
      window.history.replaceState({}, '', newUrl);
    }

    function stepRun(direction) {
      const nextIdx = currentIdx + direction;
      if (nextIdx >= 0 && nextIdx < digests.length) {
        loadDigest(nextIdx);
      }
    }

    // Load initial digest
    loadDigest(currentIdx);
  </script>
</body>
</html>"""

    # 1. Output index.html for default GitHub Pages routing
    index_path = artifacts_dir / "index.html"
    index_path.write_text(dashboard_html, encoding="utf-8")

    # 2. Output dashboard.html for backward compatibility
    dashboard_path = artifacts_dir / "dashboard.html"
    shutil.copyfile(index_path, dashboard_path)

    # 3. Output digests.html for the interactive digest archive viewer
    digests_viewer_path = artifacts_dir / "digests.html"
    digests_viewer_path.write_text(digests_viewer_html, encoding="utf-8")

    print(f"[Dashboard] Rendered multi-metric telemetry dashboard to: {index_path} and {dashboard_path}")
    print(f"[Dashboard] Rendered digest archive viewer to: {digests_viewer_path}")


if __name__ == "__main__":
    generate_dashboard()

# import json
# import re
# import shutil
# import traceback
# import urllib.request
# from collections import Counter
# from datetime import datetime, timezone
# from pathlib import Path
# from zoneinfo import ZoneInfo
# from bs4 import BeautifulSoup


# CENTRAL_TZ = ZoneInfo("America/Chicago")
# DATE_CACHE_FILE = Path(__file__).resolve().parent.parent / "artifacts" / ".date_cache.json"


# def load_date_cache() -> dict:
#     if DATE_CACHE_FILE.exists():
#         try:
#             return json.loads(DATE_CACHE_FILE.read_text(encoding="utf-8"))
#         except Exception:
#             return {}
#     return {}


# def save_date_cache(cache: dict):
#     try:
#         DATE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
#         DATE_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
#     except Exception:
#         pass


# def clean_int(val_str):
#     digits = re.sub(r"[^\d]", "", str(val_str))
#     return int(digits) if digits else 0


# def clean_float(val_str):
#     match = re.search(r"(\d+(?:\.\d+)?)", str(val_str))
#     return float(match.group(1)) if match else 0.0


# def parse_relative_or_abs_date(date_str, run_dt):
#     if not date_str:
#         return None
#     d = date_str.strip().strip('"').strip("'")
#     d = re.sub(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*", "", d, flags=re.IGNORECASE)
#     d = re.sub(r"Sept\.?", "Sep", d, flags=re.IGNORECASE)

#     if not re.search(r"\b20\d{2}\b", d):
#         d = f"{d} {run_dt.year}"

#     formats = [
#         "%d %b %Y",
#         "%d %B %Y",
#         "%b %d, %Y",
#         "%b. %d, %Y",
#         "%b %d %Y",
#         "%B %d, %Y",
#         "%Y-%m-%d",
#         "%Y/%m/%d",
#     ]

#     for fmt in formats:
#         try:
#             parsed = datetime.strptime(d.strip(), fmt).replace(tzinfo=CENTRAL_TZ)
#             delta = (run_dt.date() - parsed.date()).total_seconds() / 86400.0
#             return max(0.0, round(delta, 1))
#         except ValueError:
#             continue
#     return None


# def fetch_published_date_from_url(url: str) -> str | None:
#     if not url or not url.startswith("http"):
#         return None

#     cache = load_date_cache()
#     if url in cache:
#         return cache[url]

#     req = urllib.request.Request(
#         url,
#         headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
#     )
#     try:
#         with urllib.request.urlopen(req, timeout=3.5) as resp:
#             html_chunk = resp.read(35000).decode("utf-8", errors="ignore")
#             soup = BeautifulSoup(html_chunk, "html.parser")

#             for meta_name in [
#                 "article:published_time",
#                 "og:pubdate",
#                 "pubdate",
#                 "publishdate",
#                 "date",
#                 "DC.date.issued",
#                 "parsely-pub-date"
#             ]:
#                 tag = soup.find("meta", attrs={"property": meta_name}) or soup.find("meta", attrs={"name": meta_name})
#                 if tag and tag.get("content"):
#                     raw_val = tag["content"][:10]
#                     if re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}$", raw_val):
#                         cache[url] = raw_val
#                         save_date_cache(cache)
#                         return raw_val

#             # 2. Schema.org JSON-LD
#             for script in soup.find_all("script", type="application/ld+json"):
#                 if script.string and "datePublished" in script.string:
#                     match = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', script.string)
#                     if match:
#                         raw_val = match.group(1)
#                         cache[url] = raw_val
#                         save_date_cache(cache)
#                         return raw_val

#             # 3. MediaPost & byline text fallback (e.g., ", September 3, 2026")
#             if "mediapost.com" in url:
#                 mp_match = re.search(
#                     r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2}",
#                     html_chunk,
#                     re.IGNORECASE,
#                 )
#                 if mp_match:
#                     try:
#                         dt = datetime.strptime(
#                             mp_match.group(0).replace(",", ""), "%B %d %Y"
#                         )
#                         raw_val = dt.strftime("%Y-%m-%d")
#                         cache[url] = raw_val
#                         save_date_cache(cache)
#                         return raw_val
#                     except ValueError:
#                         pass

#     except Exception:
#         pass

#     cache[url] = None
#     save_date_cache(cache)
#     return None


# def extract_legacy_date_from_summary(summary_text, run_dt):
#     if not summary_text:
#         return "—", None

#     match_range = re.search(r"Sept?\.?\s*(\d{1,2})[–-](\d{1,2})(?:,?\s*20\d{2})?", summary_text, re.IGNORECASE)
#     if match_range:
#         day = match_range.group(1)
#         d_str = f"Sep {day}, {run_dt.year}"
#         delta = parse_relative_or_abs_date(d_str, run_dt)
#         return d_str, delta

#     match_single = re.search(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s*(\d{1,2})(?:,?\s*(20\d{2}))?", summary_text, re.IGNORECASE)
#     if match_single:
#         raw_found = match_single.group(0)
#         delta = parse_relative_or_abs_date(raw_found, run_dt)
#         return raw_found, delta

#     if any(k in summary_text.lower() for k in ["astra", "gpt-6", "a20 pro", "plasticity"]):
#         d_str = f"Sep 03, {run_dt.year}"
#         delta = max(0.0, round((run_dt.date() - datetime(run_dt.year, 9, 3).date()).days, 1))
#         return d_str, delta

#     return "—", None


# def extract_run_data_from_html(html_path):
#     try:
#         content = html_path.read_text(encoding="utf-8")
#         soup = BeautifulSoup(content, "html.parser")
#         table = soup.find("table", class_="table-bench")

#         if not table:
#             print(f"[Warning] No table-bench scorecard found in {html_path.name}")
#             return None

#         raw_metrics = {}
#         for row in table.find_all("tr"):
#             cells = row.find_all(["td", "th"])
#             if len(cells) == 3:
#                 metric_label = cells[0].get_text(strip=True).lower()
#                 val_a = cells[1].get_text(strip=True)
#                 val_b = cells[2].get_text(strip=True)
#                 raw_metrics[metric_label] = (val_a, val_b)

#         lat_a, lat_b = raw_metrics.get("wall clock latency", ("0", "0"))
#         pt_a, pt_b = raw_metrics.get("input / prompt tokens", ("0", "0"))
#         rt_a, rt_b = raw_metrics.get("reasoning (cot) tokens", ("0", "0"))
#         ct_a, ct_b = raw_metrics.get("completion tokens", ("0", "0"))
#         tot_a, tot_b = raw_metrics.get("total tokens consumed", ("0", "0"))
#         _, turns_b = raw_metrics.get("agentic iterations / turns", ("1", "1"))

#         ts_match = re.search(r"(\d{8}_\d{6})", html_path.stem)
#         if ts_match:
#             raw_ts = ts_match.group(1)
#             utc_dt = datetime.strptime(raw_ts, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
#             run_dt = utc_dt.astimezone(CENTRAL_TZ)
#             formatted_time = run_dt.strftime("%b %d, %-I:%M %p %Z")
#         else:
#             run_dt = datetime.now(CENTRAL_TZ)
#             formatted_time = html_path.stem

#         agent_turns = clean_int(turns_b) or 1
#         total_tokens_b = clean_int(tot_b)

#         categories_a = []
#         categories_b = []
#         sources = []
#         age_deltas = []
#         detailed_stories = []

#         cards = soup.find_all("div", class_="card")
#         for card in cards:
#             card_title = card.find(["div", "h4"])
#             card_text = card_title.get_text() if card_title else ""
#             is_track_a = "Track A" in card_text
#             is_track_b = "Track B" in card_text and "Deep-Dive" not in card_text

#             if not (is_track_a or is_track_b):
#                 continue

#             track_label = "Track A" if is_track_a else "Track B"

#             for item in card.find_all("div", class_="story-item"):
#                 title_elem = item.find("a", class_="story-title")
#                 story_title = title_elem.get_text(strip=True) if title_elem else "Untitled"
#                 story_url = title_elem.get("href", "") if title_elem else ""

#                 summary_elem = item.find("div", class_="story-summary")
#                 summary_text = summary_elem.get_text(strip=True) if summary_elem else ""

#                 badge = item.find("span", class_="badge")
#                 cat = badge.get_text(strip=True) if badge else "Industry & Models"

#                 if is_track_a:
#                     categories_a.append(cat)
#                 else:
#                     categories_b.append(cat)

#                 src_name = "Unknown"
#                 meta = item.find("div", class_="story-meta")
#                 if meta:
#                     match_src = re.search(r"Source:\s*([^•\n<]+)", meta.get_text())
#                     if match_src:
#                         src_name = match_src.group(1).strip()
#                         sources.append(src_name)

#                 delta_days = None
#                 date_str_display = "—"

#                 # 1. Check subtitle tag
#                 orig_div = item.find("div", class_="original-title")
#                 if orig_div:
#                     raw_orig = orig_div.get_text().strip()
#                     parts = re.split(r"[•·]|&bull;", raw_orig)
#                     if len(parts) > 1:
#                         date_str_display = parts[-1].strip()
#                         delta_days = parse_relative_or_abs_date(date_str_display, run_dt)

#                 # 2a. Check ArXiv slug (arxiv.org/abs/2609.xxxxx)
#                 if delta_days is None and "arxiv.org/abs/" in story_url:
#                     arxiv_match = re.search(r"arxiv\.org/abs/(\d{2})(\d{2})\.", story_url)
#                     if arxiv_match:
#                         yr, mo = arxiv_match.groups()
#                         date_str_display = f"Sep {run_dt.year}" if mo == "09" else f"20{yr}-{mo}"
#                         delta_days = max(0.0, round((run_dt.date() - datetime(run_dt.year, int(mo), 6).date()).days, 1))

#                 # 2b. Check Techmeme permalink slug (techmeme.com/260909/p29 -> 2026-09-09)
#                 if delta_days is None and "techmeme.com" in story_url:
#                     tm_match = re.search(r"techmeme\.com/(\d{2})(\d{2})(\d{2})/", story_url)
#                     if tm_match:
#                         yr, mo, dy = tm_match.groups()
#                         parsed_tm_date = datetime(2000 + int(yr), int(mo), int(dy), tzinfo=CENTRAL_TZ)
#                         date_str_display = parsed_tm_date.strftime("%b %d, %Y")
#                         delta_days = max(0.0, round((run_dt.date() - parsed_tm_date.date()).days, 1))
#                     else:
#                         date_str_display = run_dt.strftime("%b %d, %Y")
#                         delta_days = 0.0

#                 # 3. Dynamic HTTP head lookup for standalone articles
#                 if delta_days is None and story_url.startswith("http"):
#                     remote_date = fetch_published_date_from_url(story_url)
#                     if remote_date:
#                         derived_delta = parse_relative_or_abs_date(remote_date, run_dt)
#                         if derived_delta is not None:
#                             date_str_display = remote_date
#                             delta_days = derived_delta

#                 # 4. Fallback for Runs 1-5 without tags
#                 if delta_days is None and is_track_b:
#                     fallback_date, fallback_delta = extract_legacy_date_from_summary(summary_text, run_dt)
#                     if fallback_delta is not None:
#                         date_str_display = fallback_date
#                         delta_days = fallback_delta

#                 # 5. Fallback if site blocked crawler (e.g. 403 on The Information / Bloomberg)
#                 if delta_days is None:
#                     date_str_display = run_dt.strftime("%b %d, %Y")
#                     delta_days = 0.0

#                 if delta_days is not None:
#                     age_deltas.append(delta_days)

#                 detailed_stories.append({
#                     "track": track_label,
#                     "title": story_title,
#                     "source": src_name,
#                     "category": cat,
#                     "date_str": date_str_display,
#                     "age_days": delta_days if delta_days is not None else "N/A",
#                 })

#         return {
#             "formatted_time": formatted_time,
#             "filename": html_path.name,
#             "track_a": {
#                 "wall_time": clean_float(lat_a),
#                 "prompt": clean_int(pt_a),
#                 "reasoning": clean_int(rt_a),
#                 "completion": clean_int(ct_a),
#                 "total": clean_int(tot_a),
#                 "categories": categories_a,
#             },
#             "track_b": {
#                 "wall_time": clean_float(lat_b),
#                 "prompt": clean_int(pt_b),
#                 "reasoning": clean_int(rt_b),
#                 "completion": clean_int(ct_b),
#                 "total": total_tokens_b,
#                 "agent_turns": agent_turns,
#                 "tokens_per_turn": round(total_tokens_b / agent_turns, 1) if agent_turns else 0,
#                 "categories": categories_b,
#             },
#             "sources": sources,
#             "age_deltas": age_deltas,
#             "stories": detailed_stories,
#         }
#     except Exception as e:
#         print(f"[Error parsing {html_path.name}]: {e}")
#         traceback.print_exc()
#         return None


# def generate_dashboard():
#     project_root = Path(__file__).resolve().parent.parent
#     artifacts_dir = project_root / "artifacts"
#     digests_dir = artifacts_dir / "digests"
#     data_dir = artifacts_dir / "data"

#     artifacts_dir.mkdir(exist_ok=True)
#     digests_dir.mkdir(exist_ok=True)
#     data_dir.mkdir(exist_ok=True)

#     # Consolidate loose files from root if any exist
#     for loose_file in project_root.glob("digest_preview_*.html"):
#         target = digests_dir / loose_file.name
#         if not target.exists():
#             loose_file.replace(target)
#             print(f"[Dashboard] Consolidated {loose_file.name} -> artifacts/digests/")

#     # Search both digests subfolder and legacy root artifacts/
#     html_files = sorted(set(list(digests_dir.glob("digest_preview_*.html")) + list(artifacts_dir.glob("digest_preview_*.html"))))
#     print(f"[Dashboard] Found {len(html_files)} snapshot file(s)")

#     runs = []
#     for hf in html_files:
#         parsed = extract_run_data_from_html(hf)
#         if parsed:
#             runs.append(parsed)
#             print(f"  ✓ Successfully parsed: {hf.name} -> {parsed['formatted_time']}")
#         else:
#             print(f"  ✗ Failed to parse: {hf.name}")

#     if not runs:
#         print("[Dashboard] No valid runs extracted. Aborting.")
#         return

#     standard_categories = [
#         "Research & Papers",
#         "Industry & Models",
#         "Infrastructure & Tools",
#         "Policy & Ethics",
#     ]
#     track_a_all_cats = [c for r in runs for c in r["track_a"]["categories"]]
#     track_b_all_cats = [c for r in runs for c in r["track_b"]["categories"]]
#     counts_a = Counter(track_a_all_cats)
#     counts_b = Counter(track_b_all_cats)

#     all_sources = [s for r in runs for s in r["sources"]]
#     source_counts = Counter(all_sources).most_common(8)

#     avg_age_per_run = [
#         round(sum(r["age_deltas"]) / len(r["age_deltas"]), 1) if r["age_deltas"] else None
#         for r in runs
#     ]

#     chart_payload = {
#         "labels": [f"Run {i+1} ({r['formatted_time']})" for i, r in enumerate(runs)],
#         "tokens_stacked": {
#             "track_a": {
#                 "prompt": [r["track_a"]["prompt"] for r in runs],
#                 "reasoning": [r["track_a"]["reasoning"] for r in runs],
#                 "completion": [r["track_a"]["completion"] for r in runs],
#             },
#             "track_b": {
#                 "prompt": [r["track_b"]["prompt"] for r in runs],
#                 "reasoning": [r["track_b"]["reasoning"] for r in runs],
#                 "completion": [r["track_b"]["completion"] for r in runs],
#             },
#         },
#         "latency": {
#             "track_a": [r["track_a"]["wall_time"] for r in runs],
#             "track_b": [r["track_b"]["wall_time"] for r in runs],
#         },
#         "tokens_per_turn": [r["track_b"]["tokens_per_turn"] for r in runs],
#         "category_diversity": {
#             "labels": standard_categories,
#             "track_a": [counts_a.get(c, 0) for c in standard_categories],
#             "track_b": [counts_b.get(c, 0) for c in standard_categories],
#         },
#         "publisher_attribution": {
#             "labels": [s[0] for s in source_counts],
#             "counts": [s[1] for s in source_counts],
#         },
#         "publication_age_delta": avg_age_per_run,
#         "runs_stories": [r["stories"] for r in runs],
#     }

#     raw_json = json.dumps(chart_payload)

#     dashboard_html = """<!DOCTYPE html>
# <html lang="en">
# <head>
#   <meta charset="utf-8">
#   <meta name="viewport" content="width=device-width, initial-scale=1.0">
#   <title>AI Benchmark Telemetry & Longitudinal Analysis</title>
#   <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
#   <style>
#     body {
#       margin: 0;
#       padding: 24px;
#       background-color: #0d1117;
#       font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
#       color: #c9d1d9;
#     }
#     .container { max-width: 1200px; margin: 0 auto; }
#     .header { border-bottom: 1px solid #30363d; padding-bottom: 14px; margin-bottom: 24px; }
#     h1 { color: #58a6ff; font-size: 24px; margin: 0; }
#     .subtitle { color: #8b949e; font-size: 13px; margin-top: 6px; }
#     .grid-2 {
#       display: grid;
#       grid-template-columns: 1fr 1fr;
#       gap: 20px;
#       margin-bottom: 20px;
#     }
#     .chart-card {
#       background-color: #161b22;
#       border: 1px solid #30363d;
#       border-radius: 8px;
#       padding: 20px;
#       margin-bottom: 20px;
#     }
#     h2 { color: #f0f6fc; font-size: 15px; margin: 0 0 16px 0; }
#     select {
#       background: #21262d;
#       color: #f0f6fc;
#       border: 1px solid #30363d;
#       border-radius: 6px;
#       padding: 6px 12px;
#       font-size: 13px;
#     }
#     table.detail-table {
#       width: 100%;
#       border-collapse: collapse;
#       font-size: 12px;
#       margin-top: 8px;
#     }
#     table.detail-table th, table.detail-table td {
#       padding: 8px 12px;
#       text-align: left;
#       border-bottom: 1px solid #21262d;
#     }
#     table.detail-table th { color: #8b949e; }
#     .tag-a { color: #58a6ff; font-weight: 600; }
#     .tag-b { color: #bc8cff; font-weight: 600; }
#     @media (max-width: 850px) {
#       .grid-2 { grid-template-columns: 1fr; }
#     }
#   </style>
# </head>
# <body>
#   <div class="container">
#     <div class="header">
#       <h1>AI Benchmark Telemetry & Longitudinal Analysis</h1>
#       <div class="subtitle">Aggregated across """ + str(len(runs)) + """ benchmark snapshot(s) in <code>artifacts/</code></div>
#     </div>

#     <div class="chart-card">
#       <h2>1. Token Breakdown by Track (Hover for Prompt / CoT / Completion details)</h2>
#       <canvas id="tokensChart" height="90"></canvas>
#     </div>

#     <div class="grid-2">
#       <div class="chart-card">
#         <h2>2. Wall-Clock Execution Latency (Seconds)</h2>
#         <canvas id="latencyChart" height="150"></canvas>
#       </div>

#       <div class="chart-card">
#         <h2>3. Track B Agent Search Efficiency (Tokens / Turn)</h2>
#         <canvas id="efficiencyChart" height="150"></canvas>
#       </div>
#     </div>

#     <div class="grid-2">
#       <div class="chart-card">
#         <h2>4. Topic & Domain Diversity Bias (Radar)</h2>
#         <canvas id="radarChart" height="150"></canvas>
#       </div>

#       <div class="chart-card">
#         <h2>5. Top Publisher Attribution Frequency</h2>
#         <canvas id="publisherChart" height="150"></canvas>
#       </div>
#     </div>

#     <div class="chart-card">
#       <h2>6. Publication Age Delta (Average Days Between Story Publish Date & Benchmark Run)</h2>
#       <canvas id="ageChart" height="75"></canvas>
#     </div>

#     <div class="chart-card">
#       <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
#         <h2>Article Recency Breakdown</h2>
#         <select id="runSelect" onchange="updateStoryTable(this.value)"></select>
#       </div>
#       <table class="detail-table">
#         <thead>
#           <tr>
#             <th>Track</th>
#             <th>Headline</th>
#             <th>Source</th>
#             <th>Published Date</th>
#             <th>Age Delta</th>
#           </tr>
#         </thead>
#         <tbody id="storyTableBody"></tbody>
#       </table>
#     </div>
#   </div>

#   <script>
#     const data = """ + raw_json + """;

#     new Chart(document.getElementById('tokensChart').getContext('2d'), {
#       type: 'bar',
#       data: {
#         labels: data.labels,
#         datasets: [
#           { label: 'Track A: Prompt', data: data.tokens_stacked.track_a.prompt, backgroundColor: '#1f6feb', stack: 'Track A' },
#           { label: 'Track A: Reasoning', data: data.tokens_stacked.track_a.reasoning, backgroundColor: '#388bfd', stack: 'Track A' },
#           { label: 'Track A: Completion', data: data.tokens_stacked.track_a.completion, backgroundColor: '#79c0ff', stack: 'Track A' },
#           { label: 'Track B: Prompt', data: data.tokens_stacked.track_b.prompt, backgroundColor: '#8957e5', stack: 'Track B' },
#           { label: 'Track B: Reasoning', data: data.tokens_stacked.track_b.reasoning, backgroundColor: '#ab7df8', stack: 'Track B' },
#           { label: 'Track B: Completion', data: data.tokens_stacked.track_b.completion, backgroundColor: '#d2a8ff', stack: 'Track B' }
#         ]
#       },
#       options: {
#         responsive: true,
#         plugins: {
#           tooltip: { callbacks: { label: (c) => c.dataset.label + ': ' + c.parsed.y.toLocaleString() + ' tokens' } },
#           legend: { labels: { color: '#c9d1d9', font: { size: 10 } } }
#         },
#         scales: {
#           x: { stacked: true, ticks: { color: '#8b949e', maxRotation: 20 }, grid: { color: '#21262d' } },
#           y: { stacked: true, ticks: { color: '#8b949e' }, grid: { color: '#21262d' } }
#         }
#       }
#     });

#     new Chart(document.getElementById('latencyChart').getContext('2d'), {
#       type: 'bar',
#       data: {
#         labels: data.labels,
#         datasets: [
#           { label: 'Track A (Deterministic)', data: data.latency.track_a, backgroundColor: '#58a6ff' },
#           { label: 'Track B (Autonomous ReAct)', data: data.latency.track_b, backgroundColor: '#bc8cff' }
#         ]
#       },
#       options: {
#         responsive: true,
#         plugins: {
#           tooltip: { callbacks: { label: (c) => c.dataset.label + ': ' + c.parsed.y.toFixed(2) + 's' } },
#           legend: { labels: { color: '#c9d1d9' } }
#         },
#         scales: {
#           x: { ticks: { color: '#8b949e', maxRotation: 25 }, grid: { color: '#21262d' } },
#           y: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' } }
#         }
#       }
#     });

#     new Chart(document.getElementById('efficiencyChart').getContext('2d'), {
#       type: 'line',
#       data: {
#         labels: data.labels,
#         datasets: [{
#           label: 'Track B Tokens / Agent Turn',
#           data: data.tokens_per_turn,
#           borderColor: '#f0883e',
#           backgroundColor: 'rgba(240, 136, 62, 0.15)',
#           fill: true,
#           tension: 0.3,
#           pointRadius: 5
#         }]
#       },
#       options: {
#         responsive: true,
#         plugins: {
#           tooltip: { callbacks: { label: (c) => c.parsed.y.toLocaleString() + ' tokens / turn' } },
#           legend: { labels: { color: '#c9d1d9' } }
#         },
#         scales: {
#           x: { ticks: { color: '#8b949e', maxRotation: 25 }, grid: { color: '#21262d' } },
#           y: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' } }
#         }
#       }
#     });

#     new Chart(document.getElementById('radarChart').getContext('2d'), {
#       type: 'radar',
#       data: {
#         labels: data.category_diversity.labels,
#         datasets: [
#           {
#             label: 'Track A (Deterministic)',
#             data: data.category_diversity.track_a,
#             backgroundColor: 'rgba(88, 166, 255, 0.25)',
#             borderColor: '#58a6ff',
#             pointBackgroundColor: '#58a6ff'
#           },
#           {
#             label: 'Track B (Autonomous ReAct)',
#             data: data.category_diversity.track_b,
#             backgroundColor: 'rgba(188, 140, 255, 0.25)',
#             borderColor: '#bc8cff',
#             pointBackgroundColor: '#bc8cff'
#           }
#         ]
#       },
#       options: {
#         responsive: true,
#         scales: {
#           r: {
#             grid: { color: '#30363d' },
#             angleLines: { color: '#30363d' },
#             pointLabels: { color: '#c9d1d9', font: { size: 11 } },
#             ticks: { display: false }
#           }
#         },
#         plugins: {
#           legend: { labels: { color: '#c9d1d9' } }
#         }
#       }
#     });

#     new Chart(document.getElementById('publisherChart').getContext('2d'), {
#       type: 'bar',
#       data: {
#         labels: data.publisher_attribution.labels,
#         datasets: [{
#           label: 'Stories Surfaced',
#           data: data.publisher_attribution.counts,
#           backgroundColor: '#238636',
#           borderRadius: 4
#         }]
#       },
#       options: {
#         indexAxis: 'y',
#         responsive: true,
#         plugins: {
#           legend: { display: false },
#           tooltip: { callbacks: { label: (c) => c.parsed.x + ' stories' } }
#         },
#         scales: {
#           x: { ticks: { color: '#8b949e', stepSize: 1 }, grid: { color: '#21262d' } },
#           y: { ticks: { color: '#c9d1d9' }, grid: { display: false } }
#         }
#       }
#     });

#     new Chart(document.getElementById('ageChart').getContext('2d'), {
#       type: 'bar',
#       data: {
#         labels: data.labels,
#         datasets: [{
#           label: 'Avg Days Since Published',
#           data: data.publication_age_delta,
#           backgroundColor: '#d29922',
#           borderRadius: 4
#         }]
#       },
#       options: {
#         responsive: true,
#         plugins: {
#           tooltip: {
#             callbacks: {
#               label: (c) => c.parsed.y !== null ? c.parsed.y + ' days old' : 'No date data available'
#             }
#           },
#           legend: { labels: { color: '#c9d1d9' } }
#         },
#         scales: {
#           x: { ticks: { color: '#8b949e', maxRotation: 20 }, grid: { color: '#21262d' } },
#           y: {
#             min: 0,
#             suggestedMax: 7,
#             ticks: { color: '#8b949e' },
#             grid: { color: '#21262d' },
#             title: { display: true, text: 'Days', color: '#8b949e' }
#           }
#         }
#       }
#     });

#     const selectElem = document.getElementById('runSelect');
#     data.labels.forEach((label, idx) => {
#       const opt = document.createElement('option');
#       opt.value = idx;
#       opt.textContent = label;
#       selectElem.appendChild(opt);
#     });

#     selectElem.value = data.labels.length - 1;

#     function updateStoryTable(runIdx) {
#       const tbody = document.getElementById('storyTableBody');
#       tbody.innerHTML = '';
#       const stories = data.runs_stories[runIdx] || [];
#       stories.forEach(s => {
#         const row = document.createElement('tr');
#         const trackClass = s.track === 'Track A' ? 'tag-a' : 'tag-b';
#         const ageDisplay = s.age_days !== 'N/A' ? s.age_days + 'd' : '—';
#         row.innerHTML = `
#           <td class="${trackClass}">${s.track}</td>
#           <td>${s.title}</td>
#           <td>${s.source}</td>
#           <td>${s.date_str}</td>
#           <td><strong>${ageDisplay}</strong></td>
#         `;
#         tbody.appendChild(row);
#       });
#     }

#     updateStoryTable(selectElem.value);
#   </script>
# </body>
# </html>"""

#     # 1. Output index.html for default GitHub Pages routing
#     index_path = artifacts_dir / "index.html"
#     index_path.write_text(dashboard_html, encoding="utf-8")

#     # 2. Output dashboard.html for backward compatibility
#     dashboard_path = artifacts_dir / "dashboard.html"
#     shutil.copyfile(index_path, dashboard_path)

#     print(f"[Dashboard] Rendered multi-metric telemetry dashboard to: {index_path} and {dashboard_path}")


# if __name__ == "__main__":
#     generate_dashboard()