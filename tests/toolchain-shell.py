"""Verify tool commands and real builds as the disposable development account."""

import json
import os
from pathlib import Path
import pwd
import shlex
import subprocess
import sys
import tomllib

import yaml


def main():
    account = pwd.getpwuid(os.getuid())
    assert account.pw_name == "ansibletest", "Run inside the disposable tools container"
    home = Path(account.pw_dir)
    defaults = yaml.safe_load((Path(__file__).resolve().parents[1] / "roles/dev_host/defaults/main.yml").read_text())
    projects = home / (os.environ.get("PROJECTS_DIR") or "projects")
    fixture = projects / "toolchain-fixture"
    go_project = fixture / "go-project"
    rust_project = fixture / "rust-project"
    environment_file = home / ".config/dev-environment/environment.sh"
    prepare = sys.argv[1:] == ["--prepare"]
    assert prepare or not sys.argv[1:], "Unexpected test arguments"
    assert projects.is_dir() and not projects.is_symlink()
    assert projects.stat().st_mode & 0o777 == 0o700
    assert projects.stat().st_uid == account.pw_uid

    files = {
        fixture / "keep.txt": "Keep existing projects on reruns\n",
        fixture / "bin/codex": '#!/bin/sh\nprintf "%s\\n" "$@"\n',
        go_project / "go.mod": "module example.com/ansible-go-fixture\n\ngo 1.20\n",
        go_project / "main.go": 'package main\nimport "fmt"\nfunc main() { fmt.Println("go-build-ok") }\n',
        rust_project / "Cargo.toml": '[package]\nname = "ansible-rust-fixture"\nversion = "0.1.0"\nedition = "2021"\n',
        rust_project / "src/main.rs": 'fn main() { println!("cargo-build-ok"); }\n',
        rust_project / "rust-toolchain.toml": f'[toolchain]\nchannel = "{defaults["dev_host_rust_version"]}"\n',
    }
    for path, content in files.items():
        if prepare:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        else:
            assert path.read_text() == content, f"Existing project changed: {path}"
    if prepare:
        (fixture / "bin/codex").chmod(0o755)
    settings = tomllib.loads((home / ".rustup/settings.toml").read_text())
    assert settings["auto_self_update"] == "disable", settings

    environment = {"HOME": str(home), "USER": account.pw_name, "LOGNAME": account.pw_name,
                   "SHELL": "/bin/bash", "PATH": "/usr/local/bin:/usr/bin:/bin", "TERM": "dumb"}

    def shell(command, *options):
        result = subprocess.run(["/bin/bash", *options, "-c", "set -e\n" + command],
                                cwd=home, env=environment, text=True, capture_output=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    commands = {
        "java": home / ".jenv/shims/java",
        "gradle": home / ".sdkman/candidates/gradle/current/bin/gradle",
        "go": home / ".local/share/go/current/bin/go",
        "rustc": home / ".cargo/bin/rustc",
        "cargo": home / ".cargo/bin/cargo",
        "herdr": home / ".local/bin/herdr",
        "bun": home / ".local/bin/bun",
        "moshi-hook": home / ".local/bin/moshi-hook",
    }
    selected = "\n".join(f'test "$(command -v {name})" = {shlex.quote(str(path))}' for name, path in commands.items())
    selected += f'\ntest "$PROJECTS_DIR" = {shlex.quote(str(projects))}\ntest "$GOTOOLCHAIN" = local\n'
    source = f"source {shlex.quote(str(environment_file))}\n"
    for options in (("--login", "-i"), ("-i",), ("--login",), ("--noprofile", "--norc")):
        # Normal shells must work before an explicit source; plain scripts source once.
        initialize = source if "--noprofile" in options else ""
        output = shell(initialize + selected + source * 2 + selected + '''
go version
rustc --version
cargo --version
herdr --version
bun --version
python3 -c 'import json, os; print(json.dumps(os.environ["PATH"].split(":")))'
''', *options)
        assert "go version go" + defaults["dev_host_go_version"] + " " in output, output
        assert "rustc " + defaults["dev_host_rust_version"] + " " in output, output
        assert "cargo " + defaults["dev_host_rust_version"] + " " in output, output
        assert "herdr " + defaults["dev_host_herdr_version"] in output, output
        assert defaults["dev_host_bun_version"] in output.splitlines(), output
        path = json.loads(output.splitlines()[-1])
        for directory in {str(binary.parent) for binary in commands.values()} | {str(home / "go/bin")}:
            assert path.count(directory) == 1, path

        # Exercise argument forwarding, repeated loading, and the explicit bypass.
        arguments = shell(initialize + source * 2 + f'''
export PATH={shlex.quote(str(fixture / "bin"))}:"$PATH"
codex resume "session with spaces"
command codex --version
''', *options).splitlines()
        expected = ["resume", "session with spaces", "--version"]
        if "-i" in options:
            expected.insert(0, "--no-daemon")
        assert arguments == expected, (options, arguments)

    shell(f'''
cd {shlex.quote(str(go_project))}
go install .
test "$(ansible-go-fixture)" = go-build-ok
cd {shlex.quote(str(rust_project))}
test "$(cargo run --offline --quiet)" = cargo-build-ok
''', "--login")
    print("Java/Gradle/Go/Rust PATH, builds, projects, repeated shell loading, and Codex alias behavior passed")


if __name__ == "__main__":
    main()
