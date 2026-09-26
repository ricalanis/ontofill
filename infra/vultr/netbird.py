"""Prepare isolated NetBird Cloud peers and scoped policies for the Vultr VMs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from infra.vultr.provision import persist_env

BASE = "https://api.netbird.io/api"
NAMES = {
    "control": "ontofill-control-plane",
    "sandbox": "ontofill-sandbox-host",
    "admins": "ontofill-admins",
}
KEY_NAMES = {
    "control": "ontofill-control-plane-setup",
    "sandbox": "ontofill-sandbox-host-setup",
}


class NetBirdAPI:
    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("NETBIRD_API_TOKEN is required")
        self._token = token

    def request(self, method: str, path: str, payload: dict | None = None) -> dict | list:
        if not path.startswith("/") or ".." in path:
            raise ValueError("invalid NetBird API path")
        request = Request(
            BASE + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={
                "Authorization": "Token " + self._token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
        except HTTPError as exc:
            raise RuntimeError(f"NetBird {method} {path} failed (HTTP {exc.code})") from None
        except URLError:
            raise RuntimeError(f"NetBird {method} {path} failed (network)") from None
        return json.loads(body) if body else {}


def plan() -> dict:
    return {
        "mode": "dry-run",
        "groups": list(NAMES.values()),
        "setup_keys": list(KEY_NAMES.values()),
        "policies": ["ontofill-admin-ssh", "ontofill-control-to-sandbox-ssh"],
        "default_policy": "left enabled during prepare; disable only after VM access is verified",
        "secrets": "NETBIRD_API_TOKEN is read only during prepare/finalize; setup keys go to ignored .env",
    }


def _one_named(rows: list[dict], name: str) -> dict | None:
    found = [row for row in rows if row.get("name") == name]
    if len(found) > 1:
        raise RuntimeError(f"multiple NetBird resources named {name}")
    return found[0] if found else None


def ensure_group(api: NetBirdAPI, name: str, peers: list[str], role: str = "") -> dict:
    existing = _one_named(api.request("GET", "/groups"), name)
    if existing is not None:
        existing_peers = {row["id"] for row in existing.get("peers", [])}
        enrolled = existing.get("peers", [])
        role_enrolled = (
            role in {"control", "sandbox"}
            and len(enrolled) == 1
            and enrolled[0].get("name", "").endswith("-" + role)
        )
        if existing_peers != set(peers) and not role_enrolled:
            raise RuntimeError(f"existing NetBird group {name} has unexpected peers")
        return existing
    return api.request("POST", "/groups", {"name": name, "peers": peers})


def _rule(name: str, sources: list[str], destinations: list[str]) -> dict:
    return {
        "name": name,
        "description": name,
        "enabled": True,
        "action": "accept",
        "bidirectional": False,
        "protocol": "tcp",
        "ports": ["22"],
        "sources": sources,
        "destinations": destinations,
    }


def ensure_policy(api: NetBirdAPI, name: str, rule: dict) -> dict:
    existing = _one_named(api.request("GET", "/policies"), name)
    if existing is not None:
        current = existing.get("rules", [])
        if len(current) != 1:
            raise RuntimeError(f"existing NetBird policy {name} has unexpected rules")
        observed = current[0]
        for field in ("action", "bidirectional", "protocol", "ports"):
            if observed.get(field) != rule[field]:
                raise RuntimeError(f"existing NetBird policy {name} differs from plan")
        for field in ("sources", "destinations"):
            ids = {
                row.get("id") if isinstance(row, dict) else row for row in observed.get(field, [])
            }
            if ids != set(rule[field]):
                raise RuntimeError(f"existing NetBird policy {name} differs from plan")
        if not existing.get("enabled") or not observed.get("enabled"):
            raise RuntimeError(f"existing NetBird policy {name} is disabled")
        return existing
    return api.request(
        "POST", "/policies", {"name": name, "description": name, "enabled": True, "rules": [rule]}
    )


def ensure_setup_key(
    api: NetBirdAPI, name: str, group_id: str, stored_key: str, peer_enrolled: bool
) -> str:
    existing = _one_named(api.request("GET", "/setup-keys"), name)
    if existing is not None:
        consumed = peer_enrolled and existing.get("used_times") == 1
        if existing.get("auto_groups") != [group_id] or not (existing.get("valid") or consumed):
            raise RuntimeError(f"existing NetBird setup key {name} differs from plan")
        if not stored_key:
            raise RuntimeError(f"NetBird setup key {name} exists but is absent from ignored .env")
        return stored_key
    created = api.request(
        "POST",
        "/setup-keys",
        {
            "name": name,
            "type": "one-off",
            "expires_in": 86400,
            "auto_groups": [group_id],
            "usage_limit": 1,
            "ephemeral": False,
            "allow_extra_dns_labels": False,
        },
    )
    key = created.get("key", "")
    if not key:
        raise RuntimeError(f"NetBird did not return setup key {name}")
    return key


def prepare(api: NetBirdAPI, env_file: Path) -> dict:
    peers = api.request("GET", "/peers")
    laptops = [row for row in peers if "darwin" in row.get("os", "").lower()]
    if len(laptops) != 1:
        raise RuntimeError("expected exactly one existing macOS admin peer")
    groups = {
        role: ensure_group(api, name, [laptops[0]["id"]] if role == "admins" else [], role)
        for role, name in NAMES.items()
    }
    ensure_policy(
        api,
        "ontofill-admin-ssh",
        _rule(
            "admins-to-vms-ssh",
            [groups["admins"]["id"]],
            [groups["control"]["id"], groups["sandbox"]["id"]],
        ),
    )
    ensure_policy(
        api,
        "ontofill-control-to-sandbox-ssh",
        _rule("control-to-sandbox-ssh", [groups["control"]["id"]], [groups["sandbox"]["id"]]),
    )
    from dotenv import dotenv_values

    stored = dotenv_values(env_file)
    keys = {
        "NETBIRD_CONTROL_SETUP_KEY": ensure_setup_key(
            api,
            KEY_NAMES["control"],
            groups["control"]["id"],
            stored.get("NETBIRD_CONTROL_SETUP_KEY") or "",
            len(groups["control"].get("peers", [])) == 1,
        ),
        "NETBIRD_SANDBOX_SETUP_KEY": ensure_setup_key(
            api,
            KEY_NAMES["sandbox"],
            groups["sandbox"]["id"],
            stored.get("NETBIRD_SANDBOX_SETUP_KEY") or "",
            len(groups["sandbox"].get("peers", [])) == 1,
        ),
    }
    persist_env(env_file, keys)
    return {
        "groups": {role: group["id"] for role, group in groups.items()},
        "setup_keys_written": True,
        "default_policy": "still enabled until finalize",
    }


def finalize(api: NetBirdAPI) -> dict:
    """Disable the broad default only after peer enrollment and SSH verification."""
    peers = api.request("GET", "/peers")
    groups = {row["name"]: row for row in api.request("GET", "/groups")}
    for name in NAMES.values():
        if name not in groups or len(groups[name].get("peers", [])) != 1:
            raise RuntimeError(f"NetBird group {name} has not enrolled exactly one peer")
    for role in ("control", "sandbox"):
        peer_id = groups[NAMES[role]]["peers"][0]["id"]
        if not any(row.get("id") == peer_id and row.get("connected") for row in peers):
            raise RuntimeError(f"NetBird {role} peer is not connected")
    policies = api.request("GET", "/policies")
    default = _one_named(policies, "Default")
    if default is None:
        raise RuntimeError("NetBird Default policy was not found")
    if default.get("enabled"):
        body = {
            "name": default["name"],
            "description": default.get("description", ""),
            "enabled": False,
            "rules": [
                {
                    "name": rule["name"],
                    "description": rule.get("description", ""),
                    "enabled": rule.get("enabled", True),
                    "action": rule["action"],
                    "bidirectional": rule["bidirectional"],
                    "protocol": rule["protocol"],
                    "ports": rule.get("ports", []),
                    "sources": [row["id"] for row in rule["sources"]],
                    "destinations": [row["id"] for row in rule["destinations"]],
                }
                for rule in default.get("rules", [])
            ],
        }
        api.request("PUT", f"/policies/{default['id']}", body)
    return {"default_policy_enabled": False, "enrolled_peer_count": 2}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "prepare", "finalize"))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--ssh-verified", action="store_true")
    args = parser.parse_args(argv)
    if args.mode == "plan":
        print(json.dumps(plan(), indent=2))
        return 0
    if args.mode == "finalize" and not args.ssh_verified:
        parser.error("finalize requires --ssh-verified after testing the admin and dispatch paths")
    env_file = args.env_file or Path(__file__).resolve().parents[2] / ".env"
    if env_file.name != ".env" or env_file.is_symlink():
        parser.error("--env-file must reference a non-symlink .env file")
    from dotenv import load_dotenv

    load_dotenv(env_file)
    api = NetBirdAPI(os.environ.get("NETBIRD_API_TOKEN", ""))
    result = prepare(api, env_file) if args.mode == "prepare" else finalize(api)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
