# TokenEconomizer for Codex

Bootstrap installs [TokenEconomizer](https://github.com/microsoft/TokenEconomizer)
once for the development account. It keeps separate repository state under
`~/.tokeneconomizer`; no per-project installation or API key is required.

Ansible checks out a pinned upstream commit, builds its self-contained plugin,
and registers the bundle with Codex. Package contents and executable permissions
are compared before reinstalling, including when upstream changes without a
plugin version bump. An unchanged run leaves the installed package in place.

## Finish setup after bootstrap

Connect as the development account using an SSH key already authorized on the
server. Bootstrap authorizes the deployment key from GitHub's
`ANSIBLE_SSH_PRIVATE_KEY`; your workstation needs its original private key or
another authorized key. Bootstrap does not distribute private keys to devices.

1. Complete [Moshi Easy Pair](configuration.md#pair-moshi-with-the-qr-code) in
   your terminal if this phone has not been paired with the host.
2. Run `codex` in a project and review TokenEconomizer's hooks when prompted,
   or open `/hooks`. Ansible does not bypass Codex's hook trust checks.
3. Exit Codex and start a new session so the session-start hook runs. Enter
   `$tokeneconomizer:status` in the Codex prompt to check the plugin.

Updates that change hook definitions may require another review. Installing the
plugin does not restart any running agent or Herdr session.

## Managed settings

Defaults live in [the role](../roles/tokeneconomizer/defaults/main.yml):

```yaml
tokeneconomizer_enabled: true
tokeneconomizer_microskills: "off"
tokeneconomizer_toolprune: auto
tokeneconomizer_loopguard: nudge
```

- `tokeneconomizer_microskills`: `off`, `envfacts` for supported environment
  facts without additional model calls, or `on` for facts plus model-based
  learning across sessions. Upstream leaves it off by default.
- `tokeneconomizer_toolprune`: `auto`, `propose`, or `off`. Codex can lose
  persistently unused collaboration, goals, or image packages in later sessions.
  Use `propose` to review removals before applying them.
- `tokeneconomizer_loopguard`: `nudge` redirects repeated failures, or `off`.
  The plugin never stops a turn itself.

The loop judge and model-based learning use the agent's existing account and
consume its usage allowance. No benchmark savings are guaranteed for a project.

Ansible owns these three keys in `~/.tokeneconomizer/config.json`. Other global
settings, learned state, and per-repository overrides are preserved. Change the
inventory values to change the managed global defaults. From a project terminal,
after the first plugin session, you can set an override or restore tools:

```bash
~/.tokeneconomizer/bin/te --host codex config set microskills=envfacts
~/.tokeneconomizer/bin/te --host codex toolprune restore
```

Setting `tokeneconomizer_enabled: false` stops Ansible management; it does not
uninstall or disable an existing plugin. To disable it for the user while
retaining its state:

```bash
~/.tokeneconomizer/bin/te --host codex config set enabled=false --global
```

Repository overrides take precedence over global settings. To uninstall, first
restore tools in every affected repository, then use `codex plugin remove
tokeneconomizer@tokeneconomizer` and remove the marketplace. Disable Ansible
management first so the next run does not reinstall it.

## Ownership and updates

| Path | Owner |
| --- | --- |
| `~/.local/share/tokeneconomizer-source/` | Ansible's pinned source checkout; local changes or commits stop reconciliation. |
| `~/.local/share/tokeneconomizer-marketplace/` | Ansible's generated, self-contained bundle. |
| `~/.codex/plugins/cache/tokeneconomizer/` | Codex's installed package, reconciled by Ansible. |
| `~/.tokeneconomizer/` | Plugin state; only the three documented global keys are managed. |
| Each project's `.codex/config.toml` | User/Codex/TokenEconomizer configuration; this role does not edit it. |

To update, review a new upstream commit, change `tokeneconomizer_revision`, and
run the repository checks and tools integration test. Check mode validates
inputs and previews helper/directory changes without downloading, installing,
or configuring the plugin. Normal reruns preserve a marketplace registered from
another path and fail with an explanation instead of taking it over.

The integration test uses pinned Codex 0.169.0, checks package and hook discovery,
and exercises hook payloads without model requests. It does not measure savings
or validate paid judge calls against a live account. Upstream's published Codex
test range currently ends at 0.164.0; keep these compatibility checks when
changing either pin.
