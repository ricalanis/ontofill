"""Long-lived, secret-free Chromium hands for a single browser cell."""

from __future__ import annotations

import json
import select
import signal
import socket
import socketserver
import subprocess
import threading
import time
from pathlib import Path

from capture import isolation_probes, peak_memory_mb, pod_identity, secret_probes
from playwright.sync_api import sync_playwright


class _CDPBridgeHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        # Chromium binds its debugging socket to container loopback even when
        # asked for 0.0.0.0. Expose only this fixed port to the cell network.
        try:
            upstream = socket.create_connection(("127.0.0.1", 9222), timeout=5)
        except OSError:
            return
        with upstream:
            sockets = (self.request, upstream)
            while True:
                try:
                    readable, _, _ = select.select(sockets, [], [], 30)
                    if not readable:
                        return
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.request else self.request).sendall(data)
                except OSError:
                    return


class _CDPBridge(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


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
        # The container may exit before Docker-over-SSH can read its tmpfs.
        # Print only proof results, never environment values or credentials.
        print(
            json.dumps(
                {
                    "preflight_failed": {
                        "network": isolation["network"],
                        "writes": isolation["writes"],
                        "secret_probes": secrets,
                    }
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
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
    bridge = _CDPBridge(("0.0.0.0", 9223), _CDPBridgeHandler)
    threading.Thread(target=bridge.serve_forever, daemon=True).start()

    def stop(_signum: int, _frame: object) -> None:
        browser.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while browser.poll() is None:
        time.sleep(0.2)
    bridge.shutdown()
    bridge.server_close()
    return browser.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
