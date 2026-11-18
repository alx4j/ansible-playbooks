# Manage Codex tool use with TokenEconomizer

TokenEconomizer can prune tools a project rarely uses and nudge Codex when it repeats a failure. Bootstrap installs the plugin once for the development user. Each repository keeps its own state. The loop judge and optional model-based learning use your existing agent account and consume its usage allowance. Savings depend on the workload. [Upstream project](https://github.com/microsoft/TokenEconomizer)

## Finish setup after bootstrap

1. SSH in as the development user, open a project, and start `codex`.
2. Review TokenEconomizer's hooks when prompted, or open `/hooks`. Updates to hook definitions can require another approval.
3. Exit Codex, start a new session, and enter `$tokeneconomizer:status` in the Codex prompt. Restarting the session lets its startup hook run.

If you also use a phone, follow the [Moshi pairing instructions](first-login.md#pair-moshi-with-the-qr-code). TokenEconomizer setup works from an ordinary SSH terminal.

## Managed settings

Choose defaults in inventory. Ansible writes these three settings to `~/.tokeneconomizer/config.json`. A repository's overrides take precedence.

| Inventory setting | Default | Alternatives |
| --- | --- | --- |
| `tokeneconomizer_microskills` | `"off"` | `envfacts` collects supported environment facts without extra model calls. `on` also enables model-based learning across sessions. |
| `tokeneconomizer_toolprune` | `auto` | `propose` lets you review removals. `off` disables pruning. In automatic mode, unused collaboration, goals, or image tools can disappear from later sessions in that project. |
| `tokeneconomizer_loopguard` | `nudge` | `off` disables repeated-failure nudges. A nudge leaves the turn running. |

After the first plugin session, run either command from a project shell to change that project's behavior or restore pruned tools.

```bash
~/.tokeneconomizer/bin/te --host codex config set microskills=envfacts
~/.tokeneconomizer/bin/te --host codex toolprune restore
```

## Disable or remove it

1. Set `tokeneconomizer_enabled: false` in inventory to stop Ansible managing the plugin.
2. To turn the installed plugin off globally while keeping its state, run the following command. Check for repository overrides if a project still behaves differently.

   ```bash
   ~/.tokeneconomizer/bin/te --host codex config set enabled=false --global
   ```

3. For a full uninstall, restore tools in every affected repository first. Then run `codex plugin remove tokeneconomizer@tokeneconomizer` and remove the marketplace.

## Ownership and updates

| Path | Purpose |
| --- | --- |
| `~/.local/share/tokeneconomizer-source/` | Clean, pinned source checkout. Local edits or commits stop an update. |
| `~/.local/share/tokeneconomizer-marketplace/` | Self-contained bundle built by Ansible. |
| `~/.codex/plugins/cache/tokeneconomizer/` | Installed Codex package. |
| `~/.tokeneconomizer/` | Plugin state. Ansible manages the three global keys above. |
| Each project's `.codex/config.toml` | Project-specific settings owned by you, Codex, and the plugin. |

Review the upstream commit, change `tokeneconomizer_revision` in the [role defaults](../roles/tokeneconomizer/defaults/main.yml), and run the [tool integration tests](development.md#verification). Package files and executable modes are compared across runs. An unrelated marketplace registration stops installation. Check mode previews local setup without installing the plugin.

The repository tests the bundle and hook payloads with the pinned Codex CLI and synthetic inputs. They do not make paid judge calls or measure savings. Keep the compatibility check when updating either pin.
