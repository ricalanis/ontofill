"""Playwright runs only inside this disposable container."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import platform
import re
import resource
import socket
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from spider_policy import CrawlPolicy, crawl_site

_SECRET_ENV = re.compile(
    r"(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|PRIVATE[_-]?KEY|"
    r"AWS_ACCESS_KEY|VULTR_|NETBIRD_SETUP|JEV_)",
    re.IGNORECASE,
)


class StepLimitReached(RuntimeError):
    """The pod's local action counter reached its configured cap."""


class StepBudget:
    def __init__(self) -> None:
        self.maximum = int(os.environ["CAPTURE_MAX_STEPS"])
        self.steps = 0

    def take(self) -> None:
        if self.steps >= self.maximum:
            raise StepLimitReached("max_steps")
        self.steps += 1


def peak_memory_mb() -> float:
    cgroup_peak = Path("/sys/fs/cgroup/memory.peak")
    if cgroup_peak.exists():
        try:
            return round(int(cgroup_peak.read_text().strip()) / (1024 * 1024), 3)
        except (OSError, ValueError):
            pass
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3)


def _direct_probe(host: str, port: int) -> str:
    try:
        with socket.create_connection((host, port), timeout=1):
            return "ALLOWED"
    except OSError:
        return "BLOCKED"


def secret_probes() -> dict:
    """Count exposed credential names/files without reading or logging secret values."""
    secret_paths = (
        Path("/app/.env"),
        Path("/root/.aws/credentials"),
        Path("/home/pwuser/.aws/credentials"),
        Path("/var/run/secrets"),
        Path("/run/secrets"),
    )
    files_with_keys = 0
    for path in secret_paths:
        try:
            if path.is_file():
                files_with_keys += 1
            elif path.is_dir():
                files_with_keys += sum(item.is_file() for item in path.rglob("*"))
        except PermissionError:
            # An unprivileged pod cannot read an inaccessible host or image path.
            continue
    mesh_ip = os.environ.get("PROBE_MESH_IP", "100.64.0.1")
    return {
        "env_keys_found": sum(bool(_SECRET_ENV.search(name)) for name in os.environ),
        "files_with_keys": files_with_keys,
        "metadata_ip": _direct_probe("169.254.169.254", 80),
        "mesh": _direct_probe(mesh_ip, 22),
    }


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
    isolation = isolation_probes(proxy_url)
    secrets = secret_probes()
    budget = StepBudget()
    preflight = {
        "pod_identity": identity,
        "isolation_probes": isolation,
        "secret_probes": secrets,
    }
    if (
        secrets["env_keys_found"]
        or secrets["files_with_keys"]
        or "ALLOWED" in (secrets["metadata_ip"], secrets["mesh"])
    ):
        write_result(
            output,
            {**preflight, "hygiene_failure": True, "steps": 0, "peak_memory_mb": peak_memory_mb()},
        )
        return
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            proxy={"server": proxy_url, "bypass": "<-loopback>"},
            args=["--no-sandbox"],
        )
        try:
            context = await browser.new_context(ignore_https_errors=False, service_workers="block")
            page = await context.new_page()
            navigation_chain: list[str] = []
            redirect_location_reads: list[asyncio.Task] = []

            def record_navigation(request) -> None:
                try:
                    main_frame_navigation = (
                        request.is_navigation_request() and request.frame == page.main_frame
                    )
                except PlaywrightError:
                    return
                if main_frame_navigation and (
                    not navigation_chain or navigation_chain[-1] != request.url
                ):
                    navigation_chain.append(request.url)

            async def record_redirect_location(response) -> None:
                try:
                    request = response.request
                    if (
                        not request.is_navigation_request()
                        or request.frame != page.main_frame
                        or response.status not in {300, 301, 302, 303, 307, 308}
                    ):
                        return
                    location = await response.header_value("location")
                except PlaywrightError:
                    return
                if location:
                    redirected_url = urljoin(response.url, location)
                    try:
                        source_index = (
                            len(navigation_chain) - 1 - navigation_chain[::-1].index(response.url)
                        )
                    except ValueError:
                        navigation_chain.append(response.url)
                        source_index = len(navigation_chain) - 1
                    if (
                        source_index + 1 == len(navigation_chain)
                        or navigation_chain[source_index + 1] != redirected_url
                    ):
                        navigation_chain.insert(source_index + 1, redirected_url)

            async def flush_redirect_locations() -> None:
                if redirect_location_reads:
                    await asyncio.gather(*redirect_location_reads, return_exceptions=True)

            def observe_response(response) -> None:
                if response.status in {300, 301, 302, 303, 307, 308}:
                    redirect_location_reads.append(
                        asyncio.create_task(record_redirect_location(response))
                    )

            async def read_only(route) -> None:
                # Route callbacks run before the request reaches the egress proxy.
                # Keep the attempted main-frame URL even when the proxy rejects it
                # and Playwright later reports a navigation error.
                record_navigation(route.request)
                if route.request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
                    await route.continue_()
                else:
                    await route.abort()

            await context.route("**/*", read_only)

            page.on("request", record_navigation)
            page.on("response", observe_response)
            budget.take()
            try:
                response = await page.goto(target, wait_until="load", timeout=30000)
            except PlaywrightError as exc:
                await flush_redirect_locations()
                write_result(
                    output,
                    {
                        "url": page.url,
                        "redirect_chain": navigation_chain or [target],
                        "navigation_error": type(exc).__name__,
                        **preflight,
                        "steps": budget.steps,
                        "peak_memory_mb": peak_memory_mb(),
                    },
                )
                return
            await flush_redirect_locations()
            html = await page.content()
            accessibility = await page.locator("body").aria_snapshot()
            budget.take()
            screenshot = await page.screenshot(full_page=True)
            (output / "page.html").write_text(html, encoding="utf-8")
            (output / "a11y.txt").write_text(accessibility, encoding="utf-8")
            (output / "screenshot.png").write_bytes(screenshot)
            write_result(
                output,
                {
                    "url": page.url,
                    "redirect_chain": navigation_chain or [target],
                    "status": response.status if response else None,
                    **preflight,
                    "steps": budget.steps,
                    "peak_memory_mb": peak_memory_mb(),
                },
            )
        except StepLimitReached:
            write_result(
                output,
                {
                    **preflight,
                    "limit_reason": "max_steps",
                    "steps": budget.steps,
                    "peak_memory_mb": peak_memory_mb(),
                },
            )
        finally:
            await browser.close()


