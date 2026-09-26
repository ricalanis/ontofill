"""Playwright runs only inside this disposable container."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

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
            context = await browser.new_context(ignore_https_errors=False, service_workers="block")

            async def read_only(route) -> None:
                if route.request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
                    await route.continue_()
                else:
                    await route.abort()

            await context.route("**/*", read_only)
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


def fetch() -> None:
    target = os.environ["CAPTURE_URL"]
    proxy_url = os.environ["PROXY_URL"]
    opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    request = Request(target, headers={"User-Agent": "Ontofill/0.1"}, method="GET")
    with opener.open(request, timeout=30) as response:
        payload = response.read(20 * 1024 * 1024 + 1)
        if len(payload) > 20 * 1024 * 1024:
            raise ValueError("download exceeds the 20 MiB capture limit")
        result = {
            "url": response.geturl(),
            "status": response.status,
            "content_type": response.headers.get_content_type(),
        }
    output = Path("/out")
    (output / "payload.bin").write_bytes(payload)
    (output / "result.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    if os.environ.get("CAPTURE_MODE", "page") == "fetch":
        fetch()
    else:
        asyncio.run(capture())
