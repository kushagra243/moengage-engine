# Hosting for an internal team, securely and for free

The engine is built as a single-operator local service: it binds to loopback,
every API call needs a per-page token, secrets are encrypted with a key held
outside the database, and all writes to MoEngage pass through an approval
queue with an audit trail. Keep those properties and there are three sound
ways to run it for a team. Pick by how much you want to share state.

## Option A — one install per person (default, zero hosting)

Each teammate clones the private repo and runs `./setup.sh`. Nothing is
hosted, nothing is shared except code. Credentials never leave the person's
Mac; approvals are theirs alone.

- Cost: none. Model: each person's Claude Code login, or an OpenRouter key.
- Downside: no shared feed, snapshots or approval history. Fine for a team of
  two or three who talk.

## Option B — one always-on host inside a private network (recommended)

Run one instance on a spare Mac mini or a small Linux VM and put an
**identity-aware proxy** in front of it. The engine never opens a public
port; the proxy authenticates each person and forwards to 127.0.0.1.

### B1. Tailscale (simplest, free for up to 100 devices / 3 users, team plans beyond)

1. Install Tailscale on the host and on each teammate's laptop; put the
   teammates in a group in the tailnet ACLs.
2. On the host:
   ```bash
   ./cli.py service install                    # engine on 127.0.0.1:8080, starts at boot
   export MOE_ALLOWED_HOSTS=engine.<tailnet>.ts.net
   tailscale serve --bg --https=443 http://127.0.0.1:8080
   ```
   `tailscale serve` terminates TLS with a certificate from Let's Encrypt,
   is reachable only inside the tailnet, and forwards the caller's identity
   in the `Tailscale-User-Login` header. The engine records that identity as
   `created_by` / `decided_by` on every proposal and approval, so the audit
   log says who approved what.
3. Restrict who can reach the host in the tailnet ACL:
   ```json
   {"action": "accept", "src": ["group:growth"], "dst": ["tag:clm-engine:443"]}
   ```
4. Put `MOE_ALLOWED_HOSTS` in the launchd plist environment (or the systemd
   unit) so it survives restarts.

### B2. Cloudflare Access (Zero Trust, free up to 50 users)

Same shape, using `cloudflared tunnel` from the host and an Access policy
"allow emails ending in @yourcompany.com" or a Google Workspace group. The
identity arrives as `Cf-Access-Authenticated-User-Email` and is recorded the
same way. Use this if the team already lives on Google Workspace SSO and you
want browser-only access without installing a client.

### B3. Google Cloud IAP or similar

Equivalent for GCP shops: a small VM, IAP in front, `X-Forwarded-User`
forwarded to the engine.

### Host hardening checklist (B1–B3)

- Keep the bind on loopback (it is; `start.py` does not accept another host).
  Only the proxy talks to it.
- Linux hosts have no Keychain: the secret key falls back to
  `data/secret.key` (mode 0600). Put `data/` on an encrypted volume and run
  the service as a dedicated user with no shell.
- Back up `data/agent.db` and `data/secret.key` together, encrypted; one
  without the other is useless.
- Rotate MoEngage API keys and dashboard tokens on a schedule; re-paste in
  Settings. Rotation is logged in the audit trail.
- Set `MOE_ALLOWED_HOSTS` to the exact proxy hostname; the Host check
  rejects anything else (DNS-rebinding defence).
- Turn on the proxy's access logs and the tailnet/Access audit; the engine's
  own log is hash-chained, so tampering shows up in Integration → Audit log.
- Leave `mock_mode` off only on the host that holds real credentials.

## Option C — a public host (not recommended)

If a device-agnostic URL is unavoidable, the engine still must not be
exposed directly: it has a shared per-page token, not per-user login. Put it
behind an SSO reverse proxy (oauth2-proxy with your IdP, or Cloudflare
Access) that terminates TLS, enforces MFA, and forwards identity headers.
Add IP allowlisting for the office/VPN ranges. Treat this as B2 with more
surface, not a different design.

## What "internal only" buys you here

- The dashboard credentials and API keys exist in exactly one place, on the
  host, encrypted. Teammates never see or hold them.
- Every write to MoEngage is a proposal that a named person approved, with
  the exact request preview and a tamper-evident log.
- Outbound traffic is allowlisted to MoEngage, the configured model
  endpoint, and public market feeds; the LLM scope can never reach
  MoEngage hosts.
- No third-party SaaS holds your data: the only external calls are the ones
  you configured.
