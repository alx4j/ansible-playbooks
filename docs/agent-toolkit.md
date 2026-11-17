# Codex agent toolkit

Every **Bootstrap VPS** run updates a dedicated checkout of
`alx4j/alx4j-agent-toolkit` from `main`, registers its local marketplace, installs
the selected Codex plugins, and links the toolkit's global instructions. The
default plugins are `common`, `game-development`, `infrastructure`, and
`productivity`.

The toolkit needs the same Doppler-managed `GITHUB_TOKEN` and verified GitHub SSH
access as [project synchronization](work-repositories.md#github-access). It works
independently of `PROJECTS_TOPIC` and `work_repositories_enabled`. No additional
GitHub environment variable or secret is required. The pinned Codex 0.169.0
requires `.codex-plugin/plugin.json` compatibility manifests in the toolkit.

## Source, installation, and development

| Location | Owner and behavior |
| --- | --- |
| `$PROJECTS_DIR/alx4j-agent-toolkit` | Your editable checkout. Add the configured `PROJECTS_TOPIC` to the GitHub repo to have it cloned when absent. Existing branches, commits, and files are preserved. |
| `~/.local/share/alx4j-agent-toolkit` | Ansible's dedicated checkout. Each run updates it from `main`; local changes, another branch, or unpushed commits stop the update. |
| `~/.codex/plugins/cache/alx4j-agent-toolkit/` | Codex's installed packages. Ansible compares versions and package contents and installs only missing, disabled, or changed packages. |
| `~/.codex/AGENTS.md` | Symlink to the managed checkout's `config/codex/AGENTS.md`. Start a new Codex session to load updated instructions. |

Edit skills, scripts, manifests, and global instructions in the working copy.
Test packaged changes through a local marketplace in a separate Codex home so
the development marketplace does not replace the normal installation. Increment
the affected plugin's version in both manifests and merge your toolkit PR.

Then rerun **Bootstrap VPS**. A validated merge to `ansible-playbooks/main` also
updates the managed toolkit on reachable inventory servers. A merge to the
toolkit repository alone does not trigger server updates. There is no polling
service or cross-repository trigger.

Removing the topic can archive the inactive working copy under the usual project
safeguards. It does not affect the managed checkout, installed plugins, or global
instructions. A dirty managed checkout is preserved with an error; move that work
into the editable checkout before retrying. Existing unmarked directories at the
managed path are not adopted or overwritten.

## Existing installations

After all selected plugins install successfully, Ansible moves an existing
`AGENTS.md` and the old standalone `~/.codex/skills/doppler-secrets` into unique
directories under `~/.local/share/agent-toolkit-backups/`. Local customizations are
retained, and the run reports the backup paths. It then creates the instructions
symlink. Other standalone skills and unrelated Codex configuration remain intact.

An existing nonempty `AGENTS.override.md` takes precedence over `AGENTS.md`; the
run reports a warning and preserves it. A toolkit marketplace already registered
to another checkout stops the installation instead of replacing that source.

The `common` plugin owns the Doppler skill. Ansible continues to own the encrypted
credential service and the stable automation client at
`/usr/local/lib/doppler-secrets/client.py`. Project synchronization uses that
stable client, so plugin upgrades do not change its executable path. Plugins do
not contain credentials or install the system service.

## Inventory settings

Defaults live in [the toolkit role](../roles/agent_toolkit/defaults/main.yml).

```yaml
agent_toolkit_enabled: true
agent_toolkit_manage_instructions: true
agent_toolkit_plugins:
  - common
  - game-development
  - infrastructure
  - productivity
```

`common` is required. Removing another plugin from the list stops managing it;
it does not uninstall an existing package. Disabling the role leaves the
checkout, installed plugins, and instruction symlink intact. Set
`agent_toolkit_manage_instructions: false` before supplying an independent
`dev_host_codex_instructions_source`.

Check mode does not clone the toolkit or install plugins. The configuration
tracks the current toolkit `main`; it is repeatable for unchanged inputs, not a
historical package lock.
