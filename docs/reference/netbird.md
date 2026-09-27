# NetBird reference: zero-port, two-VM deployment on NetBird Cloud

For the two coding agents wiring NetBird into the Vultr deployment. The goal is copy-paste commands that work.
Checked 2026-09-26 against the docs repo (github.com/netbirdio/docs @ `9450f06`), the client and proxy source
(github.com/netbirdio/netbird @ `d5488db`), the dashboard source (github.com/netbirdio/dashboard @ `c403b8f`), and
the local client `netbird version` = 0.79.0 (`--help` output).

- The deck summary and bonus criteria are in [event-decks.md](event-decks.md) §2 and aren't repeated here.
- The Vultr side (firewall group, VPC, instance creation, cloud-init template) is in [vultr.md](vultr.md) §4, §5 and §9.
- The app's own deploy notes are in `proveedor-abierto/deploy/README.md`. Its `netbird_services.py` and
  `netbird-expose.sh` implement §5 and §6 of this file.
- **UNVERIFIED** means no public doc or source confirmed the claim. Test it before relying on it.
- **(src)** means the claim comes from reading the source at the commit above, not from the docs.
- Secrets always come from env vars: `NETBIRD_SETUP_KEY`, `NETBIRD_API_TOKEN`, `INVESTIGATOR_PASSWORD`,
  `INVESTIGATOR_PIN`. Never echo them.

Short links used below: `D:` = `https://docs.netbird.io`, `SRC:` =
`https://github.com/netbirdio/netbird/blob/d5488db146bfb66aed5d64344007911b67cb05a1`, `DASH:` =
`https://github.com/netbirdio/dashboard/blob/c403b8ff14e772818e8bccff85cc222ed2dda215`.

---

## 0. Gotchas (read first)

| # | Gotcha | Do this | Source |
|---|---|---|---|
| G1 | **The proxy dials the target peer's NetBird IP (`100.x`), not `127.0.0.1`.** A web app bound to loopback is unreachable through a reverse-proxy service or `netbird expose`. | Bind the investigator and approver to the VM's NetBird IP (`PA_BIND_IP=$(netbird status --ipv4)`). That is still not a public interface. | SRC:`management/internals/modules/reverseproxy/service/manager/manager.go` L187-194 (`target.Host = peer.IP.String()`) (src) |
| G2 | **The identity header `X-NetBird-User` is documented only for NetBird-Only (private) services**, and those are **not available on Cloud's shared clusters**. The source stamps it on every proxied request that has a user session, which should include SSO. PIN and password sessions carry no user. | Treat `X-NetBird-User` / `X-NetBird-Groups` as optional audit metadata. Never use them for authorization (see G9). | D:/manage/reverse-proxy/authentication#identity-headers-stamped-on-upstream-requests; SRC:`proxy/internal/proxy/reverseproxy.go` L909-940, `proxy/internal/auth/middleware.go` L349-383 (src) |
| G3 | **SSO "user groups" are NetBird groups assigned to the user** (the user's auto-groups), not IdP groups. | Create the `approvers` group, then add it to each approver *user* (Team → Users → user → groups). | SRC:`management/internals/shared/grpc/proxy.go` L1790-1830 (`user.AutoGroups`) (src); D:/manage/reverse-proxy/authentication#sso-single-sign-on |
| G4 | `netbird expose` **fails if Block Inbound Connections is on** for the peer. | Never pass `--block-inbound` to `netbird up` on VM #2 (or VM #1 if it exposes). | D:/manage/reverse-proxy/expose-from-cli#prerequisites; SRC:`client/server/server.go` L2128 |
| G5 | **Peer Expose is off by default.** In the dashboard, at least one allowed group is required. In the API, an empty `peer_expose_groups` means all peers. | Settings → Clients → Peer Expose → toggle, then pick `sandbox-host`. | D:/manage/reverse-proxy/expose-from-cli#enable-peer-expose; DASH:`src/modules/settings/ClientSettingsTab.tsx` L365-395 |
| G6 | **`PUT /api/accounts/{id}` requires the whole `settings` object** (login expiration, approval, traffic-log fields, and so on). The doc example sends only two fields. | GET the account, merge with jq, then PUT (§5.4). | D:/api/resources/accounts (Update an Account, required fields) |
| G7 | **The `Default` policy (All ↔ All) defeats the fence.** | Create your policies, then delete `Default`. | D:/manage/access-control |
| G8 | **Use unidirectional policies.** A *bidirectional* `laptop-admins → All` lets the sandbox host initiate to the laptop. | Set Direction to one-way (API: `"bidirectional": false`) on every policy in §2.3. | D:/manage/access-control/manage-network-access |
| G9 | Anything that reaches the backend directly (another peer on the mesh, such as a laptop admin with an ALL policy) can send its own `X-NetBird-User`. The proxy strips it only on requests that pass through the proxy. | Audit metadata only, as the app already does ("no decision depends on the header"). | D:/manage/reverse-proxy/service-configuration#net-bird-identity-headers |
| G10 | **Setup keys in `user_data` are readable at `169.254.169.254`** from inside the VM. | Use a one-off key per VM with a 1-day expiry (`expires_in` minimum is 86400) and auto-groups. Revoke it after enrollment; enrolled peers stay connected. | D:/manage/peers/register-machines-using-setup-keys; D:/api/resources/setup-keys |
| G11 | The existing reusable `NETBIRD_SETUP_KEY` has unknown auto-groups. If both VMs enroll with it, they land in the **same** groups and the fence doesn't exist yet. | Prefer two keys: `NETBIRD_SETUP_KEY_CP` and `NETBIRD_SETUP_KEY_SB`, with auto-groups `control-plane` and `sandbox-host`. Otherwise assign groups by hand right after enrollment. | same |
| G12 | Pods on VM #2 reach the mesh **as the sandbox host** (Docker masquerades their traffic to the host's `100.x`). | Add `DOCKER-USER -d 100.64.0.0/10 -m conntrack --ctstate NEW -j DROP` (§1.4). Match `NEW` only: a plain `-d 100.64.0.0/10` DROP also kills the replies from the live-view container to the proxy. | analysis; Docker masquerade behaviour |
| G13 | `netbird expose` prints its "Service exposed successfully!" block with cobra's `cmd.Println`, which goes to **stderr** unless the output is redirected. | Capture with `2>&1` (§6.3). | SRC:`client/cmd/expose.go` L252-269 (src, inferred from cobra's default) |
| G14 | The laptop peer was enrolled by SSO, so it is subject to **login (session) expiration**. Setup-key peers are exempt. | Before demo day, Peers → laptop → turn off **Session Expiration**, or log in again. | D:/manage/settings/enforce-periodic-user-authentication; D:/manage/networks |
| G15 | The Cloud reverse proxy is "shared, best-effort", and heavy streams can be rate-limited. | Keep live-view streams small (a low-fps screencast is enough). | D:/manage/reverse-proxy/service-configuration (Cloud usage limits) |
| G16 | The free domain is `{sub}.{nonce}.{cluster}.proxy.netbird.io`. `netbird expose` names are `{prefix}-{4 random chars}` (or 12 random chars without a prefix). | Read the domain from the API (`/reverse-proxies/domains`) instead of hardcoding it. | D:/manage/reverse-proxy/custom-domains; SRC:`management/internals/modules/reverseproxy/service/service.go` L1629-1650 |

