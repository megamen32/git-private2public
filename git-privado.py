#!/usr/bin/env python3
"""
git-privado — sanitize & mirror a private repo to a public one.

Config-driven wrapper over git-filter-repo:
  - delete paths (globs, dirs, exact files) from history
  - replace text (literal or regex, optionally scoped by glob)
  - scan the result for secrets / private data (fail_on_match)
  - push to the target public repo

Usage:
  git-privado publish --config rules.yaml
  git-privado scan --config rules.yaml       # scan only, don't push
  git-privado init                           # write example config

Install:
  pip install git-filter-repo pyyaml
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pip install pyyaml")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

@dataclass
class Config:
    source: str
    target: str
    delete: list[str] = field(default_factory=list)
    replace: list[str] = field(default_factory=list)
    allow_domains: list[str] = field(default_factory=list)
    fail_on_match: list[str] = field(default_factory=list)
    push_force: bool = True
    push_branches: list[str] = field(default_factory=lambda: ["main"])
    push_tags: bool = False

    @classmethod
    def from_yaml(cls, path: Path) -> "Config":
        data = yaml.safe_load(path.read_text())
        push = data.get("push") or {}
        return cls(
            source=data["source"],
            target=data["target"],
            delete=list(data.get("delete") or []),
            replace=list(data.get("replace") or []),
            allow_domains=list(data.get("allow_domains") or []),
            fail_on_match=list(data.get("fail_on_match") or []),
            push_force=push.get("force", True),
            push_branches=list(push.get("branches") or ["main"]),
            push_tags=push.get("tags", False),
        )


# --------------------------------------------------------------------------- #
# Rule parsing
# --------------------------------------------------------------------------- #

@dataclass
class DeleteRule:
    pattern: str
    is_dir: bool
    is_glob: bool

    @classmethod
    def parse(cls, raw: str) -> "DeleteRule":
        s = raw.strip()
        return cls(
            pattern=s,
            is_dir=s.endswith("/"),
            is_glob=any(c in s for c in "*?["),
        )

    def matches(self, path: str) -> bool:
        if self.is_dir:
            return path.startswith(self.pattern) or path == self.pattern.rstrip("/")
        if self.is_glob:
            return fnmatch.fnmatch(path, self.pattern)
        return path == self.pattern


@dataclass
class ReplaceRule:
    pattern: str
    replacement: str
    is_regex: bool
    file_glob: str | None  # None = all files

    @classmethod
    def parse(cls, raw: str) -> "ReplaceRule":
        # Format: "pattern==>replacement"
        # Optional prefix: "regex:" or "glob:*.json:"
        s = raw.strip()
        is_regex = False
        file_glob = None

        if s.startswith("regex:"):
            s = s[len("regex:"):]
            is_regex = True
        elif s.startswith("glob:"):
            rest = s[len("glob:"):]
            # glob:*.json:pattern==>replacement
            colon = rest.find(":")
            if colon == -1:
                raise ValueError(f"bad glob rule: {raw}")
            file_glob = rest[:colon]
            s = rest[colon + 1:]

        sep = "==>"
        idx = s.find(sep)
        if idx == -1:
            raise ValueError(f"replace rule missing '==>': {raw}")
        return cls(
            pattern=s[:idx],
            replacement=s[idx + len(sep):],
            is_regex=is_regex,
            file_glob=file_glob,
        )


# --------------------------------------------------------------------------- #
# git-filter-repo bridge
# --------------------------------------------------------------------------- #

def run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    if os.environ.get("GITPRIVADO_DEBUG"):
        sys.stderr.write(f"$ {' '.join(cmd)}\n")
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def clone_source(source: str, dest: Path) -> None:
    """Clone the source repo (full history, all branches)."""
    run(["git", "clone", "--mirror", source, str(dest)])
    # Re-init as a normal (non-bare-mirror) working clone so filter-repo is happy.
    # filter-repo works on bare clones too; keep it simple.


def make_filter_repo_args(deletes: list[DeleteRule], replaces_path: Path) -> list[str]:
    args: list[str] = []
    if deletes:
        # filter-repo --invert-paths --path ... --path ...
        args.append("--invert-paths")
        for d in deletes:
            args.extend(["--path", d.pattern.rstrip("/")])
    if replaces_path.exists():
        args.extend(["--replace-text", str(replaces_path)])
    return args


def write_replace_file(path: Path, rules: list[ReplaceRule]) -> None:
    """git-filter-repo --replace-text expects a file with one rule per line.
    Format: 'literal==>replacement' or 'regex:...==>...' or 'glob:*.json:...==>...'
    """
    lines = []
    for r in rules:
        if r.is_regex:
            prefix = "regex:"
        elif r.file_glob:
            prefix = f"glob:{r.file_glob}:"
        else:
            prefix = ""
        lines.append(f"{prefix}{r.pattern}==>{r.replacement}")
    path.write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #

def scan_tree(repo: Path, config: Config) -> list[str]:
    """Return list of violations (pattern + file:line) in the current tree."""
    violations: list[str] = []
    allow_re = re.compile("|".join(re.escape(d) for d in config.allow_domains)) if config.allow_domains else None

    # Compile fail_on_match patterns
    compiled: list[tuple[str, re.Pattern]] = []
    for raw in config.fail_on_match:
        s = raw.strip()
        if s.startswith("regex:"):
            compiled.append((raw, re.compile(s[len("regex:"):].encode())))
        else:
            compiled.append((raw, re.compile(re.escape(s.encode()))))

    # List tracked files
    res = run(["git", "ls-files"], cwd=str(repo))
    files = [f for f in res.stdout.strip().split("\n") if f]

    for fpath in files:
        full = repo / fpath
        if not full.is_file():
            continue
        try:
            data = full.read_bytes()
        except Exception:
            continue
        for raw, pat in compiled:
            for m in pat.finditer(data):
                # Check if it's inside an allowlisted domain context
                ctx = data[max(0, m.start() - 30):m.end() + 30]
                if allow_re and allow_re.search(ctx):
                    continue
                # Find line number
                line = data[:m.start()].count(b"\n") + 1
                snippet = data[m.start():m.end() + 20].decode("utf-8", "replace")[:60]
                violations.append(f"{fpath}:{line}: matches '{raw}' → ...{snippet}...")
    return violations


# --------------------------------------------------------------------------- #
# Publish flow
# --------------------------------------------------------------------------- #

def publish(config: Config, scan_only: bool = False) -> int:
    deletes = [DeleteRule.parse(d) for d in config.delete]
    replaces = [ReplaceRule.parse(r) for r in config.replace]

    with tempfile.TemporaryDirectory(prefix="git-privado-") as tmp:
        tmp_path = Path(tmp)
        work = tmp_path / "work"

        print(f"▸ Cloning {config.source} ...", file=sys.stderr)
        run(["git", "clone", "--no-local", config.source, str(work)])

        # Detach origin (filter-repo removes it anyway)
        run(["git", "remote", "remove", "origin"], cwd=str(work), check=False)

        # Write replace-text file
        replace_file = tmp_path / "replacements.txt"
        if replaces:
            write_replace_file(replace_file, replaces)

        # Run git-filter-repo
        filter_repo = shutil.which("git-filter-repo")
        if not filter_repo:
            # Try as git subcommand
            filter_repo = "git filter-repo"
        fr_args = make_filter_repo_args(deletes, replace_file)
        if not fr_args:
            print("▸ No delete/replace rules — nothing to filter.", file=sys.stderr)
        else:
            print(f"▸ Rewriting history ({len(deletes)} delete rules, {len(replaces)} replace rules) ...", file=sys.stderr)
            cmd = filter_repo.split() + fr_args + ["--force"]
            res = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True)
            if res.returncode != 0:
                sys.stderr.write(res.stderr)
                sys.exit(f"git-filter-repo failed (rc={res.returncode})")

        # Scan the result
        print("▸ Scanning result for secrets / private data ...", file=sys.stderr)
        violations = scan_tree(work, config)
        if violations:
            print(f"\n✗ {len(violations)} violation(s) found in final tree:", file=sys.stderr)
            for v in violations[:30]:
                print(f"  {v}", file=sys.stderr)
            if len(violations) > 30:
                print(f"  ... and {len(violations) - 30} more", file=sys.stderr)
            print("\nRefusing to push. Fix the rules and retry.", file=sys.stderr)
            return 1
        print("✓ No violations found.", file=sys.stderr)

        if scan_only:
            print("▸ Scan-only mode — not pushing.", file=sys.stderr)
            return 0

        # Push to target
        target_url = config.target
        # If target is "owner/repo" shorthand, expand to GitHub HTTPS
        if "/" in target_url and not target_url.startswith(("http", "git@", "ssh://")):
            target_url = f"https://github.com/{target_url}.git"

        # Auth from env if provided
        token = os.environ.get("GITPRIVADO_TOKEN")
        if token and "github.com" in target_url:
            target_url = target_url.replace("https://", f"https://x-access-token:{token}@")

        print(f"▸ Pushing to {target_url} ...", file=sys.stderr)
        run(["git", "remote", "add", "target", target_url], cwd=str(work))

        for branch in config.push_branches:
            push_cmd = ["git", "push"]
            if config.push_force:
                push_cmd.append("--force")
            push_cmd.extend(["target", branch])
            res = subprocess.run(push_cmd, cwd=str(work), capture_output=True, text=True)
            if res.returncode != 0:
                sys.stderr.write(res.stderr)
                sys.exit(f"push of {branch} failed (rc={res.returncode})")

        if config.push_tags:
            res = subprocess.run(
                ["git", "push", "--force", "target", "--tags"],
                cwd=str(work), capture_output=True, text=True
            )
            if res.returncode != 0:
                sys.stderr.write(res.stderr)

        print(f"✓ Done. {config.target} updated.", file=sys.stderr)
        return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

EXAMPLE_CONFIG = """\
# git-privado configuration
source: owner/private-repo
target: owner/public-repo

