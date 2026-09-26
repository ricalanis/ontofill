# Sandbox images and fixtures

The `agent-pod` image also has a long-lived `/app/cdp.py` entrypoint for native
browser cells. It runs containment preflight, then starts Chromium with a
private temporary profile, remote debugging endpoint, and the `egress`
allowlist proxy. `CellManager` starts it only with `runsc`, a read-only root
filesystem, tmpfs output, no Linux capabilities, and explicit resource caps.
See `src/ontofill/sandbox/README.md` for its API and verification limits.

The `egress` image also contains `/app/relay.py`, a fixed-destination TCP
relay. Skyvern cells use separate relay processes for their hands CDP,
inference gateway, and loopback brain API. They contain no model credential.

`agent-pod/` contains a disposable Chromium capture worker. It receives only
page/proxy/probe settings and a step cap, never inference credentials. It
publishes pod identity, isolation and secret-hygiene probes with the captured
result. `egress/` is the explicit domain-allowlist proxy. Both containers are
resource capped by the control-plane dispatcher.

`fixtures/hostile.html` and `fixtures/destructive_loop.py` support opt-in
containment checks. The destructive fixture runs only in a read-only,
unprivileged, networkless `runsc` container with no host mounts.