## 0b. Cloud plan vs trial (the account owner started the trial)

- **Trial:** "Start 14-Day Free Trial" in **Settings → Plans & Billing**. It "temporarily unlocks NetBird's full set of features and integrations". When it ends you "return to your previous plan unless you choose to upgrade". Only the Owner or an Admin can start it. Source: D:/manage/settings/plans-and-billing.
- The Sep 26–27 event fits inside 14 days. What happens to Business-only objects (posture checks attached to policies) when the trial ends is **UNVERIFIED**.

| Feature we use | Plan per public sources | Notes |
|---|---|---|
| Reverse proxy services (dashboard/API) | **Not stated on the pricing or docs pages. UNVERIFIED.** | Beta. The open-source dashboard shows it to anyone with the Services permission (Network Admin or higher), with no plan check (DASH:`src/layouts/Navigation.tsx` L150-166). The trial covers it either way. |
| Peer Expose (`netbird expose`) | **Not stated. UNVERIFIED.** | Same as above. |
| Proxy auth: PIN, password, header | Not stated (part of the proxy) | |
| Proxy auth: SSO with user groups | Login uses the account's IdP. The Free plan supports "Google, Microsoft, and social logins"; custom OIDC IdPs and IdP group sync (SCIM) are **Team+**. | Group restriction checks NetBird groups on the user (G3), which you can assign by hand on any plan. |
| Posture checks | **Business** ("Device posture checks") | https://netbird.io/pricing; D:/manage/settings/plans-and-billing. Unlocked by the trial. |
| Audit events (shows expose sessions) | Team+ ("Audit events logging") | pricing page |
| Traffic events | Business | D:/manage/activity/traffic-events-logging |
| NetBird SSH, access policies, routes/networks | Free | pricing page |

---

## 1. Install and enroll headless Linux peers

### 1.1 Commands (Ubuntu 24.04, as root)

```bash
curl -fsSL https://pkgs.netbird.io/install.sh | sh          # D:/get-started/install/linux
netbird up --setup-key "$NETBIRD_SETUP_KEY" --hostname brz-control   # D:/manage/peers/register-machines-using-setup-keys
netbird status                                              # expect "Management: Connected", "Signal: Connected"
netbird status --ipv4                                       # prints only this peer's NetBird IPv4, e.g. 100.64.0.33
netbird status --check ready && echo ready                  # exit 0/1 health check (local 0.79 --help)
```

- **Install script.** On a headless box (no `$XDG_CURRENT_DESKTOP`) it skips the UI, installs the package, and runs `netbird service install` and `netbird service start`. The result is a systemd unit (`netbird`) that auto-starts at boot and reconnects with the saved config in `/var/lib/netbird/default.json`. SRC:`release_files/install.sh` L262-267, L391-425.
- **Setup key alternatives.** `--setup-key-file PATH` works, and so does the env var `NB_SETUP_KEY`: every global flag maps to `NB_<FLAG>` (SRC:`client/cmd/root.go` L246-267). Both keep the key off the process command line.
- **`--hostname` / `-n`** "Sets a custom hostname for the device" and becomes the peer name at registration. On Cloud the DNS name is `<name>.netbird.cloud` (D:/get-started/cli). Whether re-running `up --hostname` renames an already-registered peer is **UNVERIFIED**, so rename in the dashboard instead.
- **Rename a peer:** Dashboard → **Peers** → click the peer → edit the name. API: `PUT /api/peers/{id}` with `name`, `ssh_enabled`, `login_expiration_enabled` and `inactivity_expiration_enabled`, all required (D:/api/resources/peers).
- **Remove a peer:** `netbird deregister` on the box, or `DELETE /api/peers/{id}`.

### 1.2 Setup keys: types and auto-groups (Settings → Setup Keys)

| Option | Meaning | Use for |
|---|---|---|
| One-off | usable once | each long-lived VM (G10) |
| Reusable + usage limit | N enrollments | a burst-instance pool |
| Ephemeral | peer auto-removed after **10 min offline** | burst/throwaway VMs, never the two main VMs |
| Auto-assign groups | every peer enrolled with the key joins these groups; applies only to *new* registrations | fence from the first second (G11) |
| Expiration | dashboard pre-fills 7 days; API `expires_in` 86400–31536000 s | 1 day |

Revoking or expiring a key does **not** disconnect peers that already enrolled with it. Source: D:/manage/peers/register-machines-using-setup-keys.

### 1.3 Verdict on the vultr.md cloud-init snippet (vultr.md §9 step 5)

```yaml
  - [bash, -c, "curl -fsSL https://pkgs.netbird.io/install.sh | sh"]
  - [bash, -c, "netbird up --setup-key '__NB_SETUP_KEY__'"]
```

**Correct as far as it goes.** The install URL and `up --setup-key` match D:/get-started/install/linux and
D:/manage/peers/register-machines-using-setup-keys, and a root cloud-init context satisfies the install script. It
does have four gaps:

