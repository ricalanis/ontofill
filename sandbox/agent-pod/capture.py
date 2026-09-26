"""Playwright runs only inside this disposable container."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright


async def capture() -> None:
    target = os.environ["CAPTURE_URL"]
    proxy_url = os.environ["PROXY_URL"]
    output = Path("/out")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            proxy={"server": proxy_url, "bypass": "<-loopback>"},
            args=["--no-sandbox"],
        )
        try:
            context = await browser.new_context(ignore_https_errors=False)
            page = await context.new_page()
            response = await page.goto(target, wait_until="load", timeout=30000)
            html = await page.content()
            accessibility = await page.locator("body").aria_snapshot()
            screenshot = await page.screenshot(full_page=True)
            (output / "page.html").write_text(html, encoding="utf-8")
            (output / "a11y.txt").write_text(accessibility, encoding="utf-8")
            (output / "screenshot.png").write_bytes(screenshot)
            (output / "result.json").write_text(
                json.dumps({"url": page.url, "status": response.status if response else None}),
                encoding="utf-8",
            )
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(capture())
