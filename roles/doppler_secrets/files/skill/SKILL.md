---
name: doppler-secrets
description: Use Doppler-managed credentials for trusted commands on this VPS through its local secret service, without giving Codex the bootstrap token.
---

# Doppler secrets

Use this skill when an authorized task needs an application credential. Resolve
the exact secret name from the task or the names-only mapping at
`${CODEX_HOME:-$HOME/.codex}/doppler/integrations.json`, when present. Never inspect
credential stores, dump environments, or request secret values in chat.

Run the bundled `scripts/doppler.py` by its absolute path. It works from SSH,
tmux, Herdr, or an ordinary shell. No login enrollment or Doppler CLI is needed.

```bash
python3 /absolute/path/to/doppler-secrets/scripts/doppler.py run \
  --map NOTION_API_TOKEN=NOTION_TOKEN \
  --clean-env --inherit PATH --inherit HOME -- \
  /usr/bin/node /absolute/path/to/trusted-client.mjs
```

- Use repeated `--require NAME` and/or `--map SOURCE=TARGET`. Arguments contain
  names only. Values go into the application's environment, never its arguments.
- Prefer `--clean-env` with explicit `--inherit` names so unrelated exported
  credentials are excluded. Without it, ordinary ambient variables are retained.
- Never launch an entire agent, environment dump, shell tracing session, or
  untrusted application with secrets. Application output is not redacted.
- `check --require NAME` validates selected values without printing them.
  A normal `run` already checks them; do not add a separate check to every call.
- `doctor --offline` checks local service readiness without contacting Doppler.
  `paths` shows the socket location. Neither proves application authentication.
- Treat failures as failures. There is no local credential or offline-cache
  fallback. Do not expand the selection or bypass the service to make a call work.
- Credential access does not authorize messages, writes, or any other product
  operation. Follow the corresponding product skill and the user's scope.

The root-managed service holds the bootstrap token in a systemd runtime
credential and fetches only the requested names. The configured development
account can request ordinary names within that token's Doppler config; the
selection is not an application-specific access policy. Use a read-only token
for a suitably scoped config. Root and processes running as the development
account remain trusted.

Ansible owns this skill package. Existing private profiles outside it are left
untouched but are not used by this managed wrapper. Rotate `DOPPLER_TOKEN` in
GitHub's `development` environment and rerun **Bootstrap VPS**. For service
failures, inspect safe service status rather than credential files or values.
