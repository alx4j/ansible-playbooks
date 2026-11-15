# Development VPS playbooks

Configure a development VPS with a `dev` account, sudo and SSH access, automatic updates, Podman, Java, SDKMAN, Gradle, Codex, Moshi, and Mosh.

The managed host needs Debian/Ubuntu, Python 3, OpenSSH, APT, and systemd. The configuration supports AMD64 and ARM64; container tests exercise AMD64 on Debian 13. GitHub Actions provides the controller, so you do not need Ansible installed on your workstation.

## Deploy through GitHub Actions

1. Merge the workflow into the default branch.
2. Follow [deployment setup](docs/github-deployment.md#1-configure-the-environment) to configure a GitHub environment, inventory, SSH key, and trusted host keys.
3. For a new host, [bootstrap the development account](docs/github-deployment.md#2-establish-access). Skip bootstrap if `dev` already has working SSH and sudo access.
4. Run **Actions → Deploy Ansible manually → Run workflow**, selecting the default branch, environment, exact host alias, `site`, and `check`.
5. Review the result, then run again with `apply`. Check mode connects to the host and is a partial simulation; see [its limitations](docs/github-deployment.md#3-check-review-and-apply).

Deployments are manual. Pushes and pull requests run validation only. Keep provider-console access available when changing SSH or sudo.

## Playbooks

| Playbook | Purpose |
| --- | --- |
| `bootstrap.yml` | Connect as root; create the account, sudo access, and approved keys. Leave SSH policy unchanged. |
| `site.yml` | Verify development-user login and sudo, then configure the host. |
| `maintenance.yml` | Upgrade packages; optionally purge unused dependencies and reboot when required. |

## Documentation

- [GitHub deployment](docs/github-deployment.md): environment setup, credentials, and manual runs.
- [Configuration](docs/configuration.md): shared defaults, host overrides, ownership, and account onboarding.
- [Development and local runs](docs/development.md): controller setup, local commands, and verification.
- [Setup history](ansible.md): the historical commands that informed these playbooks.
