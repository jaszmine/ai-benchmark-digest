import asyncio
from pathlib import Path
from playwright.async_api import async_playwright


async def capture_dashboard():
    artifacts_dir = Path(__file__).resolve().parent.parent / "artifacts"
    html_file = (artifacts_dir / "dashboard.html").resolve().as_uri()

    output_png = artifacts_dir / "dashboard_fullscreen.png"
    output_pdf = artifacts_dir / "dashboard_export.pdf"

    async with async_playwright() as p:
        # launch local Google Chrome
        browser = await p.chromium.launch(channel="chrome")
        page = await browser.new_page(viewport={"width": 1400, "height": 900})

        await page.goto(html_file, wait_until="networkidle")
        await asyncio.sleep(1.0)

        # Full-page screenshot
        await page.screenshot(path=str(output_png), full_page=True)
        print(f"[Export] Full-page PNG saved to: {output_png}")

        # Vector PDF
        await page.pdf(
            path=str(output_pdf),
            format="A4",
            print_background=True,
            margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
        )
        print(f"[Export] Dashboard PDF saved to: {output_pdf}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(capture_dashboard())