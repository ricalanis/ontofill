"""Idempotent Vultr VX1/VPC/Object Storage provisioning with a credential-free plan."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import tempfile
import textwrap
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

API_BASE = "https://api.vultr.com/v2"
OWNER_TAG = "ontofill-hackathon"
VPC_CIDR = "10.42.0.0/24"
_NAME = re.compile(r"[a-z][a-z0-9-]{1,31}\Z")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")


@dataclass(frozen=True)
class Config:
    region: str
    plan: str
    prefix: str
    bucket: str
    ssh_key_id: str | None
    object_tier_id: int
    object_cluster_id: int | None = None
    create_inference: bool = False

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.prefix):
            raise ValueError("prefix must be a short DNS-safe name")
        if not _BUCKET.fullmatch(self.bucket) or ".." in self.bucket:
            raise ValueError("bucket must be a valid DNS-safe S3 name")
        if not self.plan.startswith(("vx1-g-", "vx1-m-")) or not re.search(r"-\d+s\Z", self.plan):
            raise ValueError("plan must be a VX1 plan with a local NVMe boot disk")
        if not re.fullmatch(r"[a-z]{3}", self.region):
            raise ValueError("region must be a Vultr three-letter location ID")
        if self.object_tier_id <= 0:
            raise ValueError("object tier ID must be positive")
        if self.object_cluster_id is not None and self.object_cluster_id <= 0:
            raise ValueError("object cluster ID must be positive")


def resource_name(config: Config, suffix: str) -> str:
    """Encode ownership in names for Vultr resources that lack a tags field."""
    return f"{config.prefix}-{OWNER_TAG}-{suffix}"


def plan(config: Config) -> dict:
    """Return only public, intended operations; do not inspect env or call an API."""
    return {
        "mode": "dry-run",
        "region": config.region,
        "resources": [
            {"kind": "firewall_group", "name": resource_name(config, "deny-inbound"), "rules": []},
            {"kind": "vpc", "name": resource_name(config, "private"), "cidr": VPC_CIDR},
            {"kind": "ssh_key", "name": resource_name(config, "operator-ssh")},
            {
                "kind": "instance",
                "name": resource_name(config, "control"),
                "plan": config.plan,
                "os": "Ubuntu 24.04",
                "tags": [OWNER_TAG],
                "bootstrap": ["docker", "netbird"],
            },
            {
                "kind": "instance",
                "name": resource_name(config, "sandbox"),
                "plan": config.plan,
                "os": "Ubuntu 24.04",
                "tags": [OWNER_TAG],
                "bootstrap": ["docker", "runsc", "netbird", "/dev/kvm check"],
            },
            {
                "kind": "object_storage",
                "name": resource_name(config, "bronze"),
                "tier_id": config.object_tier_id,
                "cluster_id": config.object_cluster_id or "auto",
                "bucket": config.bucket,
            },
            *(
                [{"kind": "serverless_inference", "name": resource_name(config, "inference")}]
                if config.create_inference
                else []
            ),
        ],
        "network": "VPC plus NetBird peer-to-peer; Docker dispatch over SSH to sandbox NetBird IP",
        "secrets": "VULTR_API_KEY and NETBIRD_SETUP_KEY are read only during apply; keys never appear in plan output",
    }


def cloud_init(role: str, setup_key: str) -> str:
    """Cloud-init enrolls NetBird before the zero-inbound firewall makes SSH remote-only."""
    if role not in {"control", "sandbox"}:
        raise ValueError("unknown host role")
    if not re.fullmatch(r"[A-Za-z0-9-]{8,256}", setup_key):
        raise ValueError("NETBIRD_SETUP_KEY has an invalid format")
    script = textwrap.dedent(
        f"""\
        #!/bin/bash
        set -euo pipefail
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y ca-certificates curl gnupg docker.io ufw
        systemctl enable --now docker
        curl -fsSL https://pkgs.netbird.io/install.sh | sh
        trap 'rm -f /run/ontofill-netbird-setup-key' EXIT
        netbird up --setup-key-file /run/ontofill-netbird-setup-key --hostname ontofill-{role} >/dev/null
        ufw --force reset
        ufw default deny incoming
        ufw allow in on wt0 to any port 22 proto tcp
        ufw --force enable
        """
    )
    if role == "sandbox":
        script += textwrap.dedent(
            """\
            curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" > /etc/apt/sources.list.d/gvisor.list
            apt-get update
            apt-get install -y runsc
            runsc install
            # NetBird points the host resolver at its mesh IP. Container access
            # to mesh IPs is blocked, so use Vultr's public resolver in Docker.
            python3 - <<'PY'
            import json
            from pathlib import Path

            path = Path('/etc/docker/daemon.json')
            config = json.loads(path.read_text()) if path.exists() else {}
            config['dns'] = ['108.61.10.10']
            path.write_text(json.dumps(config) + '\\n')
            PY
            systemctl restart docker
            docker info --format '{{json .Runtimes}}' | grep -q runsc
            docker run --rm --runtime=runsc hello-world
            if [ -e /dev/kvm ]; then echo 'KVM_PRESENT=yes'; else echo 'KVM_PRESENT=no'; fi
            grep -Ecm1 '(vmx|svm)' /proc/cpuinfo || true
            """
        )
        script += textwrap.dedent(
            """\
            cat > /etc/systemd/system/ontofill-container-egress-guard.service <<'UNIT'
            [Unit]
            Description=Block sandbox containers from metadata and private peers
            Requires=docker.service
            After=docker.service

            [Service]
            Type=oneshot
            RemainAfterExit=yes
            ExecStart=/usr/local/sbin/ontofill-container-egress-guard

            [Install]
            WantedBy=multi-user.target
            UNIT
            cat > /usr/local/sbin/ontofill-container-egress-guard <<'GUARD'
            #!/bin/sh
            set -eu
            for network in 169.254.169.254/32 10.42.0.0/24; do
                iptables -C DOCKER-USER -d "$network" -j DROP 2>/dev/null || \\
                    iptables -I DOCKER-USER -d "$network" -j DROP
            done
            iptables -C DOCKER-USER -d 100.64.0.0/10 -m conntrack --ctstate NEW -j DROP 2>/dev/null || \\
                iptables -I DOCKER-USER -d 100.64.0.0/10 -m conntrack --ctstate NEW -j DROP
            GUARD
            chmod 0755 /usr/local/sbin/ontofill-container-egress-guard
            systemctl daemon-reload
            systemctl enable --now ontofill-container-egress-guard.service
            """
        )
    encoded = base64.b64encode(script.encode()).decode()
    encoded_key = base64.b64encode(setup_key.encode()).decode()
    return (
        "#cloud-config\n"
        "write_files:\n"
        "  - path: /usr/local/sbin/ontofill-host-bootstrap\n"
        "    permissions: '0755'\n"
        "    encoding: b64\n"
        f"    content: {encoded}\n"
        "  - path: /run/ontofill-netbird-setup-key\n"
        "    permissions: '0600'\n"
        "    encoding: b64\n"
        f"    content: {encoded_key}\n"
        "runcmd:\n"
        "  - [ /usr/local/sbin/ontofill-host-bootstrap ]\n"
    )


class VultrAPI:
    """Small API v2 client that deliberately never includes response bodies in errors."""

    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("VULTR_API_KEY is required for apply")
        self._key = key

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not path.startswith("/") or ".." in path:
            raise ValueError("invalid Vultr API path")
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(
            API_BASE + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        # Only idempotent GETs retry; the API returns transient 429/5xx responses.
        attempts = 4 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                with urlopen(req, timeout=30) as response:
                    body = response.read()
                break
            except HTTPError as exc:
                if attempt + 1 < attempts and (exc.code == 429 or exc.code >= 500):
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(
                    f"Vultr {method} {path.split('?')[0]} failed (HTTP {exc.code})"
                ) from None
            except URLError:
                if attempt + 1 < attempts:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(
                    f"Vultr {method} {path.split('?')[0]} failed (network)"
                ) from None
        return json.loads(body) if body else {}

    def list_all(self, path: str, field: str) -> list[dict]:
        rows: list[dict] = []
        cursor = ""
        while True:
            query = "?" + urlencode({"per_page": 500, **({"cursor": cursor} if cursor else {})})
            page = self.request("GET", path + query)
            rows.extend(page.get(field, []))
            next_link = page.get("meta", {}).get("links", {}).get("next") or ""
            cursor = parse_qs(urlsplit(next_link).query).get("cursor", [next_link])[0]
            if not cursor:
                return rows


def _one_named(rows: list[dict], name: str, field: str) -> dict | None:
    found = [row for row in rows if row.get(field) == name]
    if len(found) > 1:
        raise RuntimeError(f"multiple Vultr resources have the name {name}")
    return found[0] if found else None


def ensure_vpc(api: VultrAPI, config: Config) -> dict:
    name = resource_name(config, "private")
    existing = _one_named(api.list_all("/vpcs", "vpcs"), name, "description")
    if existing:
        if (
            existing.get("region") != config.region
            or existing.get("v4_subnet") != "10.42.0.0"
            or existing.get("v4_subnet_mask") != 24
        ):
            raise RuntimeError("existing VPC address range or region differs from plan")
        return existing
    return api.request(
        "POST",
        "/vpcs",
        {
            "region": config.region,
            "description": name,
            "v4_subnet": "10.42.0.0",
            "v4_subnet_mask": 24,
        },
    )["vpc"]


def ensure_ssh_key(api: VultrAPI, config: Config, public_key_path: Path) -> dict:
    name = resource_name(config, "operator-ssh")
    public_key = public_key_path.read_text().strip()
    if not public_key.startswith("ssh-ed25519 "):
        raise ValueError("operator SSH public key must use Ed25519")
    existing = _one_named(api.list_all("/ssh-keys", "ssh_keys"), name, "name")
    if existing:
        if existing.get("ssh_key", "").split()[:2] != public_key.split()[:2]:
            raise RuntimeError("existing operator SSH key differs from supplied public key")
        return existing
    return api.request("POST", "/ssh-keys", {"name": name, "ssh_key": public_key})["ssh_key"]


def ensure_firewall(api: VultrAPI, config: Config) -> dict:
    name = resource_name(config, "deny-inbound")
    existing = _one_named(api.list_all("/firewalls", "firewall_groups"), name, "description")
    group = existing or api.request("POST", "/firewalls", {"description": name})["firewall_group"]
    rules = api.list_all(f"/firewalls/{group['id']}/rules", "firewall_rules")
    if rules:
        raise RuntimeError("planned zero-inbound firewall group contains rules")
    return group


def ensure_instance(
    api: VultrAPI,
    config: Config,
    role: str,
    vpc_id: str,
    firewall_id: str,
    setup_key: str,
) -> dict:
    name = resource_name(config, role)
    existing = _one_named(api.list_all("/instances", "instances"), name, "label")
    if existing:
        if (existing.get("region"), existing.get("plan"), existing.get("os_id")) != (
            config.region,
            config.plan,
            2284,
        ):
            raise RuntimeError(f"existing {role} instance differs from plan")
        if OWNER_TAG not in existing.get("tags", []):
            raise RuntimeError(f"existing {role} instance lacks the ownership tag")
        instance = existing
        if instance.get("firewall_group_id") != firewall_id:
            api.request("PATCH", f"/instances/{instance['id']}", {"firewall_group_id": firewall_id})
            instance["firewall_group_id"] = firewall_id
    else:
        payload = {
            "region": config.region,
            "plan": config.plan,
            "os_id": 2284,
            "label": name,
            "hostname": name,
            "tags": [OWNER_TAG],
            "user_data": base64.b64encode(cloud_init(role, setup_key).encode()).decode(),
            "attach_vpc": [vpc_id],
            "firewall_group_id": firewall_id,
            "sshkey_id": [config.ssh_key_id],
            "activation_email": False,
        }
        instance = api.request("POST", "/instances", payload)["instance"]
    attached = api.request("GET", f"/instances/{instance['id']}/vpcs").get("vpcs", [])
    attached_ids = {vpc.get("id") for vpc in attached}
    if vpc_id not in attached_ids:
        api.request("POST", f"/instances/{instance['id']}/vpcs/attach", {"vpc_id": vpc_id})
    for _ in range(12):
        attached = api.request("GET", f"/instances/{instance['id']}/vpcs").get("vpcs", [])
        if vpc_id in {vpc.get("id") for vpc in attached}:
            break
        time.sleep(5)
    else:
        raise RuntimeError(f"{role} instance did not attach to VPC")
    return instance


def ensure_storage(api: VultrAPI, config: Config) -> dict:
    name = resource_name(config, "bronze")
    existing = _one_named(api.list_all("/object-storage", "object_storages"), name, "label")
    if existing:
        listed_tier = existing.get("tier_id") or (existing.get("tier") or {}).get("OBJSTORETIERID")
        if existing.get("region") != config.region or listed_tier != config.object_tier_id:
            raise RuntimeError("existing Object Storage subscription differs from plan")
        return existing
    clusters = [
        item
        for item in api.list_all("/object-storage/clusters", "clusters")
        if item.get("region") == config.region
        and item.get("deploy") == "yes"
        and (config.object_cluster_id is None or item.get("id") == config.object_cluster_id)
    ]
    eligible = [
        cluster
        for cluster in clusters
        if any(
            tier.get("id") == config.object_tier_id
            for tier in api.request("GET", f"/object-storage/clusters/{cluster['id']}/tiers").get(
                "tiers", []
            )
        )
    ]
    if len(eligible) != 1:
        raise RuntimeError("expected one cluster with the requested Object Storage tier")
    return api.request(
        "POST",
        "/object-storage",
        {"label": name, "cluster_id": eligible[0]["id"], "tier_id": config.object_tier_id},
    )["object_storage"]


def ensure_bucket(api: VultrAPI, storage: dict, name: str) -> dict:
    """Use Vultr's bucket API; pass S3 keys only to the ignored .env file."""
    storage_id = storage["id"]
    for _ in range(30):
        current = api.request("GET", f"/object-storage/{storage_id}")["object_storage"]
        if current.get("status") == "active":
            break
        time.sleep(4)
    else:
        raise RuntimeError("Object Storage subscription did not become active")
    endpoint = "https://" + current["s3_hostname"]
    buckets = api.request("GET", f"/object-storage/{storage_id}/bucket").get("buckets", [])
    if not any(bucket.get("name") == name for bucket in buckets):
        api.request("POST", f"/object-storage/{storage_id}/bucket", {"name": name})
    return {
        "endpoint": endpoint,
        "access_key": current["s3_access_key"],
        "secret_key": current["s3_secret_key"],
    }


