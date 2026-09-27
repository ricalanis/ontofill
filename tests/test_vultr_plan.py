"""Provisioning tests never contact Vultr or NetBird."""

from __future__ import annotations

import base64
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from infra.vultr import bootstrap, netbird, provision, teardown


def config() -> provision.Config:
    return provision.Config(
        region="ewr",
        plan="vx1-g-4c-16g-240s",
        prefix="ontofill-test",
        bucket="synthetic-ontofill-test-bucket",
        ssh_key_id="synthetic-ssh-id",
        object_tier_id=2,
        create_inference=True,
    )


def test_dry_run_uses_no_credentials_or_network(monkeypatch, capsys) -> None:
    monkeypatch.setenv("VULTR_API_KEY", "SYNTHETIC_SECRET_MUST_NOT_PRINT")
    monkeypatch.setenv("NETBIRD_SETUP_KEY", "ANOTHER_SYNTHETIC_SECRET")
    monkeypatch.setattr(provision, "VultrAPI", lambda _key: pytest.fail("called API"))
    assert provision.main(["plan", "--bucket", "synthetic-ontofill-test-bucket"]) == 0
    output = capsys.readouterr().out
    assert "SYNTHETIC_SECRET" not in output
    data = json.loads(output)
    assert [item["kind"] for item in data["resources"]] == [
        "firewall_group",
        "vpc",
        "ssh_key",
        "instance",
        "instance",
        "object_storage",
    ]
    assert data["resources"][4]["bootstrap"] == [
        "docker",
        "runsc",
        "netbird",
        "/dev/kvm check",
    ]
    assert all(provision.OWNER_TAG in item["name"] for item in data["resources"])
    assert data["resources"][4]["tags"] == [provision.OWNER_TAG]
    assert provision.main(["apply", "--dry-run", "--bucket", "synthetic-ontofill-test-bucket"]) == 0


