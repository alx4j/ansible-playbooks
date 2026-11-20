"""Follow cross-file calls over Serena MCP using the development user's installed servers."""

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# Each caller has the same symbol spelling; the declaration alone contains 73.
FIXTURES = {
    "go": {
        "go.mod": "module example.test/navigation\n\ngo 1.23\n",
        "caller.go": "package navigation\n\nfunc Result() int { return Answer() }\n",
        "answer.go": "package navigation\n\nfunc Answer() int { return 73 }\n",
    },
    "rust": {
        "Cargo.toml": '[package]\nname = "navigation"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/lib.rs": "mod value;\npub fn result() -> i32 { value::answer() }\n",
        "src/value.rs": "pub fn answer() -> i32 { 73 }\n",
    },
    "java": {
        "pom.xml": '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
                   '<groupId>example</groupId><artifactId>navigation</artifactId><version>1</version>'
                   '<properties><maven.compiler.release>25</maven.compiler.release></properties></project>\n',
        "src/main/java/example/Caller.java": "package example;\nclass Caller { int result() { return Value.answer(); } }\n",
        "src/main/java/example/Value.java": "package example;\nclass Value { static int answer() { return 73; } }\n",
    },
}


async def check(language, files):
    manifest, caller, declaration = files
    with TemporaryDirectory(prefix=f"serena-{language}-", dir=Path.home()) as directory:
        root = Path(directory)
        for name in (manifest, caller, declaration):
            path = root / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_text(files[name])
        (root / ".serena").mkdir()
        (root / ".serena/project.yml").write_text(f"project_name: {root.name}\nlanguage_servers: [{language}]\n")
        server = StdioServerParameters(
            command=str(Path.home() / ".local/bin/serena"), cwd=root,
            args=["start-mcp-server", "--context", "codex", "--agent-interface", "tools",
                  "--project-from-cwd", "--enable-web-dashboard", "false", "--tool-timeout", "300"],
            env={key: os.environ[key] for key in ("HOME", "PATH", "JAVA_HOME", "SERENA_HOME", "CARGO_HOME",
                                                   "RUSTUP_HOME", "GOPATH", "GOCACHE") if key in os.environ},
        )
        async with stdio_client(server) as streams, ClientSession(*streams, read_timeout_seconds=360) as client:
            await client.initialize()
            result = await client.call_tool("find_declaration", {
                "relative_path": caller, "regex": r"(?i)(answer)\(", "include_body": True,
            })
            output = "\n".join(part.text for part in result.content if part.type == "text")
            assert not result.is_error and declaration in output and "73" in output, (language, output)
            print(f"{language}: {caller} -> {declaration}", flush=True)


async def main():
    for language, files in FIXTURES.items():
        await check(language, files)


if __name__ == "__main__":
    asyncio.run(main())
