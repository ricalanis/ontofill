"""Playwright runs only inside this disposable container."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import platform
import socket
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from playwright.async_api import async_playwright


def pod_identity() -> dict:
    """Facts gathered by code executing inside this disposable pod."""
    cpuinfo = (
        Path("/proc/cpuinfo").read_text(errors="replace") if Path("/proc/cpuinfo").exists() else ""
    )
    flags = [flag for flag in ("vmx", "svm") if flag in cpuinfo.split()]
    return {
        "hostname": socket.gethostname(),
        "uname": platform.uname()._asdict(),
        "cpu_virtualization_flags": flags,
        "dev_kvm_present": Path("/dev/kvm").exists(),
    }


def isolation_probes(proxy_url: str) -> dict:
    """Exercise the actual proxy and read-only filesystem, then record failures."""
    denied_host = os.environ["PROBE_DENIED_HOST"]
    probe_url = f"http://{denied_host}/ontofill-isolation-probe"
    opener = build_opener(ProxyHandler({"http": proxy_url}))
    request = Request(probe_url, method="GET")
    try:
        with opener.open(request, timeout=10) as response:
            network = {"host": denied_host, "blocked": False, "status": response.status}
    except HTTPError as exc:
        network = {"host": denied_host, "blocked": exc.code == 403, "status": exc.code}
    except URLError as exc:
        network = {"host": denied_host, "blocked": False, "error": str(exc.reason)}

    writes = {}
    for label, path in (
        ("outside_pod", Path("/host/ontofill-proof-denied")),
        ("outside_writable_mount", Path("/etc/ontofill-proof-denied")),
    ):
        try:
            path.write_bytes(b"proof")
            path.unlink(missing_ok=True)
            writes[label] = {"path": str(path), "blocked": False}
        except OSError as exc:
            writes[label] = {
                "path": str(path),
                "blocked": exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOENT},
                "errno": exc.errno,
            }
    return {"network": network, "writes": writes}


def write_result(output: Path, result: dict) -> None:
    """Publish the completion marker only after all output bytes have been written."""
    temporary = output / "result.json.tmp"
    temporary.write_text(json.dumps(result), encoding="utf-8")
    temporary.replace(output / "result.json")


def wait_for_copy_ack() -> None:
    """Keep remote /out tmpfs mounted until the control plane copies it."""
    if os.environ.get("CAPTURE_WAIT_FOR_COPY") != "1":
        return
    for _ in range(600):
        if Path("/out/.copied").exists():
            return
        time.sleep(0.2)
    raise TimeoutError("control plane did not acknowledge sandbox output copy")


async def capture() -> None:
    target = os.environ["CAPTURE_URL"]
    proxy_url = os.environ["PROXY_URL"]
    output = Path("/out")
    identity = pod_identity()
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
            write_result(
                output,
                {
                    "url": page.url,
                    "status": response.status if response else None,
                    "pod_identity": identity,
                    "isolation_probes": isolation_probes(proxy_url),
                },
            )
        finally:
            await browser.close()


def fetch() -> None:
    target = os.environ["CAPTURE_URL"]
    proxy_url = os.environ["PROXY_URL"]
    identity = pod_identity()
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
            "pod_identity": identity,
            "isolation_probes": isolation_probes(proxy_url),
        }
    output = Path("/out")
    (output / "payload.bin").write_bytes(payload)
    write_result(output, result)


if __name__ == "__main__":
    if os.environ.get("CAPTURE_MODE", "page") == "fetch":
        fetch()
    else:
        asyncio.run(capture())
    wait_for_copy_ack()
