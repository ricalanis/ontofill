# L2 deployment checkpoint

The first control VM is active with Docker and NetBird, and SSH over NetBird
works. The tagged private network, zero-inbound firewall group, and operator
public key also exist. A sandbox VM and Object Storage subscription have not
been created. `teardown.owned_resources` selects exactly the four existing
tagged resources without deleting them.

A second VM creation returned HTTP 400. A previously inspected response
reported an account monthly-fee admission limit. The response body for the
latest attempt was intentionally suppressed, so its specific cause is unknown.
Stop create retries and ask Vultr to review the account admission gate before
resuming. The current control VM should remain in place.

The control peer joined its dedicated NetBird group. The sandbox setup key is
unused. Two one-way SSH policies exist, but the built-in `Default` all-to-all
policy remains enabled until both peers and the dispatch path can be verified.
The approver SSO group still needs account-side setup: NetBird proxy
distribution groups use a NetBird user's `auto_groups`, which can be updated
through `/api/users/{id}`.

An external probe initially found public TCP 22 reachable on control because
the Ubuntu image had preexisting UFW rules. Only generic SSH allow rules were
removed over NetBird SSH, leaving the `wt0` rule. A subsequent external probe
found TCP 22, 80, and 443 closed, and NetBird SSH remained available. Future
cloud-init resets UFW before applying the `wt0` rule.

After Vultr clears the admission gate, rerun `provision apply` with the same
names, plan, and bucket. It reuses tagged resources. Verify the sandbox peer,
`/dev/kvm`, `runsc`, Chromium/Playwright, external port closure, direct
NetBird connectivity, and all five sandbox proof checkpoints. Revoke one-use
setup keys after both peers enroll; then disable `Default` and verify the
one-way policies. Do not destroy tagged resources just to retry admission.

The account's `/vpc2` API returned HTTP 404; `/vpcs` worked, so this deployment
uses Vultr VPC with `attach_vpc`. The Object Storage cluster is selected
deterministically by region and tier when apply resumes.

## Resume checkpoint (admission gate cleared)

`provision apply` reused the tagged control VM, VPC, firewall group and SSH
key, and created the sandbox VM and the Standard-tier Object Storage
subscription (ewr1) with the lake bucket. The sandbox enrolled with its
one-off key into `ontofill-sandbox-host`; both one-off keys are now consumed.
Cloud-init finished cleanly: `runsc` is a registered Docker runtime,
`/dev/kvm` is present, and the DOCKER-USER guard is active. From a runsc
container, metadata and NetBird peers time out while the internet is reachable.

`bootstrap` linked control to sandbox Docker over SSH on the mesh (P2P over
the VPC). The client uses lazy connections, so the P2P check first opens
TCP 22. Policies are now one-way: admins to both VMs (all), control to
sandbox (TCP 22, which carries Docker and the CDP SSH tunnel), and sandbox to
control (TCP 8700, the inference gateway only). `Default` is disabled. A probe
showed sandbox to control 22/8000 and sandbox/control to the admin peer
blocked. `approvers` exists and is on the owner user's `auto_groups`. An
external probe of both public IPs found 22, 80, 443, 8000, 8080 and 8700
closed or filtered, and the firewall group still has zero rules.
