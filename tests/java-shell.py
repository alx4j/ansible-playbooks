"""Verify real SDKMAN/jenv shell integration inside the disposable tool container."""

import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

import yaml


def main():
    account = pwd.getpwuid(os.getuid())
    assert account.pw_name == "ansibletest", "Run this test as the disposable container account"
    home = Path(account.pw_dir)
    defaults = yaml.safe_load((Path(__file__).resolve().parents[1] / "roles/dev_host/defaults/main.yml").read_text())
    alias = defaults["dev_host_jenv_java_alias"]
    java_home = home / ".sdkman/candidates/java" / defaults["dev_host_java_version"]
    jenv = home / ".jenv"
    environment_file = home / ".config/dev-environment/environment.sh"
    fixture = home / ".java-shell-test"
    project = fixture / "project"
    outside = fixture / "outside"
    alternate = fixture / "alternate-jdk"
    alternate_alias = "project-fixture"
    if sys.argv[1:] == ["--prepare"]:
        # A distinct fixture executable proves that project selection changes
        # command resolution, without downloading a second full JDK.
        for directory in (project / "nested", outside, alternate / "bin"):
            directory.mkdir(parents=True)
        (project / ".java-version").write_text(alternate_alias + "\n")
        for binary in ("java", "javac"):
            path = alternate / "bin" / binary
            path.write_text("#!/bin/sh\nprintf 'project-java-fixture\\n'\n")
            path.chmod(0o755)
        (jenv / "versions" / alternate_alias).symlink_to(alternate)
    else:
        assert not sys.argv[1:], "Unexpected test arguments"
    assert (jenv / "versions" / alternate_alias).resolve(strict=True) == alternate
    assert (project / ".java-version").read_text().strip() == alternate_alias
    assert (jenv / "versions" / alias).resolve(strict=True) == java_home
    assert (jenv / "plugins/export").is_symlink()
    assert "sdkman_auto_env=false" in (home / ".sdkman/etc/config").read_text().splitlines()

    environment = {"HOME": str(home), "USER": account.pw_name, "LOGNAME": account.pw_name,
                   "SHELL": "/bin/bash", "PATH": "/usr/local/bin:/usr/bin:/bin", "TERM": "dumb"}

    def shell(command, *options):
        result = subprocess.run(["/bin/bash", *options, "-c", "set -e\n" + command],
                                cwd=outside, env=environment, text=True, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    assert "export ANSIBLE_TEST_LOGIN_MARKER=preserved" in (home / ".bash_profile").read_text()
    shell('test "$ANSIBLE_TEST_LOGIN_MARKER" = preserved', "--login")

    # Login and non-login interactive shells must initialize Java immediately.
    # Sourcing the common environment again must not duplicate PATH entries.
    selected = f'''
test "$(jenv version-name)" = "{alias}"
test "$(command -v java)" = "{jenv}/shims/java"
test "$(readlink -f "$JAVA_HOME")" = "{java_home}"
type _jenv_export_hook > /dev/null
'''
    check = f'''
source "{environment_file}"
source "{environment_file}"
{selected}
java -version
javac -version
gradle --version
python3 -c 'import json, os; print(json.dumps(os.environ["PATH"].split(":")))'
'''
    for options in (("--login", "-i"), ("-i",), ("--noprofile", "--norc")):
        output = shell((selected if "-i" in options else "") + check, *options)
        assert "Gradle " + defaults["dev_host_gradle_version"] in output, output
        path = json.loads(output.splitlines()[-1])
        assert path.count(str(jenv / "shims")) == 1, path
        assert path.count(str(jenv / "bin")) == 1, path

    # The export plugin updates JAVA_HOME before each interactive prompt.
    # Invoke that installed prompt hook after cd to simulate the next prompt.
    shell(f'''
cd "{project}/nested"
eval "$PROMPT_COMMAND"
test "$(jenv version-name)" = "{alternate_alias}"
test "$(readlink -f "$JAVA_HOME")" = "{alternate}"
test "$(java -version)" = project-java-fixture
cd "{outside}"
eval "$PROMPT_COMMAND"
test "$(jenv version-name)" = "{alias}"
test "$(readlink -f "$JAVA_HOME")" = "{java_home}"
java -version
# Exported jenv state must not prevent a new shell from initializing its hooks.
bash -ic 'set -e; test "$(command -v java)" = "$HOME/.jenv/shims/java"; test "$(jenv version-name)" = "{alias}"; type _jenv_export_hook > /dev/null'
''', "-i")
    print("Java defaults, JAVA_HOME, Gradle, project switching, nested shells, and preserved project settings passed")


if __name__ == "__main__":
    main()
