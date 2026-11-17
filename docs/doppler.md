# Doppler secrets in normal Codex sessions

Save a **read-only Doppler Service Token** as `DOPPLER_TOKEN` in GitHub's
`development` environment, then run **Bootstrap VPS**. Use a token scoped to
the Doppler config containing the application credentials this VPS should use.
No new workflow input, TPM, interactive enrollment, or Doppler CLI is needed.

When that secret is supplied, `site.yml` configures the `doppler_secrets` role
after the development account exists. Without it, the role is skipped and any
existing service and credential are left unchanged. Set
`doppler_secrets_enabled: false` in inventory to leave this capability unmanaged
on a particular host; this does not uninstall an existing service.

## How it works

```text
GitHub development secret
  → verified SSH + sudo
  → systemd-creds encryption on the VPS
  → dedicated systemd service reads its runtime credential
  → local Unix socket accepts requests from the development account
  → skill wrapper injects the selected application secrets into one command
```

The deployment scripts exclude the Doppler token from inventory generation,
access checks, and initial-user bootstrap. The credential task uses SSH
pipelining and stdin, with Ansible logging and diffs disabled for that task.
Remote module-file retention must remain disabled. Neither the token nor its
hash is written to inventory, command arguments, shell startup files, or logs.

The role keeps ciphertext at
`/etc/credstore.encrypted/doppler-secrets/doppler_token`. `systemd-creds` creates
the root-only host key at `/var/lib/systemd/credential.secret` when needed; the
role preserves it. The service runs as the separate, non-login `doppler-secrets`
account and obtains the decrypted token through `LoadCredentialEncrypted`.
The token is not placed in the service environment. Its runtime credential is
plaintext while the service needs it; persistent storage contains ciphertext.

The root-owned `/run/doppler-secrets.sock` is accessible to the development
account's primary group. The service also verifies the connecting process's UID.
It accepts explicit secret names, not commands, URLs, or wildcard selections.
Only the service contacts Doppler, using verified HTTPS and the API's selected
secrets parameter. Redirects and local secret-cache fallback are disabled.

This works with ordinary SSH, tmux, and Herdr sessions. The multiplexer does not
manage credentials and needs no changes. The bootstrap token stays with the
service; application secrets are available to the command that requests them.

## Use the wrapper

The [toolkit's `common` plugin](agent-toolkit.md) supplies the skill. Resolve its
wrapper relative to the installed skill directory. For automation and direct
commands, Ansible also installs a root-owned client at a stable path. Both use
the same `run`, `check`, `doctor`, `paths`, `--require`, `--map`, and
environment-filtering interface:

```bash
python3 /usr/local/lib/doppler-secrets/client.py run \
  --map NOTION_API_TOKEN=NOTION_TOKEN \
  --clean-env --inherit PATH --inherit HOME -- \
  /usr/bin/node /path/to/trusted-client.mjs
```

Replace the example with the exact names and command required by the task.
Existing names-only `~/.codex/doppler/integrations.json` mappings are preserved.
Applications read values from their environment. Never put values into command
arguments, environment dumps, tracing output, or logs.

`--clean-env` allows only selected secrets and explicitly inherited variables.
Without it, ordinary ambient variables are retained, matching the previous
wrapper; Doppler runtime variables are always stripped. The command keeps its
stdin, working directory, umask, signals, and exit status.

The managed wrapper uses the local service. It does not enroll or restore local
CLI profiles; existing private profiles outside the skill package remain
untouched and unused. Develop the plugin skill in the editable toolkit checkout.
When toolkit management is disabled, the Doppler role installs the standalone
skill at `~/.codex/skills/doppler-secrets` as a fallback.

## Restart and rotation

The service and socket start automatically after a reboot. A service restart
decrypts the existing credential locally, without GitHub or a login session.
Fetching application secrets still requires a working Doppler connection.

To rotate the service token, replace `DOPPLER_TOKEN` in GitHub's `development`
environment and rerun **Bootstrap VPS** for each affected server. The writer
compares the existing decrypted value with the candidate in memory, without
printing or hashing either. A changed token is encrypted and atomically replaces
the ciphertext; the service restarts to load it. An unchanged token preserves
the ciphertext and service start time. No remote Doppler token is automatically
created or revoked.

When code, unit definitions, or the token change, Ansible resets the service's
start-limit counter before restarting it. Planned rotation remains possible
after recent maintenance; the normal crash limit stays in place. Unchanged
runs leave the counter and service untouched.

The deployment verifies local service readiness, not Doppler authentication.
Run the wrapper's `check --require NAME` for an intended application secret to
verify a replacement token before revoking the old one. A failed fetch stops the
command. Updating an application secret in Doppler affects the next wrapper
invocation; an already running application keeps its previously injected value.

## Permissions and recovery

Host-key encryption protects against ordinary users reading the stored token.
Root, or anyone with both the ciphertext and the host key, can decrypt it. A
complete disk copy therefore includes the means to decrypt it. The development
account already has sudo access, so this arrangement does not isolate secrets
from that administrator. It prevents routine Codex commands from needing or
inheriting the bootstrap token.

Any process running as the allowed development account can request ordinary
secret names within the Service Token's config. Explicit selection limits each
fetch and command environment; it is not a per-skill authorization policy.
Scope the Doppler config accordingly. Commands receiving application secrets
must remain trusted; the wrapper cannot redact arbitrary application output.

Preserve the host key for recovery. If it is lost, existing ciphertext cannot be
decrypted and setup fails visibly. Do not replace the system-wide key to rotate
one token. Recover the key or explicitly remove only this role's unusable
encrypted credential before provisioning a replacement from GitHub.

For safe diagnostics, run the wrapper's `doctor --offline` or inspect
`systemctl status doppler-secrets.service doppler-secrets.socket`. Offline health
does not contact Doppler. Do not print runtime credential files or decrypted
tokens while debugging.

References: [systemd credential encryption](https://www.freedesktop.org/software/systemd/man/252/systemd-creds.html),
[service credentials](https://www.freedesktop.org/software/systemd/man/252/systemd.exec.html#Credentials),
[Doppler selected-secret API](https://docs.doppler.com/reference/secrets-download),
and [Doppler Service Tokens](https://docs.doppler.com/docs/service-tokens).
