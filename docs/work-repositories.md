# Bring your GitHub projects onto the VPS

Tag a repository on GitHub and the next bootstrap run clones it into your projects folder and opens a Herdr workspace for it. From then on, you control the branch, commits, and working files. Use your usual `git pull` and `git push`.

## 1. Choose the projects

1. Set `work_repositories_owner` in inventory to the GitHub **user** whose repositories you want. The default is `alx4j`.
2. Add the `work` topic to each repository you want on the server. To use a different topic, set `PROJECTS_TOPIC` in GitHub's `development` environment, or set `work_repositories_topic` for a single host.
3. Choose the destination folder with `PROJECTS_DIR` if needed. Its default is `projects` under the development user's home. [Folder settings](configuration.md#projects-directory)

Topic names start with a lowercase letter or digit and contain 1–50 lowercase letters, digits, or hyphens. Blank `PROJECTS_TOPIC` uses `work`. Changing the selection leaves existing checkouts and their local work in place.

## GitHub access

Set up these two credentials before the first sync.

| Credential | Where it belongs | What it does |
| --- | --- | --- |
| `DOPPLER_TOKEN` | GitHub's `development` environment secret | Lets the VPS fetch the selected application secret from its Doppler config. |
| `GITHUB_TOKEN` | Inside that Doppler config | Authenticates as the configured owner and discovers the selected public and private repositories. |

For a fine-grained GitHub token, grant **Metadata: read** for the repositories to discover. Include new private repositories in its selection when you create them. Automatic enrollment of a fresh VPS also needs the **Git SSH keys: read/write** user permission. For a classic token the equivalent scopes are `repo` for private repositories and `write:public_key` for enrollment. The workflow's built-in token is scoped to the workflow repository.

The setup tests the development account's existing noninteractive GitHub SSH access first. If needed, it generates `~/.ssh/id_ed25519_github_work` and verifies that the Doppler token belongs to the configured GitHub owner before registering the **public** key. To enroll it manually, add `~/.ssh/id_ed25519_github_work.pub` in GitHub's **Settings → SSH and GPG keys**, then rerun. Working server access can be reused without key-management permission. [Doppler setup](doppler.md) · [GitHub key registration](https://docs.github.com/en/rest/users/keys#create-a-public-ssh-key-for-the-authenticated-user)

New clones use SSH and a pinned GitHub host key. Each clone's `core.sshCommand` selects the verified configuration for future pulls and pushes. Keep the private key on the server. When retiring the host, remove it from inventory and revoke its `work-repositories: <hostname>` key on GitHub. [GitHub host key fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)

## 2. Run sync and open a workspace

1. Run **Bootstrap VPS** or wait for a validated `main` update. Editing a topic alone does not start a run.
2. Read the workflow results for cloned, kept, restored, and skipped projects. The `herdr` result names the session and the workspaces it created or kept.
3. SSH in as the development user and run `herdr`. Select a workspace to open its checkout. Start `codex` or a build when you're ready. [Using workspaces](herdr.md)

| What the sync finds | What it does |
| --- | --- |
| Selected repository is missing | Clones its default branch and full history. Initialize submodules or Git LFS content yourself if needed. |
| Managed checkout already exists | Preserves the branch, files, commits, and Git config. Reuses the Herdr workspace or recreates a missing one. |
| Destination contains an unrelated directory or a manual clone | Leaves it in place and reports a skip. |
| Repository no longer matches `PROJECTS_TOPIC` | Leaves the checkout and existing workspace in place. |
| Project archived by an older version is selected again | Moves it back if it is inactive and the destination is free. |

## Project workspaces

Sync starts the configured Herdr session in the background when there are projects to open. Each new workspace starts a shell in the checkout and uses its directory name as the initial label. It preserves existing labels, layouts, running shells, and focus. A workspace uses the existing clone and branch. If workspace setup fails after cloning, rerun once Herdr works. The successful clone is still there.

## Restore an older archive

If an older version moved your checkout into `$PROJECTS_DIR/.archive/`, select the repository with the configured topic and rerun sync. The restore uses the original checkout, including unpushed commits, stashes, and local files. The destination must be free and the archived checkout must be inactive. A referenced Herdr workspace, a linked worktree, an open file, or an inconclusive activity check postpones the restore.

A private manifest under `~/.local/state/work-repositories/<projects-folder>.json` and a marker inside `.git` identify owned checkouts. A GitHub rename preserves the local directory and origin. Sync runs are locked, clones are staged, and interrupted moves can recover. Changing the projects folder selects a new manifest.

## Settings

```yaml
work_repositories_owner: alx4j
work_repositories_topic: work
work_repositories_github_secret: GITHUB_TOKEN
work_repositories_enabled: true
work_repositories_herdr_session: default
```

The secret setting is a **name in Doppler**. Disabling sync leaves checkouts, archives, SSH keys, and sessions in place. To use a different Herdr session, set its name and connect with `herdr --session <name>`. Existing workspaces stay in their original session. [Role defaults](../roles/work_repositories/defaults/main.yml)
