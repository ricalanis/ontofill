"""Delete only this deployment's explicitly owned Vultr resources."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from infra.vultr.provision import OWNER_TAG, Config, VultrAPI, _one_named, resource_name


def plan(prefix: str) -> dict:
    """Credential-free summary. No API calls or secrets."""
    config = Config("ewr", "vx1-g-4c-16g-240s", prefix, "synthetic-bucket", None, 2)
    return {
        "mode": "dry-run",
        "owner_tag": OWNER_TAG,
        "delete_order": [
            {"kind": "instance", "name": resource_name(config, role)}
            for role in ("control", "sandbox")
        ]
        + [
            {"kind": kind, "name": resource_name(config, suffix)}
            for kind, suffix in (
                ("object_storage", "bronze"),
                ("serverless_inference", "inference"),
                ("vpc", "private"),
                ("firewall_group", "deny-inbound"),
                ("ssh_key", "operator-ssh"),
            )
        ],
        "safety": "apply requires --confirm-tag and validates exact names, instance tags and shared attachments",
    }


def owned_resources(api: VultrAPI, config: Config) -> list[tuple[str, dict]]:
    """Fail closed if any named resource could be shared or lacks ownership evidence."""
    instances = api.list_all("/instances", "instances")
    selected: list[tuple[str, dict]] = []
    instance_ids: set[str] = set()
    for role in ("control", "sandbox"):
        row = _one_named(instances, resource_name(config, role), "label")
        if row is not None:
            if OWNER_TAG not in row.get("tags", []):
                raise RuntimeError(f"{role} instance lacks ownership tag")
            selected.append(("instance", row))
            instance_ids.add(row["id"])

    storage = _one_named(
        api.list_all("/object-storage", "object_storages"),
        resource_name(config, "bronze"),
        "label",
    )
    if storage is not None:
        buckets = api.request("GET", f"/object-storage/{storage['id']}/bucket").get("buckets", [])
        if any(bucket.get("name") != config.bucket for bucket in buckets):
            raise RuntimeError("Object Storage has a bucket outside this deployment")
        selected.append(("object_storage", storage))

    inference = _one_named(
        api.request("GET", "/inference").get("subscriptions", []),
        resource_name(config, "inference"),
        "label",
    )
    if inference is not None:
        selected.append(("serverless_inference", inference))

    vpc = _one_named(api.list_all("/vpcs", "vpcs"), resource_name(config, "private"), "description")
    if vpc is not None:
        attached = {
            instance["id"]
            for instance in instances
            if vpc["id"]
            in {
                row.get("id")
                for row in api.request("GET", f"/instances/{instance['id']}/vpcs").get("vpcs", [])
            }
        }
        if not attached <= instance_ids:
            raise RuntimeError("VPC has nodes outside this deployment")
        selected.append(("vpc", vpc))

    firewall = _one_named(
        api.list_all("/firewalls", "firewall_groups"),
        resource_name(config, "deny-inbound"),
        "description",
    )
    if firewall is not None:
        users = {row["id"] for row in instances if row.get("firewall_group_id") == firewall["id"]}
        if not users <= instance_ids:
            raise RuntimeError("firewall group is attached outside this deployment")
        selected.append(("firewall_group", firewall))
    ssh_key = _one_named(
        api.list_all("/ssh-keys", "ssh_keys"), resource_name(config, "operator-ssh"), "name"
    )
    if ssh_key is not None:
        selected.append(("ssh_key", ssh_key))
    return selected


def apply(api: VultrAPI, config: Config) -> dict:
    resources = owned_resources(api, config)
    for kind, row in resources:
        if kind == "instance":
            api.request("DELETE", f"/instances/{row['id']}")
    if any(kind == "instance" for kind, _ in resources):
        for _ in range(60):
            remaining = {row["id"] for row in api.list_all("/instances", "instances")}
            if not any(row["id"] in remaining for kind, row in resources if kind == "instance"):
                break
            time.sleep(5)
        else:
            raise RuntimeError("owned instances did not finish deleting; rerun teardown")
    paths = {
        "object_storage": "/object-storage",
        "serverless_inference": "/inference",
        "vpc": "/vpcs",
        "firewall_group": "/firewalls",
        "ssh_key": "/ssh-keys",
    }
    for kind, row in resources:
        if kind in paths:
            api.request("DELETE", f"{paths[kind]}/{row['id']}")
    return {"owner_tag": OWNER_TAG, "deleted": [{"kind": k, "id": r["id"]} for k, r in resources]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "apply"))
    parser.add_argument("--prefix", default="ontofill")
    parser.add_argument("--region", default="ewr")
    parser.add_argument("--bucket", default="ontofill-bronze-example")
    parser.add_argument("--confirm-tag")
    args = parser.parse_args(argv)
    if args.mode == "plan":
        print(json.dumps(plan(args.prefix), indent=2))
        return 0
    if args.confirm_tag != OWNER_TAG or args.bucket == "ontofill-bronze-example":
        parser.error("apply requires --confirm-tag ontofill-hackathon and the exact --bucket")
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    config = Config(args.region, "vx1-g-4c-16g-240s", args.prefix, args.bucket, None, 2)
    result = apply(VultrAPI(os.environ.get("VULTR_API_KEY", "")), config)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