1. **No group separation.** One shared key means the fence is off at boot (G11). Render a different key per role.
2. **No explicit peer name.** It relies on the Vultr `hostname` (`brz-control` / `brz-sandbox`). That works, but pass
   `--hostname` so the peer name never depends on the image.
3. **The key sits in `user_data`** (G10). Use one-off, 1-day keys and revoke them after enrollment. The app README's
   "never put NetBird secrets in user_data" can't hold for the *setup key* when SSH is closed. The only other
   way in is the Vultr web console.
4. **Sandbox pods can reach the mesh** (G12). The DOCKER-USER rules drop `169.254.169.254` and the VPC, but not
   `100.64.0.0/10`.

### 1.4 Drop-in replacement lines for the cloud-init template

```yaml
  # NetBird (after Docker). __NB_SETUP_KEY__ is per-role; __NB_NAME__ is brz-control | brz-sandbox
  - [bash, -c, "curl -fsSL https://pkgs.netbird.io/install.sh | sh"]
  - [bash, -c, "NB_SETUP_KEY='__NB_SETUP_KEY__' netbird up --hostname '__NB_NAME__' --allow-server-ssh --enable-ssh-root"]
  - [bash, -c, "for i in $(seq 30); do netbird status --check ready && break; sleep 2; done; netbird status > /root/netbird-status.txt 2>&1"]
  # sandbox only: pods may not open NEW connections into the mesh (replies to the proxy still flow)
  - [bash, -c, "if [ '__ROLE__' = sandbox ]; then iptables -I DOCKER-USER -d 100.64.0.0/10 -m conntrack --ctstate NEW -j DROP; fi"]
```

- `--allow-server-ssh --enable-ssh-root` is optional (§3). Drop it if you use plain OpenSSH over the mesh.
- Do **not** add `--block-inbound` (G4).
- Extend the `render()` in vultr.md so `__NB_SETUP_KEY__` comes from `NETBIRD_SETUP_KEY_CP` or `NETBIRD_SETUP_KEY_SB` depending on the role.

---

## 2. Groups, access policies, posture checks

### 2.1 Names (neutral; check before every screenshot)

