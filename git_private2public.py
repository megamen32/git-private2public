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

__version__ = "0.1.4"

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pip install pyyaml")


# --------------------------------------------------------------------------- #
# Default secret patterns
# --------------------------------------------------------------------------- #
# Always-on safety net applied by `guard` (and optionally by `publish`).
# Catches the common offenders people accidentally commit: API keys for the
# usual providers. Add custom patterns in .gitpublic/scan — they layer on top.

DEFAULT_SECRET_PATTERNS: list[str] = [
    "regex:sk-[A-Za-z0-9]{20,}",                        # OpenAI legacy
    "regex:sk-proj-[A-Za-z0-9_-]{40,}",                 # OpenAI project
    "regex:ghp_[A-Za-z0-9]{30,}",                       # GitHub PAT (classic)
    "regex:github_pat_[A-Za-z0-9_]{30,}",               # GitHub fine-grained PAT
    "regex:gho_[A-Za-z0-9]{30,}",                       # GitHub OAuth
    "regex:ghs_[A-Za-z0-9]{30,}",                       # GitHub server token
    "regex:ghr_[A-Za-z0-9]{30,}",                       # GitHub refresh token
    "regex:hf_[A-Za-z0-9]{20,}",                        # HuggingFace
    "regex:xox[baprs]-[A-Za-z0-9-]{10,}",               # Slack
    "regex:AKIA[0-9A-Z]{16}",                           # AWS access key ID
    "regex:AIza[0-9A-Za-z_-]{35}",                      # Google API key
    "regex:ya29\\.[0-9A-Za-z_-]{20,}",                  # Google OAuth refresh
    "regex:glpat-[A-Za-z0-9_-]{20,}",                   # GitLab PAT
    "regex:eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+",  # JWT (catch-all)
    "regex:-----BEGIN [A-Z ]*PRIVATE KEY-----",         # PEM private key header
]


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
            allow_domains=list(data.get("allow_domains") or data.get("allow") or data.get("domains") or []),
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
            allow_domains=read_lines("allow") + read_lines("domains"),
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
            file_glob = rest[:colon].strip()
            s = rest[colon + 1:]

        sep = "==>"
        idx = s.find(sep)
        if idx == -1:
            raise ValueError(f"replace rule missing '==>': {raw}")
        return cls(
            pattern=s[:idx].strip(),
            replacement=s[idx + len(sep):].strip(),
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


def expand_github_shorthand(value: str) -> str:
    """Expand owner/repo to a GitHub HTTPS clone/push URL.

    Full URLs and local paths are returned unchanged. This keeps `.gitpublic/config`
    short while still allowing SSH URLs or local test repositories.
    """
    value = value.strip()
    if not value:
        return value
    if value.startswith(("http://", "https://", "git@", "ssh://", "file://")):
        return value
    if value.startswith((".", "/", "~")):
        return value
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        return f"https://github.com/{value}.git"
    return value


def mask_url(url: str) -> str:
    return re.sub(r"https://[^/@]+@", "https://***@", url)


def validate_config(config: Config, cwd: str | None = None) -> None:
    """Validate config and apply single-repo fallback.

    Single-repo mode kicks in when source and target are both empty: in
    that case we treat the current repo's origin as both — sanitize it in
    place and force-push back. Useful for small projects where maintaining
    two repos (private + public) is overkill.
    """
    if not config.source and not config.target:
        cwd = cwd or os.getcwd()
        res = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True, text=True,
        )
        if res.returncode != 0 or not res.stdout.strip():
            raise SystemExit(
                "single-repo mode: no source/target configured and no "
                "git 'origin' remote in current directory"
            )
        origin = res.stdout.strip()
        config.source = origin
        config.target = origin
        print(f"▸ single-repo mode: source = target = {mask_url(origin)}", file=sys.stderr)
    elif not config.target:
        config.target = config.source
    elif not config.source:
        config.source = config.target

    missing = []
    if not config.source:
        missing.append("source")
    if not config.target:
        missing.append("target")
    if missing:
        raise SystemExit(f"missing required config value(s): {', '.join(missing)}")


def clone_source(source: str, dest: Path) -> None:
    """Clone the source repo (full history, all branches)."""
    run(["git", "clone", "--mirror", source, str(dest)])
    # Re-init as a normal (non-bare-mirror) working clone so filter-repo is happy.
    # filter-repo works on bare clones too; keep it simple.


# Files inside .gitpublic/ that may carry literal secrets in their content
# (replace patterns or scan rules) and therefore must never be committed.
GITPUBLIC_SECRET_FILES = ("replace", "scan")