def ensure_inference(api: VultrAPI, config: Config) -> dict:
    """Use the account API; the subscription key is separate from the account key."""
    name = resource_name(config, "inference")
    listed = api.request("GET", "/inference").get("subscriptions", [])
    existing = _one_named(listed, name, "label")
    if existing is None:
        existing = api.request("POST", "/inference", {"label": name})["subscription"]
    full = api.request("GET", f"/inference/{existing['id']}")["subscription"]
    if not full.get("api_key"):
        raise RuntimeError("Serverless Inference subscription has no API key yet")
    return full


def persist_env(path: Path, values: dict[str, str]) -> None:
    """Atomically update only the ignored .env file; never print its contents."""
    if path.name != ".env" or path.is_symlink():
        raise ValueError("credentials may only be written to a non-symlink .env file")
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("credential values must be single-line")
    current = path.read_text() if path.exists() else ""
    lines = [
        line
        for line in current.splitlines()
        if not any(line.startswith(key + "=") for key in values)
    ]
    lines.extend(f"{key}={value}" for key, value in values.items())
    cache = path.parent / ".cache"
    cache.mkdir(mode=0o700, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix="env-", dir=cache)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def apply(
    config: Config,
    api: VultrAPI,
    env_file: Path | None = None,
    setup_key: str = "",
    public_key_path: Path | None = None,
    sandbox_setup_key: str = "",
) -> dict:
    if not setup_key:
        raise ValueError("NETBIRD_SETUP_KEY is required for apply")
    firewall = ensure_firewall(api, config)
    vpc = ensure_vpc(api, config)
    if not config.ssh_key_id:
        if public_key_path is None:
            raise ValueError("--ssh-key-id or --ssh-public-key is required for apply")
        ssh_key = ensure_ssh_key(api, config, public_key_path)
        config = replace(config, ssh_key_id=ssh_key["id"])
    control = ensure_instance(api, config, "control", vpc["id"], firewall["id"], setup_key)
    sandbox = ensure_instance(
        api, config, "sandbox", vpc["id"], firewall["id"], sandbox_setup_key or setup_key
    )
    storage = ensure_storage(api, config)
    bucket = ensure_bucket(api, storage, config.bucket)
    inference = ensure_inference(api, config) if config.create_inference else None
    if env_file is not None:
        keys = {
            "AWS_ACCESS_KEY_ID": bucket["access_key"],
            "AWS_SECRET_ACCESS_KEY": bucket["secret_key"],
        }
        if inference is not None:
            keys["VULTR_INFERENCE_API_KEY"] = inference["api_key"]
        persist_env(env_file, keys)
    return {
        "firewall_group_id": firewall["id"],
        "public_inbound_rules": 0,
        "vpc_id": vpc["id"],
        "ssh_key_id": config.ssh_key_id,
        "control_id": control["id"],
        "sandbox_id": sandbox["id"],
        "object_storage_id": storage["id"],
        "object_storage_endpoint": bucket["endpoint"],
        "bucket": config.bucket,
        "inference_id": inference["id"] if inference is not None else None,
        "credentials_written": env_file is not None,
        "next": "find both peer IPs in NetBird Cloud, then run bootstrap over the overlay",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "apply"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--region", default="ewr")
    parser.add_argument("--plan", default="vx1-g-2c-8g-120s")
    parser.add_argument("--prefix", default="ontofill")
    parser.add_argument("--bucket", default="ontofill-bronze-example")
    parser.add_argument("--ssh-key-id")
    parser.add_argument("--ssh-public-key", type=Path)
    parser.add_argument("--object-tier-id", type=int, default=2)
    parser.add_argument("--object-cluster-id", type=int)
    parser.add_argument("--create-inference-subscription", action="store_true")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    config = Config(
        region=args.region,
        plan=args.plan,
        prefix=args.prefix,
        bucket=args.bucket,
        ssh_key_id=args.ssh_key_id,
        object_tier_id=args.object_tier_id,
        object_cluster_id=args.object_cluster_id,
        create_inference=args.create_inference_subscription,
    )
    if args.mode == "plan" or args.dry_run:
        print(json.dumps({**plan(config), "config": asdict(config)}, indent=2))
        return 0
    if args.bucket == "ontofill-bronze-example":
        parser.error("apply requires an explicit --bucket")
    from dotenv import load_dotenv

    env_file = args.env_file or Path(__file__).resolve().parents[2] / ".env"
    if env_file.name != ".env" or env_file.is_symlink():
        parser.error("--env-file must reference a non-symlink .env file")
    load_dotenv(env_file)
    result = apply(
        config,
        VultrAPI(os.environ.get("VULTR_API_KEY", "")),
        env_file,
        os.environ.get("NETBIRD_CONTROL_SETUP_KEY") or os.environ.get("NETBIRD_SETUP_KEY", ""),
        args.ssh_public_key,
        os.environ.get("NETBIRD_SANDBOX_SETUP_KEY", ""),
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