| Group | Members | How members get in |
|---|---|---|
| `laptop-admins` | the team's laptop peer(s) | add the laptop peer, or add the group to the admin *user*; a user's groups propagate to their peers (D:/manage/access-control) |
| `control-plane` | `brz-control` (VM #1) | auto-group on `NETBIRD_SETUP_KEY_CP` |
| `sandbox-host` | `brz-sandbox` (VM #2) | auto-group on `NETBIRD_SETUP_KEY_SB` |
| `approvers` | *users* allowed on the Ontofill Console (engine approvals, SSO) | Team → Users → user → groups (G3) |

Ports (defaults; change them to match the code): engine API `8000`, Postgres `5432`, Oxigraph `7878`,
investigator `8400`, approver `8401` (from `proveedor-abierto/deploy`), sandbox dispatch agent `9100`
(**placeholder**), pod live-view `6080-6089` (at most 10, which matches the expose limit).

### 2.2 Dashboard steps

- **Groups:** Access Control → Groups → Add group.
- **Policies:** Access Control → Policies → Add Policy. Set Source, Destination, Protocol (ALL/TCP/UDP/ICMP/NetBird SSH), Ports and Direction, attach Posture Checks, then give it a name.
- **Posture checks:** Access Control → Posture Checks → Create Posture Check. The options are client version, country/region, peer network range, OS version and process. They apply to the policy's **source** peers.

Sources: D:/manage/access-control/manage-network-access, D:/manage/access-control/posture-checks.

### 2.3 Policy set for our topology (all unidirectional)

| # | Name | Source → Destination | Protocol / ports | Why |
|---|---|---|---|---|
| P1 | `admin-to-vms` | `laptop-admins` → `control-plane`, `sandbox-host` | ALL | Admin, SSH, and reaching Postgres/Oxigraph from the laptop. Using the `All` group as destination also works; keep it one-way (G8). |
| P2 | `cp-dispatch-to-sandbox` | `control-plane` → `sandbox-host` | TCP `9100` | `dispatch(task)` |
| P3 | `sandbox-to-cp-api` | `sandbox-host` → `control-plane` | TCP `8000` | results back to the engine API only. Add `5432` or `7878` **only** if the sandbox writes directly. |
| — | *(delete)* `Default` | All ↔ All | ALL | G7 |

- **The sandbox never reaches the laptop.** No policy has `laptop-admins` as a destination, and NetBird denies by default ("Without policies, no peer can communicate"). Replies to connections the laptop opens are allowed because P1 is one-way.
- **Reverse-proxy reachability needs no policy of yours.** For every enabled service, management synthesizes a "Proxy Access to <svc>" policy from the proxy peer to the target on the target port. The fence doesn't block the public URLs, and the proxy can't reach other ports. SRC:`management/server/types/legacynmap/proxy_policies.go` L16-50, L102-135 (src).
- **Optional posture check** (Business/trial): `nb_version_check.min_version = "0.66.0"` on P1, so an admin peer must run a client with `expose` support.

### 2.4 REST API

Auth: `Authorization: Token $NETBIRD_API_TOKEN`. The token is a personal access token (Team → Users → you →
Create token) or, better, a **service user** token (Team → Service Users; the role must be Admin for writes).
Source: D:/manage/public-api, D:/api/guides/authentication. Base URL: `https://api.netbird.io/api`.

| Resource | Endpoints (D:/api/resources/…) |
|---|---|
| Groups | `GET/POST /groups`, `GET/PUT/DELETE /groups/{id}`; body `{"name","peers":[peerIds]}` |
| Setup keys | `GET/POST /setup-keys`, `PUT/DELETE /setup-keys/{id}`; body `{"name","type":"one-off"\|"reusable","expires_in","auto_groups":[ids],"usage_limit","ephemeral"}` |
| Policies | `GET/POST /policies`, `PUT/DELETE /policies/{id}`; rule `{"name","enabled","action":"accept","bidirectional","protocol":"all"\|"tcp"\|"udp"\|"icmp"\|"netbird-ssh","ports":[..],"sources":[gids],"destinations":[gids]}` |
| Posture checks | `POST /posture-checks` `{"name","description","checks":{"nb_version_check":{"min_version":"0.66.0"}}}` |
| Peers | `GET /peers`, `PUT /peers/{id}` (rename, `ssh_enabled`), `DELETE /peers/{id}` |
| Account settings | `GET /accounts`, `PUT /accounts/{id}` (G6) |
| Users | `GET /users`, `PUT /users/{id}` (`auto_groups` adds the user to `approvers`) |
| Reverse proxy | `GET /reverse-proxies/domains`, `GET/POST /reverse-proxies/services`, `GET/PUT/DELETE /reverse-proxies/services/{id}`, `POST /reverse-proxies/domains` (custom), `GET /reverse-proxies/domains/{id}/validate` |

```bash
API=https://api.netbird.io/api; H=(-H "Authorization: Token $NETBIRD_API_TOKEN" -H "Content-Type: application/json")
gid() { curl -sS $API/groups "${H[@]}" | jq -r --arg n "$1" '.[] | select(.name==$n) | .id'; }
for g in laptop-admins control-plane sandbox-host approvers; do
  [ -n "$(gid $g)" ] || curl -sS -X POST $API/groups "${H[@]}" -d "{\"name\":\"$g\"}" >/dev/null; done
key() { curl -sS -X POST $API/setup-keys "${H[@]}" -d "$(jq -n --arg n "$1" --arg g "$(gid $2)" \
  '{name:$n,type:"one-off",expires_in:86400,auto_groups:[$g],usage_limit:1,ephemeral:false}')" | jq -r .key; }
export NETBIRD_SETUP_KEY_CP=$(key brz-control control-plane) NETBIRD_SETUP_KEY_SB=$(key brz-sandbox sandbox-host)
pol() { # $1 name $2 src $3 dst $4 proto $5 ports-json
  curl -sS -X POST $API/policies "${H[@]}" -d "$(jq -n --arg n "$1" --arg s "$(gid $2)" --arg d "$(gid $3)" \
    --arg p "$4" --argjson ports "$5" '{name:$n,enabled:true,rules:[{name:$n,enabled:true,action:"accept",
    bidirectional:false,protocol:$p,sources:[$s],destinations:[$d]} + (if ($ports|length)>0 then {ports:$ports} else {} end)]}')" | jq -r .id; }
pol admin-to-cp      laptop-admins control-plane all '[]'
pol admin-to-sandbox laptop-admins sandbox-host  all '[]'
pol cp-dispatch-to-sandbox control-plane sandbox-host tcp '["9100"]'
pol sandbox-to-cp-api      sandbox-host control-plane tcp '["8000"]'
# only after the four above exist:
DEF=$(curl -sS $API/policies "${H[@]}" | jq -r '.[] | select(.name=="Default") | .id'); [ -n "$DEF" ] && curl -sS -X DELETE $API/policies/$DEF "${H[@]}"
```

On create, the response field `.key` holds the plain setup key (D:/api/resources/setup-keys, example response).
Don't print it.

---

## 3. NetBird SSH

- **What it is:** an SSH server embedded in the NetBird client. The session arrives at the peer on TCP 22, and the client redirects it to its own server on **22022**. It is reachable only over the mesh. You connect with `netbird ssh user@<peer>` or plain OpenSSH, which NetBird configures through `/etc/ssh/ssh_config.d/99-netbird.conf`. It needs v0.61+ on both ends. Source: D:/manage/peers/ssh.
- **How to enable it:**
  1. On the VM: `netbird up --allow-server-ssh` (plus `--enable-ssh-root`, `--enable-ssh-sftp`, `--enable-ssh-local-port-forwarding` as needed). These need root. If the peer is already up, run `netbird down` first.
  2. In the dashboard: enable SSH on the peer (API `ssh_enabled: true`).
  3. Add a policy with protocol **NetBird SSH** (API `netbird-ssh`), or TCP 22 for network-level access only. Management auto-adds 22022 when a policy allows 22.
- **Auth:** JWT through the IdP by default, so each new session goes through the OIDC flow unless `--ssh-jwt-cache-ttl` is set. `--disable-ssh-auth` falls back to ACL-only machine identity.
- **Does it replace OpenSSH? No.** `sshd` keeps running. NetBird only intercepts TCP 22 that arrives *over the mesh* on peers with NetBird SSH enabled. The public port 22 is closed by the rule-less Vultr firewall group anyway.
- **Pick one path per VM:**
  - (a) **Plain OpenSSH over the mesh.** No `--allow-server-ssh`. P1 (ALL) covers TCP 22. Use `ssh root@<100.x or brz-control.netbird.cloud>` with the Vultr SSH key. This is the simplest, and it's what vultr.md assumes.
  - (b) **NetBird SSH.** Identity-bound, a good story for judges, but every new session needs a browser OIDC flow.

---

## 4. Reverse proxy on NetBird Cloud

### 4.1 Where it is and what state it's in

- **Dashboard:** left nav **Reverse Proxy** (shown with a **Beta** badge) → **Services** → **Add Service**. The same menu has **Custom Domains**, **Clusters** and **Logs** (access logs). Sources: D:/manage/reverse-proxy#quick-start; DASH:`src/layouts/Navigation.tsx` L150-190.
- **Status as of 2026-09-26: beta** ("Reverse Proxy is currently in **beta**", D:/manage/reverse-proxy). There is **no separate beta opt-in**. The menu shows for anyone with the Services permission (Network Admin role or higher). Cloud free domains "are available immediately with no additional configuration". The task brief asked about "Sep 2026"; today is 2026-09-26, and nothing says GA.
- **Prerequisites:** a connected target peer, and the Services permission. The dashboard warns before it saves a service with no auth.
- **Doesn't work with Rosenpass.** Don't pass `--enable-rosenpass`.

### 4.2 Service configuration

| Field | Our value | Notes |
|---|---|---|
| Mode | HTTP | L4 (TCP/UDP/TLS) gets no browser auth; on Cloud the L4 listen port is auto-assigned |
| Subdomain + base domain | `investigator` / `approver` + the **Free** domain | final form `investigator.<nonce>.<cluster>.proxy.netbird.io` |
| Target | Type **Peer** → `brz-control`, protocol `HTTP`, port `8400` / `8401` | dialed at the peer's NetBird IP (G1) |
| Path (optional) | e.g. `/api` → port 8000 as a second target | per-target prefix routing; the matched prefix is stripped unless `path_rewrite: "preserve"` |
| Settings | Pass Host Header **on**, Rewrite Redirects **on** | the app sees the public host; redirects stay public |
| Access Control | optional allowed countries; CrowdSec if the cluster offers it | evaluated **before** auth |
| Status | `pending` → `certificate_pending` → `active` | `tunnel_not_created` means the proxy can't reach the peer |

- **Custom domain (optional):** Reverse Proxy → Custom Domains → Add Domain → pick the cluster → create the CNAME the dashboard shows, e.g. `*.proxy.example.com CNAME eu.proxy.netbird.io` → Validate. Source: D:/manage/reverse-proxy/custom-domains.
- **WebSockets work** through HTTP services: the proxy tracks hijacked or upgraded connections (SRC:`proxy/internal/conntrack/hijacked.go`, `proxy/internal/responsewriter/responsewriter.go`) (src). No doc states it.

### 4.3 Auth options (per service: Authentication and Access Control tabs)

| Option | Configure | Session | Our use |
|---|---|---|---|
| **SSO (OIDC)** | Authentication → SSO → toggle, optionally pick groups (API `bearer_auth.distribution_groups` = group **IDs**) | 24 h | **approver**, groups = `approvers` (G3) |
| **Password** | Authentication → Password (API `password_auth`) | 24 h | **investigator** |
| **PIN** | Authentication → PIN Code, numeric (API `pin_auth`) | 24 h | investigator alternative |
| **Header** | Basic / Bearer / custom header name and value; OR across entries; stripped before forwarding (API `header_auths[]`) | checked on every request | optional machine access (`X-API-Key`) |
| **NetBird-only (private)** | needs a cluster with the `Private` capability; **Cloud shared clusters don't have it**, so it would take your own BYOP proxy (inbound ports) | — | **not available to us** |
| IP / country | Access Control → allowed/blocked CIDRs and countries (ISO alpha-2); Allow match Any/All | per connection | optional |
| CrowdSec | Access Control → Off/Enforce/Observe, only when the cluster supports it (`supports_crowdsec` on the domain) | per connection | optional |

- Operator methods can be combined; the user picks one. NetBird-only can't be combined with them.
- Values are stored as Argon2id hashes. Sessions are Ed25519 JWTs scoped to one service.
- Users who are blocked or pending approval are refused even after IdP login.
- Sources: D:/manage/reverse-proxy/authentication; D:/api/resources/services.

### 4.4 Identity forwarded to the backend

- **Header names:** `X-NetBird-User` (email, or the peer name for user-less peers) and `X-NetBird-Groups` (comma-separated group names). Client-supplied copies are always stripped (SRC:`proxy/internal/proxy/reverseproxy.go` L909-916).
- **Documented** only for NetBird-Only services (D:/manage/reverse-proxy/service-configuration#net-bird-identity-headers).
- **Source behaviour (src):** `stampNetBirdIdentity` runs on every proxied request, and the session-cookie path of any auth method loads the email and groups from the session JWT (`proxy/internal/auth/middleware.go` L373-380). **SSO sessions should therefore carry `X-NetBird-User`**, while PIN and password sessions should carry none.
- Whether Cloud runs this proxy build is **UNVERIFIED**. Log the headers on day one:

```python
# FastAPI: optional audit metadata, never an authorization input
from fastapi import Header
def audit_identity(user: str | None = Header(None, alias="X-NetBird-User"),
                   groups: str | None = Header(None, alias="X-NetBird-Groups")):
    return {"netbird_user": user, "netbird_groups": groups.split(",") if groups else []}
```

### 4.5 The same services through the API

```bash
CP_PEER=$(curl -sS $API/peers "${H[@]}" | jq -r '.[] | select(.name=="brz-control") | .id')
FREE=$(curl -sS $API/reverse-proxies/domains "${H[@]}" | jq -r '[.[] | select(.type=="free")][0].domain')
svc() { # $1 sub  $2 port  $3 auth-json
  curl -sS -X POST $API/reverse-proxies/services "${H[@]}" -d "$(jq -n --arg d "$1.$FREE" --arg n "brz-$1" \
   --arg p "$CP_PEER" --argjson port "$2" --argjson auth "$3" '{name:$n,domain:$d,mode:"http",enabled:true,
   pass_host_header:true,rewrite_redirects:true,auth:$auth,
   targets:[{target_id:$p,target_type:"peer",protocol:"http",port:$port,enabled:true}]}')" | jq '{id,domain,status: .meta.status}'; }
svc investigator 8400 "$(jq -n --arg pw "$INVESTIGATOR_PASSWORD" '{password_auth:{enabled:true,password:$pw}}')"
svc approver     8401 "$(jq -n --arg g "$(gid approvers)" '{bearer_auth:{enabled:true,distribution_groups:[$g]}}')"
curl -sS $API/reverse-proxies/services "${H[@]}" | jq -r '.[] | "\(.name)\t\(.domain)\t\(.enabled)"'
```

- Field names come from D:/api/resources/services. The response carries `meta.status`
  (`pending` / `certificate_pending` / `active` / …); poll `GET /reverse-proxies/services/{id}` until it is `active`.
- The app's `netbird_services.py` does this idempotently. Its open question, "is `target_type` `peer`?", is answered: **yes** (enum `peer|host|domain|subnet|cluster`).

---

## 5. Peer Expose settings (prerequisite for `netbird expose`)

### 5.1 Dashboard

**Settings → Clients → Peer Expose** → toggle **Enable Peer Expose** → **Allowed peer groups** (at least one, e.g.
`sandbox-host`, plus `control-plane` if you use the expose fallback for the role URLs) → **Save Changes**.
Source: D:/manage/reverse-proxy/expose-from-cli#enable-peer-expose.

### 5.2 API (G6: merge, don't overwrite)

```bash
ACC=$(curl -sS $API/accounts "${H[@]}" | jq -r '.[0].id')
curl -sS $API/accounts/$ACC "${H[@]}" | jq --arg g "$(gid sandbox-host)" \
  '{settings: (.settings + {peer_expose_enabled:true, peer_expose_groups:[$g]})}' > /tmp/acc.json
curl -sS -X PUT $API/accounts/$ACC "${H[@]}" --data @/tmp/acc.json | jq '.settings | {peer_expose_enabled,peer_expose_groups}'
```

If `.settings` carries nested read-only objects the PUT rejects, drop them in the jq filter (**UNVERIFIED**).

---

## 6. `netbird expose` (v0.66+; local client 0.79.0)

### 6.1 Flags (verbatim from `netbird expose --help`, 0.79.0)

| Flag | Type | Notes |
|---|---|---|
| `<port>` | arg | local port on **this** peer; the proxy dials `<this peer's 100.x>:<port>` |
| `--protocol` | `http` (default) \| `https` \| `tcp` \| `udp` \| `tls` | `https` = the backend speaks TLS |
| `--with-pin` | string | **exactly 6 digits** (server regex `^\d{6}$`); HTTP only |
| `--with-password` | string | HTTP only |
| `--with-user-groups` | strings | NetBird group **names**, resolved to IDs and checked against the user's groups; HTTP only |
| `--with-name-prefix` | string | `[a-z0-9-]`, 1–32 chars, starts and ends alphanumeric → `prefix-xxxx` |
| `--with-external-port` | uint16 | L4 only; Cloud may reassign it ("Note: requested port N was reassigned") |
| `--with-custom-domain` | string | must already be verified; the service becomes `<name>.<domain>` |

Auth flags combine (`--with-pin … --with-user-groups …`), and the user picks one. With no auth flag the URL is
**public**. Sources: D:/manage/reverse-proxy/expose-from-cli; SRC:`management/internals/modules/reverseproxy/service/service.go`
L1490-1610; SRC:`management/internals/modules/reverseproxy/service/manager/manager.go` L1205-1215.

### 6.2 Output and lifetime

```
Service exposed successfully!
  Name:     pod-a1b2-x9k3
  URL:      https://pod-a1b2-x9k3.<nonce>.<cluster>.proxy.netbird.io
  Domain:   pod-a1b2-x9k3.<nonce>.<cluster>.proxy.netbird.io
  Protocol: http
  Internal: 6080

Press Ctrl+C to stop exposing.
```

The documented example shows the domain as `…proxy.example.com`; the Cloud form above is inferred from the
free-domain format.

| Event | Service removed? | Source |
|---|---|---|
| Ctrl+C / SIGTERM to `netbird expose` | immediately (stop request, 5 s timeout) | docs |
| `netbird expose` process SIGKILLed or crashes | **immediately**: the daemon's stream context cancels and `KeepAlive` runs `defer m.stop(domain)` | SRC:`client/internal/expose/manager.go` L69-87 (src) |
| NetBird **daemon** killed, host reboot, network loss | after the **90 s** TTL (renewed every 30 s) | docs |
| Management restart | immediately (in-memory sessions) | docs |

- **Limit:** 10 active sessions per peer. The error is `peer has reached the maximum number of active expose sessions (10)`.
- **Events:** "Peer exposed service", "Peer unexposed service" and "Peer expose expired" appear in **Events → Audit**.
- **Errors:**
  - `permission denied`: Peer Expose is off, or the peer isn't in an allowed group.
  - `client is not running, run 'netbird up' first`: the daemon isn't connected.
  - `expose requires inbound connections but 'block inbound' is enabled`: see G4.

### 6.3 Capture the URL from a supervisor (Python)

```python
import re, secrets, signal, subprocess, threading

URL_RE = re.compile(r"^\s*URL:\s+(\S+)")

def start_expose(port: int, prefix: str, timeout: float = 45.0):
    """Start `netbird expose` with a fresh 6-digit PIN; return (proc, url, pin)."""
    pin = f"{secrets.randbelow(10**6):06d}"
    proc = subprocess.Popen(
        ["netbird", "expose", str(port), "--with-pin", pin, "--with-name-prefix", prefix],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)  # banner is on stderr (G13)
    found, tail = threading.Event(), []
    box: dict[str, str] = {}

    def reader():                                     # keep draining so the child never blocks on a full pipe
        for line in proc.stdout:
            m = URL_RE.match(line)
            if m and not found.is_set():
                box["url"] = m.group(1); found.set()
            elif not found.is_set() and line.strip():
                tail.append(line.strip())
        found.set()                                   # EOF: process exited (error path)

    threading.Thread(target=reader, daemon=True).start()
    found.wait(timeout)
    if "url" not in box:
        proc.send_signal(signal.SIGTERM)
        raise RuntimeError("expose failed: " + " | ".join(tail[-3:]))
    return proc, box["url"], pin

def stop_expose(proc):
    proc.send_signal(signal.SIGTERM)                  # graceful: service removed at once
    try: proc.wait(10)
    except subprocess.TimeoutExpired: proc.kill()     # still removed at once (daemon stops the session)
```

Write `url` into `status.json.live_view_url`. Store the PIN beside it only where authorized viewers can read it,
never in logs. The PIN is visible in the VM's `ps` output. Pods under gVisor can't see host processes, but other
host users can.

### 6.4 One expose per Docker container (bash, VM #2)

```bash
#!/usr/bin/env bash
# usage: pod-with-liveview.sh <host-port 6080-6089> <image> [docker args...]
set -euo pipefail
PORT=$1; IMAGE=$2; shift 2
NB_IP=$(netbird status --ipv4)                                     # G1: publish on the mesh IP, not 127.0.0.1
CID=$(docker run -d --runtime=runsc -p "$NB_IP:$PORT:6080" "$@" "$IMAGE")
PIN=$(python3 -c 'import secrets;print(f"{secrets.randbelow(10**6):06d}")')
LOG=/run/brz/expose-$CID.log; mkdir -p /run/brz; install -m 600 /dev/null "$LOG"
netbird expose "$PORT" --with-pin "$PIN" --with-name-prefix "pod-${CID:0:4}" >"$LOG" 2>&1 & XP=$!
cleanup() { kill -TERM "$XP" 2>/dev/null || true; wait "$XP" 2>/dev/null || true; docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
for _ in $(seq 90); do URL=$(awk '/^ *URL:/{print $2; exit}' "$LOG"); [ -n "$URL" ] && break
  kill -0 "$XP" 2>/dev/null || { cat "$LOG" >&2; exit 1; }; sleep 0.5; done
echo "{\"container\":\"$CID\",\"live_view_url\":\"$URL\"}"            # hand to the supervisor; PIN goes to the viewer channel
docker wait "$CID" >/dev/null                                      # block for the pod's lifetime
```

- **Docker-published ports** on the NetBird IP: traffic from `wt0` gets Docker's DNAT, and NetBird installs mangle FORWARD guards so that DNAT can't bypass its ACLs (SRC:`client/firewall/iptables/family_linux.go` L31-34). The auto-generated proxy policy matches the published port. Whether the whole path works end to end is **UNVERIFIED**, so test one pod first.
- **Fallback** if the DNAT path fails: publish on `127.0.0.1:$PORT` and run `socat TCP-LISTEN:$PORT,bind=$NB_IP,fork,reuseaddr TCP:127.0.0.1:$PORT` beside the expose process.
- **Supervisor:** give each pod a systemd scope or unit so a crashed supervisor still kills `netbird expose`. For example, run the script under `systemd-run --unit=pod-$ID --collect`.

---

## 7. Routes / Networks (short: not needed here)

- Reverse-proxy and expose targets are the peers themselves (G1), and both VMs and the laptop are peers. **We need no Network routes.**
- Add a Network only to reach a non-peer subnet from peers, such as a Vultr VPC range with a VM that doesn't run NetBird. Networks → Add Network → Resource (the CIDR) → Routing peer, plus a policy to the resource. Source: D:/manage/networks.
- Never route Docker bridge networks onto the mesh: that would undo the pod fence (G12).

---

## 8. Firewall interplay (Vultr group with zero inbound rules)

| Direction | Needed by NetBird Cloud | Vultr default |
|---|---|---|
| Inbound | **none** ("The NetBird client doesn't require any inbound port to be open") | all dropped: OK |
| Out TCP 443 | `api.netbird.io` (management), `signal.netbird.io`, `*.relay.netbird.io` / `relay.netbird.io` (relay over WebSocket) | outbound not filtered |
| Out UDP 443 | relay over QUIC (optional) | not filtered |
| Out UDP 80, 443, 3478, 5555 | `stun.netbird.io` (candidate discovery) | not filtered |
| Out UDP to peers | direct WireGuard (default listen port 51820) | not filtered |

Source: D:/about-netbird/ports-and-firewalls; the Vultr outbound behaviour is in [vultr.md](vultr.md) §5.

**P2P or relay?** ICE sends from both sides at once, so it works if the Vultr firewall tracks UDP state. Whether it
does is **UNVERIFIED** (vultr.md §5). The likely outcomes:

- **VM ↔ VM:** most likely **direct over the VPC**. They share a VPC, so ICE `host` candidates (10.42.0.x) pair up. That assumes firewall groups don't filter VPC traffic (**UNVERIFIED**).
- **Laptop ↔ VM:** P2P via `srflx` if the firewall is stateful for UDP, otherwise **Relayed**. Relayed traffic is still end-to-end WireGuard.

How to tell:

```bash
netbird status -d | grep -E 'NetBird IP|Connection type|Direct|ICE candidate \(|Relay'
netbird status -d -T Relayed            # list only relayed peers (P2P|Relayed)
```

Read the output this way:

- `Connection type: P2P` with `Direct: true` means a direct tunnel.
- `ICE candidate (Local/Remote): host/host` means the pair runs over the LAN or VPC. `srflx` means it goes through NAT hole punching.
- `Relayed` / `relay` means the traffic uses the relay.

For the "peer-to-peer" bonus, show the VM ↔ VM `P2P` line and the laptop line, whatever it is.

---

## 9. Evidence for judges

**Before every capture:**

- Check every peer, group, service and user name on screen. They must be neutral (`brz-control`, `brz-sandbox`, `laptop-admins`, …).
- **Never capture a hostname that contains an employer or client name.** Rename the peer first (§1.1) or crop it out.
- Keep emails, setup keys, tokens, PINs and passwords out of frame.

| # | Screenshot / artifact | Shows bonus |
|---|---|---|
| E1 | Peers list: `brz-control`, `brz-sandbox` and the laptop connected, with groups | 3 |
| E2 | Access Control → Policies: P1–P3, `Default` gone, one-way arrows | fence |
| E3 | `netbird status -d` on VM #1: `Connection type: P2P` to `brz-sandbox` | 3 |
| E4 | Reverse Proxy → Services: both services `active`, auth badges (password vs SSO + `approvers`) | 1, 2 |
| E5 | The Proveedor Abierto product URL asking for the password, and the Ontofill Console URL redirecting to SSO (a private browser window) | 2 |
| E6 | External `nmap -Pn -p 1-65535 <public-ip>` (or `nc -zv`): everything filtered, 22 included | 1 |
| E7 | A live `netbird expose` terminal next to the pod, the URL asking for the PIN, then after `docker stop` the URL failing plus the "Peer unexposed service" audit event | 4 |
| E8 | Reverse Proxy → Logs: an authenticated request with the method | 2 |

---

## 10. Troubleshooting

| Symptom | Reach for |
|---|---|
| Is the peer up? | `netbird status`, `netbird status --check ready; echo $?`, `systemctl status netbird`, `journalctl -u netbird -n 100` |
| P2P vs relay | `netbird status -d`, `netbird status -d -T Relayed` |
| Policy doesn't apply | API `GET /peers/{id}/accessible-peers` (D:/api/resources/peers); `netbird debug trace` to trace a packet through the peer's firewall |
| Service stuck `tunnel_not_created` / 502 | Is the target bound to the NetBird IP (G1)? Check `ss -ltnp \| grep <port>` on the VM, then `curl -sI http://$(netbird status --ipv4):8400/` from the VM and from the laptop over the mesh |
| `certificate_pending` for long | Wait a few minutes; on a custom domain, check the CNAME and CAA records (D:/manage/reverse-proxy/custom-domains) |
| Expose errors | §6.2 error list; `netbird status` for the daemon; Settings → Clients for Peer Expose |
| Firewall rules on the host | `iptables -S \| grep -i netbird`, `nft list ruleset \| grep -i netbird`, `iptables -S DOCKER-USER` |
| Support bundle | `netbird debug bundle` (anonymize with `-A`) |
| Reset a VM's enrollment | `netbird down && netbird deregister`, then `up` with a new key |

Reverse-proxy-specific help: D:/manage/reverse-proxy/troubleshooting.

---

## 11. Copy-paste runbook

Prereqs: `NETBIRD_API_TOKEN` (service user, Admin role), `jq`, and the §2.4 helper variables (`API`, `H`,
`gid`). The dashboard path is given beside each step.

1. **Groups and keys** (Access Control → Groups; Settings → Setup Keys): run the §2.4 block up to the `export NETBIRD_SETUP_KEY_CP…` line. Put the laptop peer (or your user) in `laptop-admins`, and your approver user(s) in `approvers`:
   ```bash
   U=$(curl -sS $API/users "${H[@]}" | jq -r '.[] | select(.email==env.APPROVER_EMAIL) | .id')
   curl -sS $API/users "${H[@]}" | jq --arg u "$U" --arg g "$(gid approvers)" '.[] | select(.id==$u) |
     {role, is_blocked, auto_groups: ((.auto_groups // []) + [$g] | unique)}' > /tmp/u.json
   curl -sS -X PUT $API/users/$U "${H[@]}" --data @/tmp/u.json | jq '{id, auto_groups}'
   ```
2. **Policies** (Access Control → Policies): run the §2.4 `pol …` lines, then delete `Default`. Check the laptop still reaches nothing yet (no VMs), and that nothing else broke.
3. **Enroll the VMs:** in [vultr.md](vultr.md) §9, use the §1.4 cloud-init lines, render `__NB_SETUP_KEY__` per role, and create both instances. Then:
   ```bash
   curl -sS $API/peers "${H[@]}" | jq -r '.[] | "\(.name)\t\(.ip)\t\(.connected)\t\([.groups[].name]|join(","))"'
   for k in $(curl -sS $API/setup-keys "${H[@]}" | jq -r '.[] | select(.name|startswith("brz-")) | .id'); do
     curl -sS -X DELETE $API/setup-keys/$k "${H[@]}"; done        # revoke; enrolled peers stay (G10)
   ssh root@brz-control.netbird.cloud 'cloud-init status --wait; cat /root/netbird-status.txt'
   ```
4. **Bind the apps to the mesh IP** on VM #1: `PA_BIND_IP=$(netbird status --ipv4)` in `deploy/.env`, then `docker compose up -d`. Start compose after `netbird.service` (the `for … --check ready` loop in §1.4). Otherwise the bind fails with "cannot assign requested address".
5. **Enable Peer Expose** (Settings → Clients → Peer Expose, allowed group `sandbox-host`): run the §5.2 block.
6. **Create the two services** (Reverse Proxy → Services → Add Service):
   - Dashboard: see §4.2 and §4.3.
   - API: run the §4.5 block, or `proveedor-abierto/deploy/netbird_services.py plan && … apply`.
   - Wait for `active`.
7. **Verify from outside** (not on the mesh: `netbird down` on the laptop, or use a phone hotspot):
   ```bash
   nmap -Pn -p- --min-rate 2000 "$CP_PUBLIC_IP" "$SB_PUBLIC_IP"     # expect: all filtered, incl. 22
   curl -sI "https://investigator.$FREE" | head -3                  # expect an auth page / 401-style, not app content
   curl -sI "https://approver.$FREE" | head -3                      # expect a redirect to the SSO login
   ```
   Then log in to each URL in a browser (E5). Confirm the approver backend logs `X-NetBird-User` (§4.4).
8. **Per-pod expose:** copy §6.4 to VM #2 as `/usr/local/bin/pod-with-liveview.sh` and start one pod. Open the URL, enter the PIN, then `docker stop` the pod. The URL must stop working, and **Events → Audit** must show "Peer unexposed service".
9. **Evidence:** capture §9 E1–E8.
10. **Teardown:**
    ```bash
    for s in $(curl -sS $API/reverse-proxies/services "${H[@]}" | jq -r '.[] | select(.name|startswith("brz-")) | .id'); do
      curl -sS -X DELETE $API/reverse-proxies/services/$s "${H[@]}"; done
    pkill -TERM -f 'netbird expose' || true                           # on VM #2; sessions vanish at once
    for p in $(curl -sS $API/peers "${H[@]}" | jq -r '.[] | select(.name|test("^brz-")) | .id'); do
      curl -sS -X DELETE $API/peers/$p "${H[@]}"; done              # before or after deleting the VMs (vultr.md §9 step 10)
    ```
    Optionally turn Peer Expose off again (§5.2 with `false`), and restore a policy for the laptop if you still need one.

---

## 12. UNVERIFIED items to check by hand

1. Which plan the reverse proxy and Peer Expose need on Cloud after the trial (not stated in the docs or on the pricing page).
2. Whether Cloud's proxy stamps `X-NetBird-User` on **SSO** sessions. The source says yes; the docs mention only NetBird-Only.
3. Docker-published ports on the NetBird IP as expose/proxy targets (the DNAT path through NetBird's ACL guard); socat fallback in §6.4.
4. P2P vs relay through a rule-less Vultr firewall group (UDP state tracking, VPC filtering).
5. `PUT /accounts/{id}` accepting the full GET-derived `settings` object unchanged.
6. Whether `netbird up --hostname` renames an already-registered peer.
7. What happens to posture checks, and to other Business features in use, when the 14-day trial ends.
8. WebSocket upgrades through HTTP services: the source handles them, but no doc says so. Test the live-view stream.
