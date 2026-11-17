# Development VPS playbooks

Configure a development VPS with a dedicated account, sudo and SSH access, automatic updates, Podman, Java through SDKMAN and jenv, Gradle, Go, Rust/Cargo, Codex, Herdr, Moshi, and Mosh. Development commands are available on the user's PATH, with a configurable projects directory in their home.

Repositories owned by `alx4j` with the GitHub topic selected by `PROJECTS_TOPIC`
(default `work`) are cloned over SSH into that projects directory. Existing
checkouts are left untouched; removing
the topic archives inactive, automation-owned checkouts. This uses the
Doppler-managed GitHub credential; see [work repository setup](docs/work-repositories.md).

The managed host needs Debian/Ubuntu, Python 3, OpenSSH, APT, and systemd. The configuration supports AMD64 and ARM64; tests exercise Debian 13 containers and an Ubuntu 24.04 VM on AMD64. GitHub Actions provides the controller, so you do not need Ansible installed on your workstation.

## Run through GitHub Actions

1. Follow [workflow setup](docs/github-deployment.md#1-configure-the-environment) to save the `SUDO_USER` variable, SSH private key, `SUDO_PASSWORD` secret, and trusted host keys in the `development` GitHub environment. List managed servers in the [development inventory](inventories/development/hosts.yml).
2. Open **Actions → Bootstrap VPS → Run workflow**. Enter the server address and its initial username and password. One run creates the configured account when needed, verifies its key and sudo access, and configures the server. For `dev.alx4j.com`, the initial username is `debian`.
3. Merge later configuration changes to `main`. After validation passes, **Bootstrap VPS** updates inventory servers with working development-account access. It warns and skips servers without that access; first setup remains manual.

You can also rerun a single server manually with just its address once development-account access works. Pull requests run validation without contacting inventory servers. Keep provider-console access available when changing SSH or sudo.

## Local playbooks

The setup workflow runs these stages automatically. They are also available for [local runs](docs/development.md#bootstrap-and-apply).

| Playbook | Purpose |
| --- | --- |
| `bootstrap.yml` | Connect as the initial administrator; create the development account, sudo access, and approved keys. Leave SSH policy unchanged. |
| `site.yml` | Verify development-user login and sudo, then configure the host. |

## Documentation

- [GitHub workflow](docs/github-deployment.md): environment setup, manual bootstrap, and automatic inventory runs.
- [Configuration](docs/configuration.md): shared defaults, host overrides, ownership, and account onboarding.
- [Development and local runs](docs/development.md): controller setup, local commands, and verification.
- [Dependency and tool updates](docs/updates.md): Dependabot, version pins, checksums, and the upgrade procedure.
- [Doppler secrets](docs/doppler.md): encrypted token setup and selected secrets for normal SSH/tmux/Herdr sessions.
- [Herdr and Moshi](docs/herdr.md): persistent workspaces, phone pairing, session discovery, and automatic window titles.
- [Work repositories](docs/work-repositories.md): topic selection, GitHub SSH access, and safe archive/restore behavior.
- [Setup history](ansible.md): the historical commands that informed these playbooks.

## Repository map

```text
ansible-playbooks/
├── bootstrap.yml                 # Create the development account through the initial administrator
├── access.yml                    # Probe existing SSH and sudo access
├── site.yml                      # Configure the server through the development account
├── inventories/development/      # Servers and host-specific settings
├── roles/dev_host/
│   ├── defaults/main.yml         # Shared settings, tool versions, and checksums
│   ├── tasks/                    # Account, tools, services, firewall, and SSH configuration
│   ├── templates/                # Shell environment and service/configuration files
│   ├── files/                    # SSH configuration validator
│   └── handlers/                 # Reload or restart services after changes
├── roles/doppler_secrets/        # Encrypted credential, isolated service, and Codex skill wrapper
├── roles/work_repositories/      # GitHub topic discovery, SSH checkouts, and archive/restore
├── scripts/                      # Controller setup, workflow runner, and test entry points
├── tests/                        # Unit tests, container fixtures, and full VM checks
├── requirements.txt              # One Ansible core pin for controllers and test images
├── requirements-dev.txt          # Additional validation tools
├── requirements.yml              # Ansible collection pins
├── .github/workflows/            # Validation and Bootstrap VPS
├── .github/dependabot.yml         # Scheduled dependency update PRs
└── docs/                         # Setup, configuration, testing, and upgrades
```
