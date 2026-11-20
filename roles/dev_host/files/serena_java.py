"""Merge the managed JDK into Serena's user configuration under its registry lock."""

from copy import deepcopy
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from filelock import FileLock
from ruamel.yaml import YAML
from serena.constants import SERENA_CONFIG_TEMPLATE_FILE


def configure(config, java_home, version):
    java = config.setdefault("ls_specific_settings", {}).setdefault("java", {})
    before = deepcopy(java)
    java["gradle_wrapper_enabled"] = True
    java["gradle_java_home"] = java_home
    runtimes = java.setdefault("runtimes", [])
    name = f"JavaSE-{version}"
    runtime = next((entry for entry in runtimes if entry.get("name") == name), None)
    if runtime is None:
        runtimes.append({"name": name, "path": java_home})
    else:
        runtime["path"] = java_home
    return java != before


def main():
    path = Path.home() / ".serena/serena_config.yml"
    path.parent.mkdir(mode=0o700, exist_ok=True)
    yaml = YAML()
    with FileLock(str(path) + ".lock"):
        source = path if path.exists() else Path(SERENA_CONFIG_TEMPLATE_FILE)
        config = yaml.load(source)
        if configure(config, *sys.argv[1:]):
            with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    yaml.dump(config, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            print("updated")


if __name__ == "__main__":
    main()
