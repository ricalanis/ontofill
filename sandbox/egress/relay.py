"""Fixed-destination TCP relay for one cell's CDP or inference gateway."""

from __future__ import annotations

import os
import select
import socket
import socketserver

TARGET_HOST = os.environ["RELAY_TARGET_HOST"]
TARGET_PORT = int(os.environ["RELAY_TARGET_PORT"])
LISTEN_PORT = int(os.environ["RELAY_LISTEN_PORT"])


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            upstream = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=5)
        except OSError:
            return
        with upstream:
            sockets = (self.request, upstream)
            while True:
                try:
                    readable, _, _ = select.select(sockets, [], [], 30)
                    if not readable:
                        continue
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.request else self.request).sendall(data)
                except OSError:
                    return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("0.0.0.0", LISTEN_PORT), Handler) as server:
        server.serve_forever(poll_interval=0.2)