def check_local_gitpublic_secrets_not_tracked(cwd: str) -> list[str]:
    """Refuse to publish if .gitpublic/{replace,scan} is tracked in the local repo.

    Only ``replace`` and ``scan`` may contain literal secrets (e.g.
    ``whisper.bezrabotnyi.com==>example.com`` or ``XYZ123`` as a scan
    pattern). The other files in .gitpublic/ — ``config``, ``ignore``,
    ``allow`` — are safe to commit.

    Returns the list of tracked secret-bearing files. Empty list = OK.
    """
    res = subprocess.run(
        ["git", "-C", cwd, "ls-files", "-c", "--", ".gitpublic/"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        return []
    tracked = {f for f in res.stdout.splitlines() if f}
    secret_files = {f".gitpublic/{name}" for name in GITPUBLIC_SECRET_FILES}
    return sorted(tracked & secret_files)


def load_scan_rules(cwd: str, include_defaults: bool = False) -> list[str]:
    """Return scan rules: defaults + custom from .gitpublic/scan.

    With include_defaults=True (used by `guard`), the canonical set of
    provider API-key patterns is prepended to whatever the user added in
    .gitpublic/scan. With False (legacy publish behaviour), only the
    user's rules are returned.
    """
    custom: list[str] = []
    scan_file = Path(cwd) / ".gitpublic" / "scan"
    if scan_file.exists():
        for line in scan_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                custom.append(line)
    if include_defaults:
        # Defaults first so the user can override duplicate patterns by
        # placing their own copy later in .gitpublic/scan.
        return list(DEFAULT_SECRET_PATTERNS) + custom
    return custom


def make_filter_repo_args(deletes: list[DeleteRule], replaces_path: Path) -> list[str]:
    args: list[str] = []
    for d in deletes:
        if d.is_glob:
            args.extend(["--path-glob", d.pattern])
        else:
            # Keep trailing slash for directories; git-filter-repo treats it as a path prefix.
            args.extend(["--path", d.pattern])
    if deletes:
        args.append("--invert-paths")
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
            prefix = "literal:"
        lines.append(f"{prefix}{r.pattern}==>{r.replacement}")
    path.write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #

def scan_tree(repo: Path, config: Config) -> list[str]:
    """Return list of violations (pattern + file:line) in the current tree."""
    violations: list[str] = []
    allow_re = re.compile(b"|".join(re.escape(d.encode()) for d in config.allow_domains)) if config.allow_domains else None

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
                # Domain allowlist applies to the matched text itself.
                # Using surrounding context would accidentally allow `private.local`
                # just because `github.com` appears nearby on the same line/paragraph.
                matched = m.group(0)
                if allow_re and allow_re.search(matched):
                    continue
                # Find line number
                line = data[:m.start()].count(b"\n") + 1
                snippet = data[m.start():m.end() + 20].decode("utf-8", "replace")[:60]
                violations.append(f"{fpath}:{line}: matches '{raw}' → ...{snippet}...")
    return violations


# --------------------------------------------------------------------------- #
# Publish flow
# --------------------------------------------------------------------------- #

def publish(config: Config, scan_only: bool = False, cwd: str | None = None) -> int:
    cwd = cwd or os.getcwd()

    # Only .gitpublic/{replace,scan} may carry literal secrets; the rest
    # (config / ignore / allow) is safe to commit. Refuse early if those
    # two files accidentally landed in the local repo's index.
    tracked_secrets = check_local_gitpublic_secrets_not_tracked(cwd)
    if tracked_secrets:
        sys.stderr.write(
            "\u2717 secret-bearing .gitpublic/ files are tracked in your local repo "
            "\u2014 refusing to publish.\n"
            "  These files may hold literal secrets inside replace/scan rules;\n"
            "  pushing them would leak them to the public repo.\n"
        )
        for f in tracked_secrets:
            sys.stderr.write(f"  {f}\n")
        sys.stderr.write(
            "\n  Fix (keep config/ignore/allow public, hide only replace/scan):\n"
            "    echo '.gitpublic/replace' >> .gitignore\n"
            "    echo '.gitpublic/scan'    >> .gitignore\n"
            "    git rm --cached .gitpublic/replace .gitpublic/scan\n"
            "    git commit -m 'untrack .gitpublic/{replace,scan}'\n"
        )
        return 1

    validate_config(config, cwd=cwd)
    deletes = [DeleteRule.parse(d) for d in config.delete]
    replaces = [ReplaceRule.parse(r) for r in config.replace]

    with tempfile.TemporaryDirectory(prefix="git-private2public-") as tmp:
        tmp_path = Path(tmp)
        work = tmp_path / "work"

        source_url = expand_github_shorthand(config.source)
        print(f"▸ Cloning {mask_url(source_url)} ...", file=sys.stderr)
        run(["git", "clone", "--no-local", source_url, str(work)])

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
        target_url = expand_github_shorthand(config.target)

        # Auth from env if provided. Never print the tokenized URL.
        token = os.environ.get("GIT_PRIVATE2PUBLIC_TOKEN")
        if token and "github.com" in target_url and target_url.startswith("https://"):
            target_url = target_url.replace("https://", f"https://x-access-token:{token}@", 1)

        print(f"▸ Pushing to {mask_url(target_url)} ...", file=sys.stderr)
        run(["git", "remote", "add", "target", target_url], cwd=str(work))

        for branch in config.push_branches:
            push_cmd = ["git", "push"]
            if config.push_force:
                push_cmd.append("--force-with-lease")
            push_cmd.extend(["target", f"HEAD:{branch}"])
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
#   - "<PRIVATE_IP>==>203.0.113.5"
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
        if hook_path.exists() and marker not in hook_path.read_text():
            sys.exit(f"{hook_path} exists and is not managed by git-private2public; refusing to overwrite")
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


# --------------------------------------------------------------------------- #
# Guard — pre-push safety net
# --------------------------------------------------------------------------- #
# guard installs a lightweight pre-push hook that blocks `git push` if
# scanned content matches DEFAULT_SECRET_PATTERNS or .gitpublic/scan rules.
# Unlike `hook` (which also rewrites history and pushes to a public repo),
# guard is purely a refusal mechanism — no clone, no filter-repo, no push.

GUARD_HOOK_MARKER = "# git-private2public guard hook"


def scan_local_repo(repo: Path, rules: list[str], allow_domains: list[str]) -> list[str]:
    """Scan tracked files in `repo` against `rules`. Returns violations."""
    allow_re = re.compile(b"|".join(re.escape(d.encode()) for d in allow_domains)) if allow_domains else None
    compiled: list[tuple[str, "re.Pattern[bytes]"]] = []
    for raw in rules:
        s = raw.strip()
        if s.startswith("regex:"):
            compiled.append((raw, re.compile(s[len("regex:"):].encode())))
        else:
            compiled.append((raw, re.compile(re.escape(s.encode()))))

    res = subprocess.run(["git", "ls-files"], cwd=str(repo), capture_output=True, text=True)
    files = [f for f in res.stdout.strip().split("\n") if f]

    violations: list[str] = []
    for fpath in files:
        full = repo / fpath
        if not full.is_file():
            continue
        try:
            data = full.read_bytes()
        except Exception:
            continue
        # Heuristic: skip very large binary-ish files
        if len(data) > 1_500_000:
            continue
        for raw, pat in compiled:
            for m in pat.finditer(data):
                matched = m.group(0)
                if allow_re and allow_re.search(matched):
                    continue
                line = data[:m.start()].count(b"\n") + 1
                snippet = matched.decode("utf-8", "replace")[:40]
                violations.append(f"{fpath}:{line}: matches '{raw}' \u2192 {snippet!r}")
    return violations


def cmd_guard_run(args) -> int:
    """Scan the local repo. Used by the guard pre-push hook."""
    cwd = os.getcwd()
    repo = find_git_root(Path(cwd))
    if not repo:
        sys.exit("Not inside a git repo.")
    rules = load_scan_rules(str(repo), include_defaults=True)
    if not rules:
        print("no scan rules — skipping", file=sys.stderr)
        return 0
    # Allowlist: anything in .gitpublic/allow — these domains are OK
    allow: list[str] = []
    allow_file = repo / ".gitpublic" / "allow"
    if allow_file.exists():
        allow = [line.strip() for line in allow_file.read_text().splitlines()
                 if line.strip() and not line.strip().startswith("#")]

    violations = scan_local_repo(repo, rules, allow)
    if violations:
        print(f"\u2717 guard: refusing push \u2014 {len(violations)} potential secret(s):",
              file=sys.stderr)
        for v in violations[:30]:
            print(f"  {v}", file=sys.stderr)
        if len(violations) > 30:
            print(f"  ... and {len(violations) - 30} more", file=sys.stderr)
        print("\nFix: remove the secret, rotate it if it's live, then re-push.",
              file=sys.stderr)
        print("Bypass (NOT recommended):  GIT_PRIVATE2PUBLIC_SKIP_GUARD=1 git push",
              file=sys.stderr)
        return 1
    return 0


def cmd_guard(args) -> int:
    """Install / remove / show the local git pre-push guard hook."""
    if args.action == "run":
        return cmd_guard_run(args)

    repo_root = find_git_root(Path.cwd())
    if not repo_root:
        sys.exit("Not inside a git repo.")

    hook_dir = repo_root / ".git" / "hooks"
    hook_path = hook_dir / "pre-push"

    if args.action == "enable":
        # If a regular `hook` (publish) is already installed, leave it alone —
        # the user asked for guard-only. Both would race on the same hook file.
        if hook_path.exists() and "git-private2public hook" in hook_path.read_text() \
                and GUARD_HOOK_MARKER not in hook_path.read_text():
            sys.exit(
                f"{hook_path} is already a git-private2public 'hook' (publish). "
                "Disable it first with `git-private2public hook disable` if you "
                "want to switch to guard-only. Or run guard enable AFTER hook "
                "disable if you want both behaviours — but they share the hook "
                "file so you'll need to combine them manually."
            )
        hook_dir.mkdir(parents=True, exist_ok=True)
        if hook_path.exists() and GUARD_HOOK_MARKER not in hook_path.read_text() \
                and "git-private2public" not in hook_path.read_text():
            sys.exit(f"{hook_path} exists and is not managed by git-private2public; refusing to overwrite")
        tool = str(Path(__file__).resolve())
        hook_content = f"""#!/bin/sh
{GUARD_HOOK_MARKER}
# Auto-generated by: {tool}
# Runs `git-private2public guard run` before `git push` goes out.
# Blocks the push if scanned content matches DEFAULT_SECRET_PATTERNS
# (sk-, ghp_, hf_, AWS, etc.) or .gitpublic/scan rules.
# To disable: `git-private2public guard disable`  (or delete this file)
if [ "$GIT_PRIVATE2PUBLIC_SKIP_GUARD" = "1" ]; then
    exit 0
fi
exec python3 "{tool}" guard run
"""
        hook_path.write_text(hook_content)
        hook_path.chmod(0o755)
        print(f"\u2713 Guard installed: {hook_path}")
        print(f"  Every `git push` will now be scanned against the default")
        print(f"  secret patterns plus your .gitpublic/scan rules.")
        print(f"  Disable: git-private2public guard disable")
        print(f"  Bypass once: GIT_PRIVATE2PUBLIC_SKIP_GUARD=1 git push")
        return 0

    if args.action == "disable":
        if hook_path.exists():
            content = hook_path.read_text()
            if GUARD_HOOK_MARKER in content:
                hook_path.unlink()
                print(f"\u2713 Guard removed: {hook_path}")
                print(f"  `git push` will no longer be auto-scanned.")
            else:
                print(f"  {hook_path} exists but is not ours — leaving it alone.")
                return 1
        else:
            print(f"  No hook at {hook_path} — nothing to remove.")
        return 0

    if args.action == "status":
        if hook_path.exists() and GUARD_HOOK_MARKER in hook_path.read_text():
            print(f"\u2713 Guard is ENABLED: {hook_path}")
            print(f"  Default patterns: {len(DEFAULT_SECRET_PATTERNS)}")
        else:
            print(f"\u2717 Guard is disabled (no guard hook at {hook_path}).")
            print(f"  Enable: git-private2public guard enable")
        return 0

    return 1


# Files written by `init` into .gitpublic/
GITPUBLIC_FILES = {
    "config": """# Required: which repos to sync
# owner/repo shorthand, full Git URL, or local path
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
# <PRIVATE_IP> ==> 203.0.113.5
# real-token ==> ***
# regex:[A-Fa-f0-9]{64} ==> ***
""",
    "scan": """# Refuse to push if these appear in the result. One per line.
# regex:github_pat_[A-Za-z0-9_]{30,}
# regex:sk-[A-Za-z0-9]{40,}
# regex:192\\.168\\.
""",
    "allow": """# Domains that are OK to publish when the matched text is that domain.
# Example: scan has regex:[a-z0-9.-]+\\.[a-z]{2,}; allow keeps public domains from failing.
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
    return publish(config, scan_only=args.scan, cwd=os.getcwd())


def cmd_scan(args) -> int:
    config = Config.load(args.config)
    return publish(config, scan_only=True, cwd=os.getcwd())


def main() -> int:
    p = argparse.ArgumentParser(
        prog="git-private2public",
        description="Like .gitignore, but for what goes public. Folder-based config.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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

    p_guard = sub.add_parser(
        "guard", help="pre-push safety net: scan for secrets, refuse push if found"
    )
    p_guard_sub = p_guard.add_subparsers(dest="action", required=True)
    p_guard_sub.add_parser("enable", help="install the guard pre-push hook")
    p_guard_sub.add_parser("disable", help="remove the guard hook")
    p_guard_sub.add_parser("status", help="show whether the guard is on or off")
    p_guard_sub.add_parser(
        "run",
        help="scan the local repo against default + custom rules (used by the hook itself; "
             "also useful manually: `git-private2public guard run`)",
    )
    p_guard.set_defaults(func=cmd_guard)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
