"""Verify cloud-init NetBird peers and link control to sandbox over the overlay."""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import time

# Optional operator-owned known_hosts file; None keeps OpenSSH's default.
KNOWN_HOSTS_FILE: str | None = None


def ssh(
    address: str, command: str, *, input_text: str = "", identity_file: str | None = None
) -> str:
    """Invoke an already trusted SSH host; never put setup keys in argv or errors."""
    ipaddress.ip_address(address)
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"]
    if KNOWN_HOSTS_FILE:
        argv.extend(["-o", f"UserKnownHostsFile={KNOWN_HOSTS_FILE}"])
    if identity_file:
        argv.extend(["-i", identity_file])
    argv.extend([f"root@{address}", command])
    completed = subprocess.run(
        argv, input=input_text, text=True, capture_output=True, timeout=180, check=False
    )
    if completed.returncode:
        raise RuntimeError(f"SSH command failed on {address} (exit {completed.returncode})")
    return completed.stdout.strip()


def verify_peer(address: str, identity_file: str | None = None) -> str:
    ssh(address, "cloud-init status --wait >/dev/null", identity_file=identity_file)
    peer_ip = ssh(address, "netbird status --ipv4", identity_file=identity_file)
    verified = str(ipaddress.ip_address(peer_ip))
    if verified != address:
        raise RuntimeError("SSH target does not match its NetBird IP")
    return verified


def wait_for_p2p(
    control_address: str, sandbox_peer_ip: str, identity_file: str | None = None
) -> None:
    ipaddress.ip_address(sandbox_peer_ip)
    for _ in range(18):
        # Lazy connections stay Idle until traffic flows; open TCP 22 (allowed by policy) first.
        # Client 0.79 prints "Connection type: P2P" and no longer prints a "Direct:" line.
        detail = ssh(
            control_address,
            f"timeout 5 bash -c '</dev/tcp/{sandbox_peer_ip}/22' >/dev/null 2>&1; "
            f"netbird status -d --filter-by-ips {sandbox_peer_ip} --filter-by-status connected",
            identity_file=identity_file,
        )
        if "Connection type: P2P" in detail:
            return
        time.sleep(5)
    raise RuntimeError("NetBird control-to-sandbox peer is not directly connected (P2P)")


def link_docker_ssh(
    control_address: str,
    sandbox_address: str,
    sandbox_peer_ip: str,
    identity_file: str | None = None,
) -> str:
    """Create a dedicated key on VM #1; install only its public half on VM #2."""
    key_path = "/root/.ssh/ontofill_sandbox"
    ssh(
        control_address,
        "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
        "test -f /root/.ssh/ontofill_sandbox || "
        "ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/ontofill_sandbox",
        identity_file=identity_file,
    )
    public_key = ssh(control_address, f"cat {key_path}.pub", identity_file=identity_file)
    if not public_key.startswith("ssh-ed25519 "):
        raise RuntimeError("control peer returned an invalid SSH public key")
    ssh(
        sandbox_address,
        "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
        "IFS= read -r key && "
        '(grep -qxF "$key" /root/.ssh/authorized_keys 2>/dev/null || '
        "printf '%s\\n' \"$key\" >> /root/.ssh/authorized_keys) && "
        "chmod 600 /root/.ssh/authorized_keys",
        input_text=public_key + "\n",
        identity_file=identity_file,
    )
    host_key = ssh(
        sandbox_address, "cat /etc/ssh/ssh_host_ed25519_key.pub", identity_file=identity_file
    )
    parts = host_key.split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise RuntimeError("sandbox peer returned an invalid SSH host key")
    known_host_line = f"{sandbox_peer_ip} {parts[0]} {parts[1]}"
    ssh(
        control_address,
        "touch /root/.ssh/known_hosts && chmod 600 /root/.ssh/known_hosts && "
        "IFS= read -r line && "
        '(grep -qxF "$line" /root/.ssh/known_hosts || '
        "printf '%s\\n' \"$line\" >> /root/.ssh/known_hosts)",
        input_text=known_host_line + "\n",
        identity_file=identity_file,
    )
    ssh(
        control_address,
        f"ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -i {key_path} "
        f"root@{sandbox_peer_ip} 'docker info --format {{{{.Name}}}}' >/dev/null",
        identity_file=identity_file,
    )
    return f"ssh://root@{sandbox_peer_ip}"


def bootstrap(
    control_address: str,
    sandbox_address: str,
    identity_file: str | None = None,
) -> dict:
    control_peer_ip = verify_peer(control_address, identity_file)
    sandbox_peer_ip = verify_peer(sandbox_address, identity_file)
    wait_for_p2p(control_address, sandbox_peer_ip, identity_file)
    docker_host = link_docker_ssh(control_address, sandbox_address, sandbox_peer_ip, identity_file)
    return {
        "control_netbird_ip": control_peer_ip,
        "sandbox_netbird_ip": sandbox_peer_ip,
        "netbird_connection": "P2P",
        "ONTOFILL_SANDBOX_DOCKER_HOST": docker_host,
        "DOCKER_SSH_COMMAND": "ssh -i /root/.ssh/ontofill_sandbox -o StrictHostKeyChecking=yes",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-peer-ip", required=True)
    parser.add_argument("--sandbox-peer-ip", required=True)
    parser.add_argument("--identity-file")
    parser.add_argument("--known-hosts-file", help="pinned host keys; default ~/.ssh/known_hosts")
    args = parser.parse_args(argv)
    global KNOWN_HOSTS_FILE
    KNOWN_HOSTS_FILE = args.known_hosts_file
    result = bootstrap(
        args.control_peer_ip,
        args.sandbox_peer_ip,
        args.identity_file,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
