#!/usr/bin/env python3
"""Select semantic release tags for the install/update E2E matrix.

Fork checkouts often have no tags. Selection order:

1. Valid tags already in the local clone.
2. Tags fetched from upstream https://github.com/nousresearch/hermes-agent.git.
3. The known stable fallback list, only if both sources are empty.

Upstream release tags are calendar-shaped (v2026.8.3), not package versions
(v0.20.0). Both shapes are accepted so a successful upstream fetch is not
discarded. The v0.20.x fallback is last-resort only: those names are package
versions and are not git tags on NousResearch/hermes-agent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

UPSTREAM_URL = "https://github.com/nousresearch/hermes-agent.git"
FALLBACK_TAGS = [
    "v0.20.1",
    "v0.20.2",
    "v0.20.3",
    "v0.20.4",
    "v0.20.5",
    "v0.20.6",
]

# Package semver (v0.20.1) and upstream calendar tags (v2026.8.3, v2026.8.3.1).
SEMVER_RE = re.compile(r"^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*))?$")


def _run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag.lstrip("v").split("."))


def valid_tags(names: list[str]) -> list[str]:
    seen: set[str] = set()
    kept: list[str] = []
    for name in names:
        if name in seen or SEMVER_RE.fullmatch(name) is None:
            continue
        seen.add(name)
        kept.append(name)
    return sorted(kept, key=_version_key)


def fetch_repository_tags(repo: Path) -> list[str]:
    """Return valid semantic tags from a local clone. Empty if git has none."""
    if not (repo / ".git").exists() and _run(["git", "-C", str(repo), "rev-parse", "--git-dir"]).returncode != 0:
        return []
    result = _run(["git", "-C", str(repo), "tag", "-l"], cwd=repo)
    if result.returncode != 0:
        print(f"warning: local git tag -l failed: {result.stderr.strip()}", file=sys.stderr)
        return []
    return valid_tags([line.strip() for line in result.stdout.splitlines() if line.strip()])


def fetch_upstream_tags(repo: Path, upstream: str = UPSTREAM_URL) -> list[str]:
    """Fetch upstream tags into the local clone, then re-read them.

    Falls back to ls-remote when the checkout is not a git repository.
    """
    if _run(["git", "-C", str(repo), "rev-parse", "--git-dir"]).returncode == 0:
        fetched = _run(
            ["git", "-C", str(repo), "fetch", "--tags", "--force", upstream, "refs/tags/*:refs/tags/*"],
            cwd=repo,
        )
        if fetched.returncode != 0:
            print(f"warning: upstream tag fetch failed: {fetched.stderr.strip()}", file=sys.stderr)
        return fetch_repository_tags(repo)

    remote = _run(["git", "ls-remote", "--tags", upstream])
    if remote.returncode != 0:
        print(f"warning: git ls-remote failed: {remote.stderr.strip()}", file=sys.stderr)
        return []
    names: list[str] = []
    for line in remote.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].startswith("refs/tags/"):
            continue
        name = parts[1].removeprefix("refs/tags/")
        if name.endswith("^{}"):
            continue
        names.append(name)
    return valid_tags(names)


def select_tags(repo: Path, upstream: str = UPSTREAM_URL) -> tuple[list[str], str]:
    local = fetch_repository_tags(repo)
    if local:
        return local, "local"
    upstream_tags = fetch_upstream_tags(repo, upstream)
    if upstream_tags:
        return upstream_tags, "upstream"
    print(
        "warning: no valid semantic tags locally or upstream; "
        "using package-version fallback. These are not git tags on "
        "nousresearch/hermes-agent (release tags are vYYYY.M.D).",
        file=sys.stderr,
    )
    return list(FALLBACK_TAGS), "fallback"


def github_matrix(tags: list[str], limit: int) -> str:
    chosen = tags[-limit:] if limit > 0 else tags
    return json.dumps({"include": [{"tag": tag} for tag in chosen]}, separators=(",", ":"))


def write_github_output(tags: list[str], source: str, limit: int) -> None:
    matrix = github_matrix(tags, limit)
    chosen = tags[-limit:] if limit > 0 else tags
    payload = f"matrix={matrix}\nsource={source}\ntags={json.dumps(chosen)}\n"
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(payload)
    print(payload, end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pick semantic release tags for install/update E2E.")
    parser.add_argument("--repo", default=".", help="Checkout to read tags from")
    parser.add_argument("--upstream", default=UPSTREAM_URL)
    parser.add_argument("--limit", type=int, default=3, help="Newest N tags for the matrix")
    parser.add_argument("--output-github", action="store_true", help="Write matrix= to GITHUB_OUTPUT and stdout")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    tags, source = select_tags(repo, args.upstream)
    if not tags:
        print("Release tag selection failed: No valid semantic release tags detected.", file=sys.stderr)
        return 1
    if args.output_github:
        write_github_output(tags, source, args.limit)
    else:
        print(json.dumps(tags))
    print(f"Selected {len(tags)} tag(s) from {source}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
