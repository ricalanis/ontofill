# Vultr two-VM deployment

This is the C4c deployment and recovery path. The `plan` modes read no credentials, make no API calls and print no secrets. Live state and the account-limit pause are recorded in [NOTES.md](NOTES.md).

```sh
uv run python -m infra.vultr.provision plan --region ewr --bucket example-unique-bucket
```

The ignored `.env` holds `VULTR_API_KEY` and `NETBIRD_API_TOKEN`. Prepare separate one-use setup keys and peer groups first:

```sh
uv run python -m infra.vultr.netbird plan
uv run python -m infra.vultr.netbird prepare --env-file /path/to/ontofill/.env
```

After Vultr clears the account admission gate, resume `apply` with the same exact names and bucket. It reuses the existing tagged control VM and operator SSH public key:

```sh
uv run python -m infra.vultr.provision apply \
  --region ewr --plan vx1-g-2c-8g-120s --object-cluster-id 2 \
  --bucket ontofill-bronze-c4c-20260926 \
  --ssh-public-key /path/to/id_ed25519.pub \
  --env-file /path/to/ontofill/.env
```

`apply` reuses exactly named VPC, zero-inbound firewall group, VX1 instances and Object Storage subscription. The ownership marker `ontofill-hackathon` appears in every resource name and both instance API tags. It rejects ambiguous names, unexpected firewall rules and plan drift. It creates the bucket when absent and writes S3 keys to the ignored `.env` with mode 0600. It never prints keys. The existing Serverless Inference subscription is reused by default; `--create-inference-subscription` is optional. Ubuntu 24.04 is OS ID `2284`. Both VMs boot Docker and enroll in separate NetBird peer groups. Cloud-init resets preexisting UFW rules, then allows SSH only on `wt0`; the Vultr firewall group has no inbound rules. The sandbox installs gVisor `runsc`, checks `/dev/kvm`, and blocks container access to metadata (`169.254.169.254`) and the deployment VPC (`10.42.0.0/24`). New container connections to NetBird peers (`100.64.0.0/10`) are blocked while reply traffic remains allowed. Remote agent pods request `runsc`. One-use setup keys are present in Vultr user data for first boot; revoke them after enrollment.

Once cloud-init completes, find the two peer IPs in NetBird Cloud from the already enrolled operator Mac. Trust each VM's SSH host key in the operator's `known_hosts`, then run:

```sh
uv run python -m infra.vultr.bootstrap \
  --control-peer-ip CONTROL_NETBIRD_IP --sandbox-peer-ip SANDBOX_NETBIRD_IP \
  --identity-file PATH_TO_OPERATOR_SSH_PRIVATE_KEY
```

The bootstrap verifies the peers and a direct NetBird P2P connection. It creates a dedicated SSH key **on control VM #1** and installs only its public half on sandbox VM #2. It pins the sandbox's host key for its NetBird IP and confirms control can reach sandbox Docker over that IP. The output gives `ONTOFILL_SANDBOX_DOCKER_HOST=ssh://root@SANDBOX_NETBIRD_IP` and `DOCKER_SSH_COMMAND`. Set both on control VM #1 when running the engine. Sandbox capture transfers output with `docker cp` from VM #2; the control VM stores it in the configured lake. Do not expose Docker's TCP socket or bind a public sandbox port.

The S3 endpoint and bucket from apply belong in the app-owned `lake.yaml` (`bronze.kind: s3`); the script writes `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` to ignored `.env` for the operator to deploy to control VM #1. After both peers are connected and SSH access is verified, disable the built-in NetBird `Default` all-to-all policy, then verify the one-way admin and control-to-sandbox policies. The app URL and app-side access gate are owned by Claude PA under `proveedor-abierto/deploy`. The `approvers` SSO group is a NetBird group assigned to the account owner's user `auto_groups`; this remains a resume step.

Teardown has a credential-free plan and an explicit apply gate. It deletes only instances carrying the ownership tag and exact-name resources with no API tag field. It refuses shared VPC/firewall links or extra buckets:

```sh
uv run python -m infra.vultr.teardown plan
uv run python -m infra.vultr.teardown apply --region ewr \
  --bucket YOUR_UNIQUE_BUCKET --confirm-tag ontofill-hackathon
```

API and runtime references: [Vultr VX1 provisioning](https://docs.vultr.com/products/compute/instances/vx1-cloud-compute/provisioning), [Vultr VPC](https://docs.vultr.com/reference/terraform/resources/vpc), [Vultr Object Storage](https://docs.vultr.com/public/doc-assets/pdfs/collection_item/products-cloud-storage-object-storage.pdf), [NetBird setup keys](https://docs.netbird.io/api/resources/setup-keys), [NetBird groups](https://docs.netbird.io/api/resources/groups), [gVisor installation](https://gvisor.dev/docs/user_guide/install/).
