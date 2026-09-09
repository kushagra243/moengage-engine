# Team access — private, allow-list only

The engine is a long-running local service: FastAPI + a scheduler + SQLite in `data/` + secrets encrypted with a Keychain-held key + an outbound host allowlist. That is exactly what keeps real MoEngage data on one machine, and it is also why **Vercel is not an option**: Vercel runs stateless serverless functions (no persistent SQLite, no Keychain, no 15-minute scheduler, no long-lived processes, and the data would live in a third-party cloud). Nothing needs to move to make the team use it privately, though: put an identity-aware tunnel in front of the Mac that already runs it.

## What the team gets
- The same terminal at a private URL, gated by an **email allow-list** (only the addresses you list can even reach the login page).
- Every action is attributed: the proxy asserts the signed-in email and the engine records it as the actor in the audit log, proposals, comments and SOP runs (`request_actor` trusts identity headers only when the proxy runs on the same host).
- **One model login for everyone:** the agent runs server-side with the host Mac's Claude Code login (`llm_provider = claude_cli`); when that session expires, it falls back to your OpenRouter key automatically (`llm_fallback_provider = openrouter`, `llm_fallback_model`, default Sonnet). Team members never paste keys.
- Data never leaves the Mac. The tunnel is outbound-only; no inbound port is opened.

## Option A — Cloudflare Tunnel + Access (recommended; free for up to 50 users)
On the Mac that runs the engine:
```bash
brew install cloudflared
cloudflared tunnel login                       # opens the browser once; pick the zone (your domain)
cloudflared tunnel create moengage-engine
cloudflared tunnel route dns moengage-engine engine.<your-domain>
```
Write `~/.cloudflared/config.yml`:
```yaml
tunnel: moengage-engine
credentials-file: /Users/<you>/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: engine.<your-domain>
    service: http://127.0.0.1:8080
    originRequest:
      httpHostHeader: engine.<your-domain>
  - service: http_status:404
```
Tell the engine which public host to accept, then start both (the engine keeps binding 127.0.0.1 only):
```bash
export MOE_ALLOWED_HOSTS=engine.<your-domain>
./cli.py service install                        # or: .venv/bin/python start.py
cloudflared service install                     # runs the tunnel at login
```
In **Cloudflare Zero Trust → Access → Applications → Add → Self-hosted**: application domain `engine.<your-domain>`, session 24h, policy **Allow** with *Emails* = the team list (or *Emails ending in* `@coindcx.com`). Identity provider: One-time PIN is enough; Google Workspace is nicer. Access adds `Cf-Access-Authenticated-User-Email` to every request; the engine reads it as the actor. Optionally enable **Require** country = India and a WARP/device posture rule.

Revoking a person = removing the email from the policy; sessions end within the session lifetime.

## Option B — Tailscale (no domain needed)
```bash
brew install --cask tailscale     # sign in on the Mac
tailscale serve --bg --https=443 http://127.0.0.1:8080
export MOE_ALLOWED_HOSTS=<mac-name>.<tailnet>.ts.net
```
Invite team members to the tailnet; restrict with an ACL that allows only the named users to reach the Mac on port 443. Tailscale Serve runs on the host, so `Tailscale-User-Login` is trusted as the actor. Good for a small team; no public hostname exists at all.

## Hardening checklist
- Keep `mock_mode=false` only on the host Mac; team members never see keys (Engine → Settings shows only *set / not set*).
- The per-process page token is injected into the HTML; because the page itself is behind Access, that is enough for browser use. Do not share raw `X-Local-Token` values.
- Audit: Engine → Integration → audit chain stays intact; every proposal, approval and SOP run records the proxy email.
- Backups: `data/agent.db` and the Keychain item are the whole state; Time Machine covers it.
- If the Mac sleeps, the tunnel drops: set *Prevent automatic sleeping* in Energy settings or run the engine on a Mac mini.

## If you still want a cloud host later
A small always-on VM (Fly.io, Hetzner, a Mac mini in a colo) with a persistent volume for `data/`, `MOE_SECRET_BACKEND=file` (the file backend is used automatically off macOS), and the same Cloudflare Access policy in front. Same code, same allow-list; only the machine changes — but the data then lives off your laptop, which is a decision to take deliberately.