def fetch() -> None:
    target = os.environ["CAPTURE_URL"]
    proxy_url = os.environ["PROXY_URL"]
    identity = pod_identity()
    isolation = isolation_probes(proxy_url)
    secrets = secret_probes()
    budget = StepBudget()
    preflight = {
        "pod_identity": identity,
        "isolation_probes": isolation,
        "secret_probes": secrets,
    }
    output = Path("/out")
    if (
        secrets["env_keys_found"]
        or secrets["files_with_keys"]
        or "ALLOWED" in (secrets["metadata_ip"], secrets["mesh"])
    ):
        write_result(
            output,
            {**preflight, "hygiene_failure": True, "steps": 0, "peak_memory_mb": peak_memory_mb()},
        )
        return
    opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    request = Request(target, headers={"User-Agent": "Ontofill/0.1"}, method="GET")
    budget.take()
    with opener.open(request, timeout=30) as response:
        payload = response.read(20 * 1024 * 1024 + 1)
        if len(payload) > 20 * 1024 * 1024:
            raise ValueError("download exceeds the 20 MiB capture limit")
        result = {
            "url": response.geturl(),
            "status": response.status,
            "content_type": response.headers.get_content_type(),
            **preflight,
            "steps": budget.steps,
            "peak_memory_mb": peak_memory_mb(),
        }
    (output / "payload.bin").write_bytes(payload)
    write_result(output, result)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def spider() -> None:
    """Run the entire bounded read-only crawl in this gVisor cell."""
    target = os.environ["CAPTURE_URL"]
    proxy_url = os.environ["PROXY_URL"]
    output = Path("/out")
    identity = pod_identity()
    isolation = isolation_probes(proxy_url)
    secrets = secret_probes()
    budget = StepBudget()
    preflight = {
        "pod_identity": identity,
        "isolation_probes": isolation,
        "secret_probes": secrets,
    }
    if (
        secrets["env_keys_found"]
        or secrets["files_with_keys"]
        or "ALLOWED" in (secrets["metadata_ip"], secrets["mesh"])
    ):
        write_result(
            output,
            {**preflight, "hygiene_failure": True, "steps": 0, "peak_memory_mb": peak_memory_mb()},
        )
        return

    config = json.loads(os.environ["SPIDER_CONFIG"])
    policy = CrawlPolicy(
        allowed_domain=os.environ["SPIDER_ALLOWED_DOMAIN"],
        max_depth=config["max_depth"],
        page_cap=config["page_cap"],
        delay_seconds=config["delay_seconds"],
        max_redirects=config["max_redirects"],
        max_redirects_total=config["max_redirects_total"],
        max_response_bytes=config["max_response_bytes"],
    )
    opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}), _NoRedirect())
    request_count = 0

    def fetch_one(url: str) -> dict:
        nonlocal request_count
        budget.take()
        request_count += 1
        request = Request(
            url,
            headers={"User-Agent": policy.user_agent, "Accept": "*/*"},
            method="GET",
        )
        try:
            with opener.open(request, timeout=30) as response:
                body = response.read(policy.max_response_bytes + 1)
                return {
                    "status": response.status,
                    "final_url": response.geturl(),
                    "location": response.headers.get("Location"),
                    "content_type": response.headers.get_content_type(),
                    "body": body,
                }
        except HTTPError as exc:
            body = exc.read(policy.max_response_bytes + 1)
            return {
                "status": exc.code,
                "final_url": url,
                "location": exc.headers.get("Location") if exc.headers else None,
                "content_type": (
                    exc.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0]
                    if exc.headers
                    else "application/octet-stream"
                ),
                "body": body,
                "too_large": len(body) > policy.max_response_bytes,
            }
        except (URLError, TimeoutError, OSError):
            return {"status": None, "final_url": url, "body": b"", "error": "network_error"}

    try:
        crawl = crawl_site(target, policy=policy, fetch=fetch_one)
    except StepLimitReached:
        write_result(
            output,
            {
                **preflight,
                "url": target,
                "redirect_chain": [target],
                "status": None,
                "limit_reason": "max_steps",
                "steps": budget.steps,
                "peak_memory_mb": peak_memory_mb(),
            },
        )
        return

    for index, robots in enumerate(crawl["robots"]):
        body = robots.pop("body", b"")
        filename = (
            f"robots-{index:04d}.txt"
            if isinstance(robots.get("http_status"), int) and isinstance(body, bytes)
            else None
        )
        if filename:
            (output / filename).write_bytes(body)
        robots["file_name"] = filename
    for index, page in enumerate(crawl["pages"]):
        body = page.pop("body", b"")
        is_html = page["content_type"].casefold().startswith(("text/html", "application/xhtml+xml"))
        has_response = type(page.get("status")) is int and 100 <= page["status"] <= 599
        filename = (
            f"page-{index:04d}.{'html' if is_html else 'bin'}"
            if isinstance(body, bytes) and has_response
            else None
        )
        if filename:
            (output / filename).write_bytes(body)
        page["file_name"] = filename
    seed_status = crawl["pages"][0].get("status") if crawl["pages"] else None
    write_result(
        output,
        {
            **preflight,
            "url": target,
            "redirect_chain": [target],
            "status": seed_status if type(seed_status) is int else 0,
            "crawl": {
                key: value
                for key, value in crawl.items()
                if key not in {"pages", "robots", "edges", "blocked"}
            },
            "pages": crawl["pages"],
            "robots": crawl["robots"],
            "edges": crawl["edges"],
            "blocked": crawl["blocked"],
            "request_count": request_count,
            "steps": budget.steps,
            "peak_memory_mb": peak_memory_mb(),
        },
    )


if __name__ == "__main__":
    mode = os.environ.get("CAPTURE_MODE", "page")
    if mode == "fetch":
        fetch()
    elif mode == "spider":
        spider()
    else:
        asyncio.run(capture())
    wait_for_copy_ack()
