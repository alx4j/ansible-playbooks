# Give Codex the same skills on every VPS

Bootstrap installs a shared set of Codex plugins and global instructions for the development account. The default toolkit is `alx4j/alx4j-agent-toolkit`. Its four plugin packages are `common`, `game-development`, `infrastructure`, and `productivity`.

## 1. Choose the source and plugins

The managing account needs a Doppler-backed GitHub credential and working GitHub SSH access to the toolkit repository. The toolkit has its own checkout, so you can use it even with project sync turned off. A project topic is only needed if you also want a separate editable clone. [GitHub access setup](work-repositories.md#github-access)

### Inventory settings

```yaml
agent_toolkit_enabled: true
agent_toolkit_manage_instructions: true
agent_toolkit_owner: alx4j
agent_toolkit_plugins:
  - common
  - game-development
  - infrastructure
  - productivity
```

Set `agent_toolkit_owner` to the authenticated GitHub user. By default the role looks for `alx4j-agent-toolkit` under that owner. Set `agent_toolkit_repository` for a different compatible SSH repository. Keep `common` in the selection. Codex 0.169.0 needs the toolkit's `.codex-plugin/plugin.json` compatibility manifests. For a host without a suitable toolkit, set `agent_toolkit_enabled: false` before bootstrap. [Role defaults](../roles/agent_toolkit/defaults/main.yml)

## 2. Install and use the toolkit

Run **Bootstrap VPS**, then start a new Codex session to load the global instructions. Each run updates the managed checkout from `main`, registers its local marketplace, and installs missing, changed, or disabled packages. Package contents are checked even when the version number is unchanged.

| Location | What belongs there |
| --- | --- |
| `$PROJECTS_DIR/alx4j-agent-toolkit` | Your editable checkout if you select the repository for project sync. Work on branches here. |
| `~/.local/share/alx4j-agent-toolkit` | Ansible's managed checkout. Leave it clean and on `main` so it can advance safely. |
| `~/.codex/plugins/cache/alx4j-agent-toolkit/` | Installed plugin packages that Codex runs. |
| `~/.codex/AGENTS.md` | Link to `config/codex/AGENTS.md` in the managed checkout. |

The `common` plugin supplies the Doppler skill. Ansible supplies the local credential service and stable client at `/usr/local/lib/doppler-secrets/client.py`. Project sync uses that stable client across plugin upgrades. [Using Doppler](doppler.md)

## 3. Publish a toolkit change

1. Edit the skill, script, manifest, or global instructions in your **editable checkout**.
2. Test the packaged changes with a local marketplace in a separate Codex home. Bump the affected plugin's version in both manifests and merge the toolkit PR.
3. Rerun **Bootstrap VPS** or wait for a validated merge to this bootstrap repository's `main`. A toolkit merge alone starts no deployment.
4. Start a new Codex session to use the updated instructions.

## Existing installations

After all selected plugins install successfully, Ansible backs up an existing `AGENTS.md` and the old standalone `~/.codex/skills/doppler-secrets` under `~/.local/share/agent-toolkit-backups/`. The run reports the backup paths. Then it links the toolkit instructions. An existing nonempty `AGENTS.override.md` still takes precedence and produces a warning.

If the managed checkout has local edits, unpushed commits, a different branch, or no ownership marker, inspect it before rerunning. Move work you want to keep into the editable checkout. A marketplace registered to another source also needs to be resolved before installation proceeds.

Removing a plugin from inventory stops managing it. Disabling the role leaves installed packages and links in place. To supply independent instructions, set `agent_toolkit_manage_instructions: false` before setting `dev_host_codex_instructions_source`. Removing a project topic affects only the editable clone. Check mode skips cloning and installation.