def test_cloud_init_joins_netbird_and_installs_runsc_without_public_ssh() -> None:
    cloud = yaml.safe_load(provision.cloud_init("sandbox", "synthetic-setup-key"))
    script = base64.b64decode(cloud["write_files"][0]["content"]).decode()
    assert "VULTR_API_KEY" not in script
    assert "synthetic-setup-key" not in script
    assert base64.b64decode(cloud["write_files"][1]["content"]).decode() == ("synthetic-setup-key")
    assert cloud["write_files"][1]["permissions"] == "0600"
    assert "netbird up --setup-key-file /run/ontofill-netbird-setup-key" in script
    assert "runsc install" in script
    assert "--runtime=runsc" in script
    assert "config['dns'] = ['108.61.10.10']" in script
    dns_script = script.split("python3 - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
    compile(dns_script, "cloud-init-docker-dns", "exec")
    assert "/dev/kvm" in script
    assert script.index("ufw --force reset") < script.index("ufw default deny incoming")
    assert "ufw allow in on wt0 to any port 22" in script
    assert "ufw allow from" not in script
    assert "169.254.169.254/32" in script
    assert "10.42.0.0/24" in script
    assert "100.64.0.0/10" in script
    assert "100.64.0.0/10 -m conntrack --ctstate NEW -j DROP" in script
    assert "ontofill-container-egress-guard.service" in script
    assert (
        subprocess.run(
            ["bash", "-n"], input=script, text=True, capture_output=True, check=False
        ).returncode
        == 0
    )
    assert (
        "runsc install"
        not in base64.b64decode(
            yaml.safe_load(provision.cloud_init("control", "synthetic-setup-key"))["write_files"][
                0
            ]["content"]
        ).decode()
    )


class FakeAPI:
    def __init__(self) -> None:
        self.vpc: list[dict] = []
        self.ssh_keys: list[dict] = []
        self.firewalls: list[dict] = []
        self.firewall_rules: list[dict] = []
        self.instances: list[dict] = []
        self.storages: list[dict] = []
        self.buckets: list[dict] = []
        self.instance_vpcs: dict[str, list[dict]] = {}
        self.inferences: list[dict] = []
        self.created: list[tuple[str, dict]] = []

    def list_all(self, path: str, field: str) -> list[dict]:
        if path == "/vpcs":
            return self.vpc
        if path == "/ssh-keys":
            return self.ssh_keys
        if path == "/firewalls":
            return self.firewalls
        if path.endswith("/rules"):
            return self.firewall_rules
        if path == "/instances":
            return self.instances
        if path == "/object-storage":
            return self.storages
        if path == "/object-storage/clusters":
            return [{"id": 7, "region": "ewr", "deploy": "yes"}]
        raise AssertionError(path)

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if method == "GET" and path == "/inference":
            return {"subscriptions": self.inferences}
        if method == "GET" and path.startswith("/instances/") and path.endswith("/vpcs"):
            instance_id = path.split("/")[2]
            return {"vpcs": self.instance_vpcs.get(instance_id, [])}
        if method == "GET" and path.startswith("/object-storage/clusters/"):
            return {"tiers": [{"id": 2}]}
        if method == "GET" and path.startswith("/inference/"):
            return {"subscription": self.inferences[0]}
        if method == "GET" and path == "/object-storage/storage-id":
            return {
                "object_storage": {
                    **self.storages[0],
                    "status": "active",
                    "s3_hostname": "synthetic.example.test",
                    "s3_access_key": "synthetic-access",
                    "s3_secret_key": "synthetic-secret",
                }
            }
        if method == "GET" and path == "/object-storage/storage-id/bucket":
            return {"buckets": self.buckets}
        assert method == "POST", (method, path)
        assert payload is not None
        self.created.append((path, payload))
        if path == "/vpcs":
            row = {"id": "vpc-id", **payload}
            self.vpc.append(row)
            return {"vpc": row}
        if path == "/ssh-keys":
            row = {"id": "ssh-id", **payload}
            self.ssh_keys.append(row)
            return {"ssh_key": row}
        if path == "/firewalls":
            row = {"id": "firewall-id", **payload}
            self.firewalls.append(row)
            return {"firewall_group": row}
        if path == "/instances":
            row = {"id": payload["label"] + "-id", **payload, "main_ip": "192.0.2.5"}
            self.instances.append(row)
            self.instance_vpcs[row["id"]] = [{"id": vpc} for vpc in payload.get("attach_vpc", [])]
            return {"instance": row}
        if path.endswith("/vpcs/attach"):
            instance_id = path.split("/")[2]
            self.instance_vpcs.setdefault(instance_id, []).append({"id": payload["vpc_id"]})
            return {}
        if path == "/object-storage":
            row = {"id": "storage-id", "region": "ewr", **payload}
            self.storages.append(row)
            return {"object_storage": row}
        if path == "/inference":
            row = {"id": "inference-id", "api_key": "synthetic-inference-key", **payload}
            self.inferences.append(row)
            return {"subscription": row}
        if path == "/object-storage/storage-id/bucket":
            self.buckets.append(payload)
            return {}
        raise AssertionError(path)


def test_apply_reuses_resources_on_second_run() -> None:
    api = FakeAPI()
    first = provision.apply(config(), api, setup_key="synthetic-setup-key")
    count = len(api.created)
    second = provision.apply(config(), api, setup_key="synthetic-setup-key")
    assert first == second
    assert len(api.created) == count
    assert count == 7  # firewall, VPC, two VMs, storage, bucket, inference
    assert all(row["os_id"] == 2284 for row in api.instances)
    assert all(row["firewall_group_id"] == "firewall-id" for row in api.instances)
    assert all(row["sshkey_id"] == ["synthetic-ssh-id"] for row in api.instances)
    assert all(row["tags"] == [provision.OWNER_TAG] for row in api.instances)
    assert all(row["attach_vpc"] == ["vpc-id"] for row in api.instances)
    assert all("vpc2_ids" not in row and "ssh_key_ids" not in row for row in api.instances)
    assert first["bucket"] == "synthetic-ontofill-test-bucket"
    assert first["inference_id"] == "inference-id"
    assert "synthetic-secret" not in json.dumps(first)


def test_operator_ssh_key_is_created_once_from_public_half(tmp_path) -> None:
    path = tmp_path / "id_ed25519.pub"
    path.write_text("ssh-ed25519 SYNTHETIC_PUBLIC_KEY operator@example.test\n")
    api = FakeAPI()
    first = provision.ensure_ssh_key(api, config(), path)
    second = provision.ensure_ssh_key(api, config(), path)
    assert first == second
    assert first["id"] == "ssh-id"
    assert len(api.ssh_keys) == 1


def test_persist_env_replaces_keys_without_printing_or_loosening_permissions(tmp_path) -> None:
    path = tmp_path / ".env"
    path.write_text("VULTR_API_KEY=synthetic-account-key\nVULTR_INFERENCE_API_KEY=old\n")
    provision.persist_env(path, {"VULTR_INFERENCE_API_KEY": "synthetic-inference-key"})
    contents = path.read_text()
    assert "VULTR_API_KEY=synthetic-account-key" in contents
    assert contents.count("VULTR_INFERENCE_API_KEY=") == 1
    assert "VULTR_INFERENCE_API_KEY=synthetic-inference-key" in contents
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_bootstrap_verifies_peer_identity(monkeypatch) -> None:
    calls = []

    def fake_ssh(address, command, *, input_text="", identity_file=None):
        calls.append((address, command, input_text))
        return "100.64.0.2" if "--ipv4" in command else ""

    monkeypatch.setattr(bootstrap, "ssh", fake_ssh)
    peer_ip = bootstrap.verify_peer("100.64.0.2")
    assert peer_ip == "100.64.0.2"
    assert all(input_text == "" for _, _, input_text in calls)


def test_bootstrap_configures_docker_ssh_identity(monkeypatch) -> None:
    calls = []

    def fake_ssh(address, command, *, input_text="", identity_file=None):
        calls.append((address, command, input_text))
        if command.endswith("ontofill_sandbox.pub"):
            return "ssh-ed25519 SYNTHETIC_PUBLIC_KEY control"
        if command == "cat /etc/ssh/ssh_host_ed25519_key.pub":
            return "ssh-ed25519 SYNTHETIC_HOST_KEY sandbox"
        return ""

    monkeypatch.setattr(bootstrap, "ssh", fake_ssh)
    assert bootstrap.link_docker_ssh("100.64.0.1", "100.64.0.2", "100.64.0.2") == (
        "ssh://root@100.64.0.2"
    )
    config = next(
        (command, data) for _, command, data in calls if "cat >> /root/.ssh/config" in command
    )
    assert "grep -qxF 'Host 100.64.0.2'" in config[0]
    assert "IdentityFile /root/.ssh/ontofill_sandbox" in config[1]
    assert "IdentitiesOnly yes" in config[1]
    assert "StrictHostKeyChecking yes" in config[1]
    assert any("docker --host ssh://root@100.64.0.2 info" in command for _, command, _ in calls)


def test_bootstrap_persists_verified_sandbox_host_in_engine_env(monkeypatch) -> None:
    calls = []

    def fake_ssh(address, command, *, input_text="", identity_file=None):
        calls.append((address, command, input_text))
        return "configured"

    monkeypatch.setattr(bootstrap, "ssh", fake_ssh)
    assert bootstrap.configure_engine_docker_host("100.64.0.1", "ssh://root@100.64.0.2")
    assert calls[0][0] == "100.64.0.1"
    assert calls[0][2] == "ssh://root@100.64.0.2\n"
    assert "ONTOFILL_SANDBOX_DOCKER_HOST=%s" in calls[0][1]
    assert "chmod 600 /opt/ontofill/engine.env" in calls[0][1]


def test_existing_firewall_with_rules_is_rejected() -> None:
    api = FakeAPI()
    api.firewalls.append(
        {"id": "firewall-id", "description": provision.resource_name(config(), "deny-inbound")}
    )
    api.firewall_rules.append({"id": 1, "port": "22"})
    with pytest.raises(RuntimeError, match="contains rules"):
        provision.ensure_firewall(api, config())


def test_teardown_selects_only_owned_resources_and_rejects_shared_links() -> None:
    api = FakeAPI()
    provision.apply(config(), api, setup_key="synthetic-setup-key")
    selected = teardown.owned_resources(api, config())
    assert [kind for kind, _ in selected] == [
        "instance",
        "instance",
        "object_storage",
        "serverless_inference",
        "vpc",
        "firewall_group",
    ]
    api.instances.append({"id": "unrelated-id", "label": "unrelated"})
    api.instance_vpcs["unrelated-id"] = [{"id": "vpc-id"}]
    with pytest.raises(RuntimeError, match="outside this deployment"):
        teardown.owned_resources(api, config())
    api.instances.pop()
    api.instance_vpcs.pop("unrelated-id")
    api.instances[0]["tags"] = []
    with pytest.raises(RuntimeError, match="lacks ownership tag"):
        teardown.owned_resources(api, config())


def test_teardown_plan_needs_no_credentials(monkeypatch, capsys) -> None:
    monkeypatch.setenv("VULTR_API_KEY", "SYNTHETIC_SECRET_MUST_NOT_PRINT")
    monkeypatch.setattr(teardown, "VultrAPI", lambda _key: pytest.fail("called API"))
    assert teardown.main(["plan"]) == 0
    output = capsys.readouterr().out
    assert "SYNTHETIC_SECRET" not in output
    assert json.loads(output)["owner_tag"] == provision.OWNER_TAG


class FakeNetBird:
    def __init__(self) -> None:
        self.groups: list[dict] = []
        self.policies: list[dict] = []
        self.keys: list[dict] = []

    def request(self, method: str, path: str, payload: dict | None = None):
        if method == "GET":
            return {
                "/peers": [{"id": "mac-id", "os": "Darwin", "connected": True}],
                "/groups": self.groups,
                "/policies": self.policies,
                "/setup-keys": self.keys,
            }[path]
        assert method == "POST"
        if path == "/groups":
            row = {"id": f"group-{len(self.groups)}", **payload}
            row["peers"] = [{"id": peer} for peer in payload["peers"]]
            self.groups.append(row)
            return row
        if path == "/policies":
            row = {"id": f"policy-{len(self.policies)}", **payload}
            self.policies.append(row)
            return row
        if path == "/setup-keys":
            row = {"id": f"key-{len(self.keys)}", "valid": True, "key": "synthetic-key", **payload}
            self.keys.append(row)
            return row
        raise AssertionError(path)


def test_netbird_prepare_scopes_one_use_keys_and_admin_access(tmp_path) -> None:
    api = FakeNetBird()
    env_file = tmp_path / ".env"
    env_file.write_text("NETBIRD_API_TOKEN=synthetic-token\n")
    result = netbird.prepare(api, env_file)
    assert result["setup_keys_written"]
    assert len(api.groups) == 3
    assert api.groups[2]["peers"] == [{"id": "mac-id"}]
    assert len(api.policies) == 2
    assert all(policy["rules"][0]["ports"] == ["22"] for policy in api.policies)
    assert all(not policy["rules"][0]["bidirectional"] for policy in api.policies)
    assert all(key["usage_limit"] == 1 and key["auto_groups"] for key in api.keys)
    assert "NETBIRD_CONTROL_SETUP_KEY=synthetic-key" in env_file.read_text()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    api.groups[0]["peers"] = [{"id": "vm-control", "name": "ontofill-control"}]
    api.keys[0]["valid"] = False
    api.keys[0]["used_times"] = 1
    assert netbird.prepare(api, env_file)["setup_keys_written"]
    assert len(api.keys) == 2
