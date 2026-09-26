"""Long-lived, secret-free Chromium hands for a single browser cell."""

from __future__ import annotations

import json
import signal
import subprocess
import time
from pathlib import Path

from capture import isolation_probes, peak_memory_mb, pod_identity, secret_probes
from playwright.sync_api import sync_playwright


def main() -> int:
    proxy = "http://egress:8888"
    identity = pod_identity()
    isolation = isolation_probes(proxy)
    secrets = secret_probes()
    proof = {
        "pod_identity": identity,
        "isolation_probes": isolation,
        "secret_probes": secrets,
        "peak_memory_mb": peak_memory_mb(),
    }
    Path("/out/preflight.json").write_text(json.dumps(proof), encoding="utf-8")
    if (
        not isolation["network"].get("blocked")
        or not all(item.get("blocked") for item in isolation["writes"].values())
        or secrets["env_keys_found"]
        or secrets["files_with_keys"]
        or secrets["metadata_ip"] != "BLOCKED"
        or secrets["mesh"] != "BLOCKED"
    ):
        return 2

    with sync_playwright() as playwright:
        browser_path = playwright.chromium.executable_path
    command = [
        browser_path,
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--no-first-run",
        "--remote-debugging-address=0.0.0.0",
        "--remote-debugging-port=9222",
        "--remote-allow-origins=*",
        "--user-data-dir=/tmp/chromium-profile",
        "--proxy-server=http://egress:8888",
        "--proxy-bypass-list=<-loopback>",
        "about:blank",
    ]
    browser = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(_signum: int, _frame: object) -> None:
        browser.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while browser.poll() is None:
        time.sleep(0.2)
    return browser.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
