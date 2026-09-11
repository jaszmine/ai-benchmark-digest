import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from scripts.build_dashboard import generate_dashboard
from src.config import get_settings
from src.delivery.mailer import send_digest_email
from src.delivery.renderer import render_digest_html
from src.telemetry import (
    build_telemetry_scorecard,
    calculate_jaccard_convergence,
)
from src.track_a.pipeline import run_track_a
from src.track_b.deep_dive import run_track_b
from src.verifier import verify_links_batch

settings = get_settings()


async def orchestrate_benchmark(send_email: bool = False):
    print("=== Starting Comparative Benchmark ===")
    print(f"Model: {settings.model_name}")

    # Track A execution
    print("\n[Track A] Launching Deterministic Heuristic Pipeline...")
    track_a_output, telemetry_a = await run_track_a()
    print(
        f"[Track A] Completed in {telemetry_a['wall_time_seconds']}s. Tokens: {telemetry_a['total_tokens']}"
    )

    # Rate-limit budgeting pause
    print(
        f"\n[Rate-Limit Pause] Sleeping {settings.inter_track_pause_seconds}s before Track B..."
    )
    await asyncio.sleep(settings.inter_track_pause_seconds)

    # Track B execution
    print("\n[Track B] Launching Autonomous ReAct Agent...")
    track_b_output, telemetry_b = await run_track_b()
    print(
        f"[Track B] Completed in {telemetry_b['wall_time_seconds']}s. Tokens: {telemetry_b['total_tokens']}"
    )

    # Link Integrity Engine
    all_urls = [s.url for s in track_a_output.stories] + [
        s.url for s in track_b_output.stories
    ]
    print(f"\n[Verifier] Verifying {len(all_urls)} URLs across both tracks...")
    link_results = await verify_links_batch(all_urls)

    # Telemetry and Convergence
    convergence = calculate_jaccard_convergence(track_a_output, track_b_output)
    scorecard = build_telemetry_scorecard(
        telemetry_a, telemetry_b, convergence, link_results
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Render Template
    html_output = render_digest_html(
        track_a_output, track_b_output, scorecard, link_results
    )

    # Save artifacts into dedicated subdirectories
    artifacts_dir = Path(__file__).resolve().parent.parent / "artifacts"
    digests_dir = artifacts_dir / "digests"
    data_dir = artifacts_dir / "data"

    digests_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    html_path = digests_dir / f"digest_preview_{timestamp}.html"
    html_path.write_text(html_output, encoding="utf-8")
    print(f"[Renderer] HTML digest snapshot saved to: {html_path}")

    json_path = data_dir / f"run_{timestamp}.json"
    json_path.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    print(f"[Telemetry] Saved run scorecard to: {json_path}")

    # Rebuild dashboard (outputs to artifacts/index.html & artifacts/dashboard.html)
    print("[Dashboard] Rebuilding interactive telemetry dashboard...")
    generate_dashboard()

    # Dispatch (optional)
    if send_email:
        print("\n[Mailer] Dispatching email via Resend API...")
        dispatch_result = send_digest_email(html_output)
        print(f"[Mailer] Response: {dispatch_result}")

    print("\n=== Benchmark Completed Successfully ===")
    return scorecard


if __name__ == "__main__":
    asyncio.run(orchestrate_benchmark(send_email=False))