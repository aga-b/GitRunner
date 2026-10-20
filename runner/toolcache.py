#!/usr/bin/env python3
"""Pre-populate /opt/hostedtoolcache so actions/setup-* find tools without downloading."""
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("RUNNER_TOOL_CACHE", "/opt/hostedtoolcache"))
UA = {"User-Agent": "personal-ci-toolcache/1"}


def fetch(url, dest=None):
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=120) as response:
        if dest is None:
            return response.read()
        with open(dest, "wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)


def version_key(value):
    return tuple(int(part) for part in re.findall(r"\d+", value))


def mark(tool, version):
    (ROOT / tool / version / "x64.complete").touch()


def extract(archive, target, strip):
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts[strip:]
            if not parts:
                continue
            member.name = str(Path(*parts))
            tar.extract(member, target, filter="tar")


def node(major):
    index = json.loads(fetch("https://nodejs.org/dist/index.json"))
    version = max((e["version"].lstrip("v") for e in index if e["version"].startswith(f"v{major}.")), key=version_key)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "node.tar.xz"
        fetch(f"https://nodejs.org/dist/v{version}/node-v{version}-linux-x64.tar.xz", archive)
        extract(archive, ROOT / "node" / version / "x64", 1)
    mark("node", version)
    return f"node {version}"


def go(minor):
    releases = json.loads(fetch("https://go.dev/dl/?mode=json&include=all"))
    wanted = [r for r in releases if r.get("stable") and re.fullmatch(rf"go{re.escape(minor)}(\.\d+)?", r["version"])]
    release = max(wanted, key=lambda r: version_key(r["version"]))
    version = release["version"][2:]
    entry = next(f for f in release["files"] if f["os"] == "linux" and f["arch"] == "amd64" and f["kind"] == "archive")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "go.tar.gz"
        fetch("https://go.dev/dl/" + entry["filename"], archive)
        extract(archive, ROOT / "go" / version / "x64", 1)
    mark("go", version)
    return f"go {version}"


def python(minor):
    manifest = json.loads(fetch("https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json"))
    best = None
    for entry in manifest:
        if entry.get("stable") and re.fullmatch(rf"{re.escape(minor)}\.\d+", entry["version"]):
            for file in entry["files"]:
                if file["platform"] == "linux" and file.get("platform_version") == "24.04" and file["arch"] == "x64":
                    if best is None or version_key(entry["version"]) > version_key(best[0]):
                        best = (entry["version"], file["download_url"])
    version, url = best
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "python.tar.gz"
        fetch(url, archive)
        extract(archive, Path(tmp) / "src", 0)
        env = {**os.environ, "AGENT_TOOLSDIRECTORY": str(ROOT)}
        subprocess.run(["bash", "setup.sh"], cwd=Path(tmp) / "src", env=env, check=True,
                       stdout=subprocess.DEVNULL)
    return f"python {version}"


def main():
    jobs = [(node, os.environ.get("TOOLCACHE_NODE", "22 24")), (go, os.environ.get("TOOLCACHE_GO", "")),
            (python, os.environ.get("TOOLCACHE_PYTHON", "3.11 3.12"))]
    for function, versions in jobs:
        for version in versions.split():
            try:
                print("toolcache:", function(version), flush=True)
            except Exception as error:
                print(f"toolcache: skipped {function.__name__} {version}: {type(error).__name__}: {error}",
                      file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