delete:
  - "secrets/"
  - "*.env"
  - "*.key"
  - "private-config.yml"

replace:
  - "my-secret-token==>***REMOVED***"
  - "10.0.0.==>192.0.2."
  - "regex:[A-Fa-f0-9]{64}==>***REMOVED-HEX***"

allow_domains:
  - "example.com"
  - "github.com"

fail_on_match:
  - "regex:github_pat_[A-Za-z0-9_]{30,}"
  - "regex:sk-[A-Za-z0-9]{40,}"
  - "regex:192\\.168\\."

push:
  force: true
  branches: [main]
  tags: false
"""


def cmd_init(args) -> int:
    out = Path(args.path)
    if out.exists() and not args.force:
        sys.exit(f"{out} exists (use --force to overwrite)")
    out.write_text(EXAMPLE_CONFIG)
    print(f"✓ Wrote example config to {out}")
    return 0


def cmd_publish(args) -> int:
    config = Config.from_yaml(Path(args.config))
    return publish(config, scan_only=args.scan)


def cmd_scan(args) -> int:
    config = Config.from_yaml(Path(args.config))
    return publish(config, scan_only=True)


def main() -> int:
    p = argparse.ArgumentParser(
        prog="git-privado",
        description="Sanitize & mirror a private repo to a public one. Config-driven.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="write an example config")
    p_init.add_argument("path", nargs="?", default=".git-privado.yaml")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_pub = sub.add_parser("publish", help="sanitize + push to target")
    p_pub.add_argument("-c", "--config", default=".git-privado.yaml")
    p_pub.add_argument("--scan", action="store_true", help="scan only, don't push")
    p_pub.set_defaults(func=cmd_publish)

    p_scan = sub.add_parser("scan", help="scan only (no push)")
    p_scan.add_argument("-c", "--config", default=".git-privado.yaml")
    p_scan.set_defaults(func=cmd_scan)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
