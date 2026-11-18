---
name: doppler-secrets
description: Use Doppler-managed credentials for trusted commands on this VPS through its local secret service, without giving Codex the bootstrap token.
---

# Doppler secrets

Use the local service when an authorized task needs an application credential. Follow the user's scope and the corresponding product skill. Access to a credential does not authorize messages, writes, or other product operations.

## Run a trusted command

1. Resolve the exact secret names from the task or the names-only mapping at `${CODEX_HOME:-$HOME/.codex}/doppler/integrations.json`, when present. Never inspect credential stores, dump environments, or request values in chat.
2. Locate the bundled `scripts/doppler.py` and use its absolute path. It works in SSH, tmux, Herdr, and ordinary shells without Doppler CLI enrollment.
3. Select each name with `--require NAME` or `--map SOURCE=TARGET`. Only names go in arguments. The application reads values from its environment.
4. Prefer `--clean-env` and explicit `--inherit` names. Without them ordinary ambient variables are retained. Launch only the trusted application. Never launch an entire agent, a tracing shell, an environment dump, or an untrusted program with secrets. Its output passes through without redaction.

```bash
python3 /absolute/path/to/doppler-secrets/scripts/doppler.py run \
  --map NOTION_API_TOKEN=NOTION_TOKEN \
  --clean-env --inherit PATH --inherit HOME -- \
  /usr/bin/node /absolute/path/to/trusted-client.mjs
```

## Check a failure

| Command | What it establishes |
| --- | --- |
| `paths` | Shows the socket location. |
| `doctor --offline` | Checks local service readiness without contacting Doppler. |
| `check --require NAME` | Validates the selected value without printing it. A normal `run` already checks it, so omit a separate check for routine calls. |

These diagnostics cannot establish application authentication. On failure, report the failure and inspect safe service status. Never broaden the selection, bypass the service, or use local credentials or an offline cache as a fallback.

## Service ownership

The root-managed service holds the bootstrap token in a systemd runtime credential. Use a read-only token scoped to the appropriate Doppler config. Any process running as the configured development account can request ordinary names in that config. Selection limits each fetch. Root and processes running as the development account remain trusted.

Ansible owns this skill package. Private profiles outside the package stay in place and the wrapper uses the local service. Rotate `DOPPLER_TOKEN` in GitHub's `development` environment and rerun **Bootstrap VPS**. Keep credential files and secret values out of diagnostics.
