# Work repositories

Add the topic selected by `PROJECTS_TOPIC` (default `work`) to repositories you
own on GitHub. Every **Bootstrap VPS** run then synchronizes those repositories
into the development account's
`$PROJECTS_DIR`. The same rules apply during first setup, manual reruns, and
automatic updates after a validated merge to `main`.

| State | Result |
| --- | --- |
| Tagged repository is absent | Clone its default branch and full history over SSH. |
| Tagged repository is already present | Preserve its branch, commits, files, and Git configuration; do not fetch, pull, or reset. |
| Topic was removed from a managed repository | Move the entire checkout into `$PROJECTS_DIR/.archive/` when it is inactive. |
| Topic is added again | Restore the archived checkout when its destination is free and it is inactive. |
| A destination belongs to an unrelated folder or manual clone | Leave it alone and report a skip. |

Archives retain unpushed commits, local branches, stashes, ignored files, and
uncommitted work. There is no automatic expiration or permanent deletion.
Review and remove archives yourself when they are no longer needed. Archiving
on the same disk is not a backup and does not reclaim disk space.

Topic edits do not trigger the workflow themselves. Run **Bootstrap VPS**
manually or wait for the next validated `main` update. Every enabled inventory
server receives the same selection unless its role settings override it.

## GitHub access

Keep the existing `DOPPLER_TOKEN` in GitHub's `development` environment. Its
Doppler config must contain `GITHUB_TOKEN`, belonging to the configured GitHub
owner and able to discover the relevant public and private repositories.
The workflow's automatically generated GitHub token cannot discover your other
private repositories. No `gh` installation or additional workflow input is needed.

For a fine-grained personal access token, allow **Metadata: read** for the
repositories to discover. Include newly created private repositories in its
selection, or choose all repositories if that matches the intended access.
Automatic SSH enrollment also needs the **Git SSH keys: read/write** user
permission. For classic tokens, the corresponding scopes are `repo` for private
repositories and `write:public_key` for enrollment.

The helper first tests the account's existing GitHub SSH access. Working access
is reused without creating or registering another key. On a fresh VPS, it creates
a dedicated Ed25519 identity:

```text
~/.ssh/id_ed25519_github_work       # Private key, mode 0600; stays on this server
~/.ssh/id_ed25519_github_work.pub   # Public key registered with GitHub
```

The helper tests this dedicated key against GitHub. If it is not authorized, it adds
the public key to the authenticated account through the API. It never replaces
the runner's SSH key or your other SSH settings. Once the server key works,
reruns do not need the GitHub key-management permission. Alternatively, add the
generated `.pub` file yourself in GitHub's **Settings → SSH and GPG keys** and
rerun; do not copy the private key into GitHub or Doppler.

New clones use `git@github.com:<owner>/<repo>.git`. Their local `core.sshCommand`
selects the verified SSH configuration and root-owned GitHub host-key file, so
ordinary `git pull` and `git push` use the same identity. The account's GitHub
permissions determine what it can push. The API token is not passed to Git or
SSH and is never embedded in a clone URL or Git configuration.

GitHub's Ed25519 host key is pinned to its published fingerprint. A mismatch
fails instead of trusting a new key from the network. When retiring a server,
remove it from the inventory and revoke its `work-repositories: <hostname>` SSH
key in GitHub. Existing keys are not automatically revoked by this playbook.

Unattended synchronization does not rely on an interactive SSH agent or prompt
for a key passphrase. Existing SSH access must work in that environment to be
reused; otherwise the dedicated server identity is used.

## Ownership and safeguards

The private manifest lives at
`~/.local/state/work-repositories/<projects-folder>.json`. Each managed checkout
also has an ownership marker inside `.git`. Only checkouts recorded and created
by this automation can be archived. Removing or changing their origin or marker
causes a skip, not adoption or deletion. Repository IDs preserve identity across
GitHub renames; an existing checkout keeps its local directory and origin URL.
GitHub normally redirects the old repository URL; update it yourself if needed.

Before archiving, the helper successfully reads the repository again and confirms
both its identity and the absence of the selected topic. A missing API result,
lost access, owner transfer, or failed request is not treated as topic removal.
Discovery must finish every page before any repository operation begins.

Moves are skipped when the development account has a process working in the
checkout or holding files open, a running Herdr session references it, or Git has
linked worktrees. If activity cannot be inspected, the checkout stays put.
These are conservative checks, not a lock against interactive users starting
work afterward; close the workspace before removing its topic.

Concurrent sync runs are locked. Clones are staged before publishing their
directory, moves refuse to overwrite an existing destination, and pending moves
can recover after interruption. A failed clone is retried on the next run.
Submodules and Git LFS content are not initialized automatically.

Changing `PROJECTS_DIR` selects a separate manifest and leaves the old directory
and its state intact. The workflow reports cloned, kept, archived, restored, and
skipped repositories. Failures remain failures.

## Settings

Set `PROJECTS_TOPIC` in GitHub's **development environment variables** to choose
the topic for both manual runs and automatic updates. Local Ansible runs read
the same controller environment variable. Unset or empty values use `work`.
Topics must contain 1–50 lowercase letters, digits, or hyphens and start with a
letter or digit.

Changing `PROJECTS_TOPIC` changes the selection on the next run. Previously
managed repositories without the new topic can be archived under the same
inactivity and ownership safeguards; no repositories are permanently deleted.

Defaults live in [the work-repositories role](../roles/work_repositories/defaults/main.yml).
Inventory values override the environment-derived defaults when needed:

```yaml
work_repositories_owner: alx4j
work_repositories_topic: work
work_repositories_github_secret: GITHUB_TOKEN  # Doppler secret name, never its value
work_repositories_enabled: true
```

Setting `work_repositories_enabled: false` leaves existing checkouts, archives,
and SSH keys untouched. With synchronization enabled, a working Doppler service
and selected GitHub credential are required; there is no anonymous or cached
fallback.

References: [repository discovery](https://docs.github.com/en/rest/repos/repos#list-repositories-for-the-authenticated-user),
[SSH key registration](https://docs.github.com/en/rest/users/keys#create-a-public-ssh-key-for-the-authenticated-user),
and [GitHub host keys](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints).
