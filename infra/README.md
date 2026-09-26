# infra

- `vultr/`: VM #1 control plane, VM #2 sandbox host, Object Storage bucket, burst instances
- `compose/`: control plane services (FastAPI, Postgres, Oxigraph)
- `netbird/`: minimal option only, one gated app URL (PIN/SSO) and zero inbound ports
