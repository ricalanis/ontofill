"""The small cell sidecar forwards TCP to its one configured destination."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


def test_fixed_destination_relay_round_trip() -> None:
    with socket.socket() as upstream:
        upstream.bind(("127.0.0.1", 0))
        upstream.listen(1)
        upstream_port = upstream.getsockname()[1]

        def echo_once() -> None:
            connection, _address = upstream.accept()
            with connection:
                data = connection.recv(64)
                connection.sendall(data)

        thread = threading.Thread(target=echo_once, daemon=True)
        thread.start()
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            relay_port = available.getsockname()[1]
        script = Path(__file__).resolve().parents[1] / "sandbox/egress/relay.py"
        env = {
            "PATH": os.environ.get("PATH", ""),
            "RELAY_TARGET_HOST": "127.0.0.1",
            "RELAY_TARGET_PORT": str(upstream_port),
            "RELAY_LISTEN_PORT": str(relay_port),
        }
        process = subprocess.Popen(
            [sys.executable, str(script)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(40):
                try:
                    with socket.create_connection(("127.0.0.1", relay_port), timeout=1) as client:
                        client.sendall(b"synthetic-ping")
                        assert client.recv(64) == b"synthetic-ping"
                    break
                except ConnectionRefusedError:
                    time.sleep(0.05)
            else:
                raise AssertionError("cell relay did not start")
            thread.join(timeout=2)
            assert not thread.is_alive()
        finally:
            process.terminate()
            process.wait(timeout=2)
