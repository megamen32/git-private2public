#!/usr/bin/env python3
"""
git-private2public & mirror a private repo to a public one.

Config-driven wrapper over git-filter-repo:
  - delete paths (globs, dirs, exact files) from history
  - replace text (literal or regex, optionally scoped by glob)
  - scan the result for secrets / private data (fail_on_match)
  - push to the target public repo

Usage:
  git-private2public publish --config rules.yaml
  git-private2public scan --config rules.yaml       # scan only, don't push
  git-private2public init                           # write example config

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
    delete: list[str] = field(default_factory=list)  # alias: ignore
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
            delete=list(data.get("delete") or data.get("ignore") or []),
            replace=list(data.get("replace") or []),
            allow_domains=list(data.get("allow_domains") or []),
            fail_on_match=list(data.get("fail_on_match") or []),
            push_force=push.get("force", True),
            push_branches=list(push.get("branches") or ["main"]),
            push_tags=push.get("tags", False),
        )

    @classmethod
    def from_folder(cls, folder: Path) -> "Config":
        """Load from a .gitpublic/ folder — each file is one concern, gitignore-style.

        Files (all optional — if missing, that setting is empty):
          config      — source=, target=, push_force=, push_branches= (key=value)
          ignore      — one path/glob per line (# for comments)
          replace     — old==>new per line (regex: prefix supported, glob:*.ext: scoped)
          scan        — one regex/literal per line (fail_on_match)
          allow       — one domain per line
        """
        def read_lines(name: str) -> list[str]:
            f = folder / name
            if not f.exists():
                return []
            lines = []
            for line in f.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                lines.append(line)
            return lines

        # config file — simple key=value
        source = ""
        target = ""
        push_force = True
        push_branches = ["main"]
        push_tags = False
        cfg_file = folder / "config"
        if cfg_file.exists():
            for line in cfg_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k == "source":
                    source = v
                elif k == "target":
                    target = v
                elif k == "push_force":
                    push_force = v.lower() in ("true", "yes", "1")
                elif k == "push_branches":
                    push_branches = [b.strip() for b in v.split(",") if b.strip()]
                elif k == "push_tags":
                    push_tags = v.lower() in ("true", "yes", "1")

        return cls(
            source=source,
            target=target,
            delete=read_lines("ignore"),
            replace=read_lines("replace"),
            allow_domains=read_lines("allow"),
            fail_on_match=read_lines("scan"),
            push_force=push_force,
            push_branches=push_branches,
            push_tags=push_tags,
        )

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        """Auto-detect: .gitpublic/ folder OR .yaml file."""
        p = Path(path)
        if p.is_dir():
            return cls.from_folder(p)
        # If path doesn't exist, try .gitpublic/ folder in same dir
        if not p.exists():
            folder = p.parent / ".gitpublic"
            if folder.is_dir():
                return cls.from_folder(folder)
        return cls.from_yaml(p)


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
    if os.environ.get("GIT_PRIVATE2PUBLIC_DEBUG"):
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

    with tempfile.TemporaryDirectory(prefix="git-private2public-") as tmp:
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
        token = os.environ.get("GIT_PRIVATE2PUBLIC_TOKEN")
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
# git-private2public config
# Easy mode: just list files to NOT publish. Like .gitignore.

source: owner/private-repo
target: owner/public-repo

ignore:          # these won't be in the public repo
  - ".env"
  - "secrets/"
  - "*.key"

# --- medium mode (uncomment to scrub secrets inside files) ---
# replace:
#   - "10.0.0.5==>203.0.113.5"
#   - "real-token==>***"

# --- hard mode (uncomment to refuse push if these survive) ---
# fail_on_match:
#   - "regex:github_pat_[A-Za-z0-9_]{30,}"
#   - "regex:192\\.168\\."

push:
  force: true
  branches: [main]
"""


def cmd_hook(args) -> int:
    """Install / remove / show the local git pre-push hook."""
    repo_root = find_git_root(Path.cwd())
    if not repo_root:
        sys.exit("Not inside a git repo.")

    hook_dir = repo_root / ".git" / "hooks"
    hook_path = hook_dir / "pre-push"
    marker = "# git-private2public hook"

    if args.action == "enable":
        hook_dir.mkdir(parents=True, exist_ok=True)
        # Resolve path to this tool + config
        tool = str(Path(__file__).resolve())
        cfg = str(Path(args.config).resolve())
        hook_content = f"""#!/bin/sh
{marker}
# Auto-generated by: {tool}
# Runs `git-private2public publish` before `git push` goes out.
# To disable: `git-private2public hook disable`  (or delete this file)
exec python3 "{tool}" publish -c "{cfg}"
"""
        hook_path.write_text(hook_content)
        hook_path.chmod(0o755)
        print(f"✓ Hook installed: {hook_path}")
        print(f"  Every `git push` will now also publish your clean public mirror.")
        print(f"  Config: {cfg}")
        print(f"  Disable: git-private2public hook disable")
        return 0

    if args.action == "disable":
        if hook_path.exists():
            content = hook_path.read_text()
            if marker in content:
                hook_path.unlink()
                print(f"✓ Hook removed: {hook_path}")
                print(f"  `git push` will no longer auto-publish. Run `git-private2public publish` manually.")
            else:
                print(f"  {hook_path} exists but is not ours — leaving it alone.")
                return 1
        else:
            print(f"  No hook at {hook_path} — nothing to remove.")
        return 0

    if args.action == "status":
        if hook_path.exists() and marker in hook_path.read_text():
            print(f"✓ Hook is ENABLED: {hook_path}")
            # Show the config it points to
            for line in hook_path.read_text().splitlines():
                if "-c" in line:
                    print(f"  {line.strip()}")
        else:
            print(f"✗ Hook is disabled (no hook at {hook_path}).")
            print(f"  Enable: git-private2public hook enable")
        return 0

    return 1


def find_git_root(start: Path) -> Path | None:
    """Walk up from `start` to find the nearest .git directory."""
    p = start.resolve()
    while p != p.parent:
        if (p / ".git").is_dir():
            return p
        p = p.parent
    return None


# Files written by `init` into .gitpublic/
GITPUBLIC_FILES = {
    "config": """# Required: which repos to sync
source = you/private-repo
target = you/public-repo

# Push settings
push_force = true
push_branches = main
""",
    "ignore": """# Files/dirs to NOT publish. Like .gitignore, one per line.
.env
secrets/
*.key
*.pem
""",
    "replace": """# Find ==> replace, one per line. Literal by default.
# Prefix with regex: for regex. glob:*.json: to scope to file type.
# 10.0.0.5 ==> 203.0.113.5
# real-token ==> ***
# regex:[A-Fa-f0-9]{64} ==> ***
""",
    "scan": """# Refuse to push if these appear in the result. One per line.
# regex:github_pat_[A-Za-z0-9_]{30,}
# regex:sk-[A-Za-z0-9]{40,}
# regex:192\\.168\\.
""",
    "allow": """# Domains that are OK to publish (won't trigger scan).
# get.docker.com
# example.com
""",
}


def cmd_init(args) -> int:
    # Folder mode: .gitpublic/ with one file per concern (like .gitignore)
    folder = Path(args.path)
    if folder.is_file() and folder.suffix in (".yaml", ".yml"):
        # Legacy YAML mode
        if folder.exists() and not args.force:
            sys.exit(f"{folder} exists (use --force to overwrite)")
        folder.write_text(EXAMPLE_CONFIG)
        print(f"✓ Wrote example config to {folder}")
        return 0

    # Default: folder mode
    if folder.exists() and not args.force:
        sys.exit(f"{folder} exists (use --force to overwrite)")
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in GITPUBLIC_FILES.items():
        (folder / name).write_text(content)
    print(f"✓ Created {folder}/ with:")
    for name in GITPUBLIC_FILES:
        print(f"    {name}")
    print()
    print(f"  Edit {folder}/config  — set source + target")
    print(f"  Edit {folder}/ignore  — files to hide (like .gitignore)")
    print(f"  Run: git-private2public publish")
    return 0


def cmd_publish(args) -> int:
    config = Config.load(args.config)
    return publish(config, scan_only=args.scan)


def cmd_scan(args) -> int:
    config = Config.load(args.config)
    return publish(config, scan_only=True)


def main() -> int:
    p = argparse.ArgumentParser(
        prog="git-private2public",
        description="Like .gitignore, but for what goes public. Folder-based config.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="write an example config")
    p_init.add_argument("path", nargs="?", default=".gitpublic")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_pub = sub.add_parser("publish", help="sanitize + push to target")
    p_pub.add_argument("-c", "--config", default=".gitpublic")
    p_pub.add_argument("--scan", action="store_true", help="scan only, don't push")
    p_pub.set_defaults(func=cmd_publish)

    p_scan = sub.add_parser("scan", help="scan only (no push)")
    p_scan.add_argument("-c", "--config", default=".gitpublic")
    p_scan.set_defaults(func=cmd_scan)

    p_hook = sub.add_parser("hook", help="enable/disable a local git pre-push hook")
    p_hook_sub = p_hook.add_subparsers(dest="action", required=True)
    p_hook_sub.add_parser("enable", help="install the pre-push hook (auto-publish on every git push)")
    p_hook_sub.add_parser("disable", help="remove the hook")
    p_hook_sub.add_parser("status", help="show whether the hook is on or off")
    p_hook.add_argument("-c", "--config", default=".gitpublic")
    p_hook.set_defaults(func=cmd_hook)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
