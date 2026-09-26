# Sandbox images and fixtures

`agent-pod/` contains a disposable Chromium capture worker. It receives only
page/proxy/probe settings and a step cap, never inference credentials. It
publishes pod identity, isolation and secret-hygiene probes with the captured
result. `egress/` is the explicit domain-allowlist proxy. Both containers are
resource capped by the control-plane dispatcher.

`fixtures/hostile.html` and `fixtures/destructive_loop.py` support opt-in
containment checks. The destructive fixture runs only in a read-only,
unprivileged, networkless `runsc` container with no host mounts.
