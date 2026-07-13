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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Iterable

__version__ = "0.1.8"

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
    "regex:sk-ant-[A-Za-z0-9_-]{20,}",                  # Anthropic
    "regex:ghp_[A-Za-z0-9]{30,}",                       # GitHub PAT (classic)
    "regex:github_pat_[A-Za-z0-9_]{30,}",               # GitHub fine-grained PAT
    "regex:gho_[A-Za-z0-9]{30,}",                       # GitHub OAuth
    "regex:ghs_[A-Za-z0-9]{30,}",                       # GitHub server token
    "regex:ghr_[A-Za-z0-9]{30,}",                       # GitHub refresh token
    "regex:hf_[A-Za-z0-9]{20,}",                        # HuggingFace
    "regex:xox[baprs]-[A-Za-z0-9-]{10,}",               # Slack
    "regex:AKIA[0-9A-Z]{16}",                           # AWS long-term access key ID
    "regex:ASIA[0-9A-Z]{16}",                           # AWS temporary access key ID
    "regex:AIza[0-9A-Za-z_-]{35}",                      # Google API key
    "regex:ya29\\.[0-9A-Za-z_-]{20,}",                  # Google OAuth refresh
    "regex:glpat-[A-Za-z0-9_-]{20,}",                   # GitLab PAT
    "regex:[0-9]{6,12}:[A-Za-z0-9_-]{30,50}",           # Telegram bot token
    "regex:npm_[A-Za-z0-9]{30,}",                       # npm access token
    "regex:pypi-[A-Za-z0-9_-]{40,}",                    # PyPI API token
    "regex:(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}",   # Stripe secret/restricted key
    r"regex:SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}", # SendGrid API key
    r"regex:(?:MTA|MTE|MTI|MTM|MTQ|MTU|MTY|MTc|MTg|MTk)[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{20,}",  # Discord bot token
    "regex:SK[0-9a-fA-F]{32}",                          # Twilio API key
    "regex:key-[0-9a-zA-Z]{32}",                        # Mailgun private API key
    "regex:dop_v1_[A-Za-z0-9]{64}",                    # DigitalOcean personal access token
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
# Redacted findings
# --------------------------------------------------------------------------- #

_PATTERN_LABELS = {
    "sk-ant-": "Anthropic API key",
    "sk-proj-": "OpenAI project key",
    "sk-": "OpenAI API key",
    "github_pat_": "GitHub fine-grained PAT",
    "ghp_": "GitHub classic PAT",
    "AKIA": "AWS access key",
    "ASIA": "AWS temporary access key",
    "npm_": "npm token",
    "pypi-": "PyPI token",
    r"SG\.": "SendGrid API key",
    "dop_v1_": "DigitalOcean token",
    "PRIVATE KEY": "private key",
}


def describe_pattern(raw: str) -> str:
    for needle, label in _PATTERN_LABELS.items():
        if needle in raw:
            return label
    return "credential-like value"


def _safe_token_hint(matched: bytes) -> str:
    """Return a rotation-friendly, non-secret preview such as ghp_…A1b2."""
    text = matched.decode("utf-8", "replace")
    if len(text) <= 8:
        return "…"

    known_prefixes = (
        "github_pat_", "sk-proj-", "sk-ant-", "dop_v1_", "pypi-",
        "glpat-", "npm_", "ghp_", "gho_", "ghs_", "ghr_", "hf_",
        "xoxb-", "xoxa-", "xoxp-", "xoxr-", "xoxs-", "AKIA", "ASIA",
        "sk_live_", "sk_test_", "rk_live_", "rk_test_", "SG.", "key-",
    )
    prefix = next((p for p in known_prefixes if text.startswith(p)), "")
    if not prefix:
        if ":" in text and text.split(":", 1)[0].isdigit():
            prefix = text.split(":", 1)[0][:3] + "…:"
        elif text.startswith("-----BEGIN"):
            prefix = "PRIVATE-KEY-"
        else:
            prefix = text[:3]
    return f"{prefix}…{text[-4:]}"


def redact_match(matched: bytes) -> str:
    digest = hashlib.sha256(matched).hexdigest()[:12]
    return f"[{_safe_token_hint(matched)} len={len(matched)} sha256={digest}]"


def format_violation(location: str, line: int, raw: str, matched: bytes) -> str:
    return f"{location}:{line}: {describe_pattern(raw)} — {redact_match(matched)}"


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
                violations.append(format_violation(fpath, line, raw, matched))
    return violations


# --------------------------------------------------------------------------- #
# Publish flow
# --------------------------------------------------------------------------- #


def _source_gitdir(source: str) -> Path | None:
    src_path = Path(source).expanduser().resolve()
    gitdir = src_path / ".git"
    if gitdir.is_file():
        ref = gitdir.read_text().strip()
        gitdir = Path(ref.split(":", 1)[-1].strip()).expanduser().resolve()
    return gitdir if gitdir.exists() else None


def _remote_ref_sha(url: str, ref: str) -> str:
    res = subprocess.run(
        ["git", "ls-remote", url, ref], capture_output=True, text=True, timeout=30
    )
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()
        raise SystemExit(f"cannot read target ref {ref}: {err}")
    line = res.stdout.strip().splitlines()
    return line[0].split()[0] if line else ""


def _verify_remote_ref(url: str, ref: str, expected: str) -> None:
    actual = _remote_ref_sha(url, ref)
    if actual != expected:
        raise SystemExit(
            f"post-push verification failed for {ref}: expected {expected}, got {actual or '<missing>'}"
        )
    print(f"✓ Verified {ref} at {actual[:12]}", file=sys.stderr)


def _repo_size_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            pass
    return total


def _backup_gitdir(source: str, cwd: str) -> tuple[str, str] | None:
    """Create a timestamped backup of the source gitdir before filter-repo.
    Backups: ~/.cache/git-private2public/backups/<source>/<ts>/
    Keeps max 5 backups per source. Returns path or None.
    """
    cache_dir = Path.home() / ".cache" / "git-private2public" / "backups"
    try:
        src_path = Path(source).expanduser().resolve()
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(src_path))
        backup_root = cache_dir / safe_name
        backup_root.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_path = backup_root / ts
        gitdir = _source_gitdir(source)
        if not gitdir:
            msg = "ℹ No gitdir found at {} -- skipping backup.".format(gitdir)
            print(msg, file=sys.stderr); return None
        msg = "ℹ Backing up gitdir ({}) to {} ...".format(gitdir, backup_path)
        print(msg, file=sys.stderr)
        shutil.copytree(gitdir, backup_path, symlinks=True)
        backups = sorted(b for b in backup_root.iterdir() if b.is_dir())
        for old in backups[:-5]:
            shutil.rmtree(old)
            print("✓ Rotated out old backup " + old.name, file=sys.stderr)
        return str(backup_path), str(gitdir)
    except Exception as ex:
        print("⚠ Backup failed (continuing anyway): " + str(ex), file=sys.stderr)
        return None

def _break_alternates(work_gitdir: Path) -> bool:
    """Break object-store alternates so filter-repo cannot corrupt source.
    git clone --no-local shares objects; filter-repo rewrites in-place.
    """
    alt_file = work_gitdir / "info" / "alternates"
    if not alt_file.exists(): return False
    alt_path = alt_file.read_text().strip()
    if not alt_path: return False
    print("ℹ Breaking object-store alternates (was: " + alt_path + ") ...", file=sys.stderr)
    work_objects = work_gitdir / "objects"
    local_objects = work_gitdir / "objects-local"
    shutil.copytree(work_objects, local_objects, symlinks=False, dirs_exist_ok=True)
    shutil.rmtree(work_objects)
    local_objects.rename(work_objects)
    alt_file.unlink()
    for sub in ["info", "refs"]:
        subp = work_gitdir / sub
        if subp.exists(): shutil.rmtree(subp, ignore_errors=True)
    return True

def publish(config: Config, scan_only: bool = False, cwd: str | None = None, output_format: str = "text") -> int:
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
        target_url = expand_github_shorthand(config.target)

        # Auth from env if provided. Never print the tokenized URL.
        token = os.environ.get("GIT_PRIVATE2PUBLIC_TOKEN")
        if token and "github.com" in target_url and target_url.startswith("https://"):
            target_url = target_url.replace("https://", f"https://x-access-token:{token}@", 1)

        # Capture the exact target state before any expensive rewriting. A later
        # push is allowed only if the branch still has this SHA (or is still absent).
        expected_target_refs = {
            branch: _remote_ref_sha(target_url, f"refs/heads/{branch}")
            for branch in config.push_branches
        }

        # Backup source gitdir before any destructive rewrite
        backup_info = _backup_gitdir(config.source, cwd)
        backup_path = backup_info[0] if backup_info else None
        restore_gitdir = backup_info[1] if backup_info else None
        print(f"▸ Cloning {mask_url(source_url)} ...", file=sys.stderr)
        run(["git", "clone", "--no-local", source_url, str(work)])

        # Break object-store alternates so filter-repo cannot corrupt source
        _break_alternates(work / ".git")

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
                bk = ("\n  Backup available at: " + backup_path) if backup_path else ""
                restore = ("\n  Run: rsync -a " + backup_path + "/. /home/roomhacker/gptadmin/.git/ to restore") if backup_path else ""
                sys.stderr.write(res.stderr)
                sys.exit("git-filter-repo failed (rc=" + str(res.returncode) + ")" + bk + restore)

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
        if output_format == "json":
            print(json.dumps({"ok": True, "scope": "sanitized-tree", "count": 0, "findings": []}, ensure_ascii=False))

        if scan_only:
            print("▸ Scan-only mode — not pushing.", file=sys.stderr)
            return 0

        # Push to target
        print(f"▸ Pushing to {mask_url(target_url)} ...", file=sys.stderr)
        run(["git", "remote", "add", "target", target_url], cwd=str(work))

        local_head = run(["git", "rev-parse", "HEAD"], cwd=str(work)).stdout.strip()
        for branch in config.push_branches:
            push_cmd = ["git", "push"]
            if config.push_force:
                expected = expected_target_refs[branch]
                lease = f"--force-with-lease=refs/heads/{branch}:{expected}"
                push_cmd.append(lease)
            push_cmd.extend(["target", f"HEAD:refs/heads/{branch}"])
            res = subprocess.run(push_cmd, cwd=str(work), capture_output=True, text=True)
            if res.returncode != 0:
                sys.stderr.write(res.stderr)
                sys.exit(
                    f"push of {branch} failed; target probably changed while sanitizing. "
                    "Nothing was overwritten. Run publish again."
                )
            _verify_remote_ref(target_url, f"refs/heads/{branch}", local_head)

        if config.push_tags:
            # Never force tags: signed/release tags may drive CI and must not be
            # silently rewritten. Existing conflicting tags make publish fail.
            res = subprocess.run(
                ["git", "push", "target", "--tags"],
                cwd=str(work), capture_output=True, text=True
            )
            if res.returncode != 0:
                sys.stderr.write(res.stderr)
                sys.exit("tag push failed; existing target tags were left unchanged")

        # For small repositories, independently clone the published target and
        # scan it again. Large repositories get cheap SHA verification above.
        verify_limit = int(os.environ.get("GIT_PRIVATE2PUBLIC_VERIFY_MAX_BYTES", 100 * 1024 * 1024))
        repo_size = _repo_size_bytes(work / ".git")
        if repo_size <= verify_limit:
            verify_repo = tmp_path / "verify-target"
            print(f"▸ Re-cloning small target for independent verification ({repo_size // 1024 // 1024} MiB) ...", file=sys.stderr)
            run(["git", "clone", "--no-local", target_url, str(verify_repo)])
            verify_violations = scan_tree(verify_repo, config)
            if verify_violations:
                raise SystemExit("post-push target scan found violations")
            print("✓ Published target independently re-scanned.", file=sys.stderr)
        else:
            print(
                f"ℹ Target re-clone skipped ({repo_size // 1024 // 1024} MiB > "
                f"{verify_limit // 1024 // 1024} MiB); remote SHA was verified.",
                file=sys.stderr,
            )

        if backup_path:
            msg = "✓ Done. {} updated.  | Backup: {}".format(config.target, backup_path)
            print(msg, file=sys.stderr)
        else:
            msg = "✓ Done. {} updated.".format(config.target)
            print(msg, file=sys.stderr)
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


HOOK_DISPATCHER_MARKER = "# git-private2public managed pre-push dispatcher"


def _hook_paths(repo_root: Path) -> tuple[Path, Path, Path]:
    res = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=str(repo_root), capture_output=True, text=True, check=True)
    hook_dir = Path(res.stdout.strip())
    if not hook_dir.is_absolute():
        hook_dir = (repo_root / hook_dir).resolve()
    return (
        hook_dir / "pre-push",
        hook_dir / "pre-push.git-private2public.json",
        hook_dir / "pre-push.git-private2public-original",
    )


def _load_hook_actions(state_path: Path) -> dict[str, str]:
    if not state_path.exists():
        return {}
    try:
        value = json.loads(state_path.read_text())
        return {str(k): str(v) for k, v in value.items()}
    except Exception:
        return {}


def _write_hook_dispatcher(repo_root: Path, actions: dict[str, str]) -> None:
    hook_path, state_path, original_path = _hook_paths(repo_root)
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    if hook_path.exists() and HOOK_DISPATCHER_MARKER not in hook_path.read_text(errors="replace"):
        if original_path.exists():
            raise SystemExit(f"cannot preserve existing hook: {original_path} already exists")
        hook_path.rename(original_path)

    if not actions:
        state_path.unlink(missing_ok=True)
        if hook_path.exists() and HOOK_DISPATCHER_MARKER in hook_path.read_text(errors="replace"):
            hook_path.unlink()
        if original_path.exists():
            original_path.rename(hook_path)
        return

    state_path.write_text(json.dumps(actions, indent=2) + "\n")
    tool = str(Path(__file__).resolve())
    lines = ["#!/bin/sh", HOOK_DISPATCHER_MARKER, "set -e"]
    if "guard" in actions:
        lines += [
            'if [ "${GIT_PRIVATE2PUBLIC_SKIP_GUARD:-0}" != "1" ]; then',
            f'  python3 "{tool}" guard run',
            "fi",
        ]
    if "publish" in actions:
        lines.append(f'python3 "{tool}" publish -c "{actions["publish"]}"')
    lines += [f'if [ -x "{original_path}" ]; then', f'  exec "{original_path}" "$@"', "fi", "exit 0"]
    hook_path.write_text("\n".join(lines) + "\n")
    hook_path.chmod(0o755)


def _set_hook_action(repo_root: Path, action: str, enabled: bool, config: str = "") -> dict[str, str]:
    _, state_path, _ = _hook_paths(repo_root)
    actions = _load_hook_actions(state_path)
    if enabled:
        actions[action] = config or "enabled"
    else:
        actions.pop(action, None)
    _write_hook_dispatcher(repo_root, actions)
    return actions


def cmd_hook(args) -> int:
    """Enable/disable auto-publish without overwriting existing hooks."""
    repo_root = find_git_root(Path.cwd())
    if not repo_root:
        sys.exit("Not inside a git repo.")
    if args.action == "enable":
        actions = _set_hook_action(repo_root, "publish", True, str(Path(args.config).resolve()))
        print(f"✓ Auto-publish enabled. Active pre-push actions: {', '.join(sorted(actions))}")
        return 0
    if args.action == "disable":
        actions = _set_hook_action(repo_root, "publish", False)
        print(f"✓ Auto-publish disabled. Active pre-push actions: {', '.join(sorted(actions)) or 'none'}")
        return 0
    _, state_path, original_path = _hook_paths(repo_root)
    actions = _load_hook_actions(state_path)
    print(f"{'✓' if 'publish' in actions else '✗'} Auto-publish is {'enabled' if 'publish' in actions else 'disabled'}.")
    if original_path.exists():
        print(f"  Existing pre-push hook is preserved and chained: {original_path}")
    return 0

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


# Cap on blob size for history scan; anything bigger is almost certainly a binary
# artefact (compiled assets, dataset snapshots, etc.) and is unlikely to contain
# a credential. Configurable via env GIT_PRIVATE2PUBLIC_MAX_BLOB_BYTES.
DEFAULT_MAX_BLOB_BYTES = 1_500_000


def _compile_rules(rules: list[str]) -> list[tuple[str, "re.Pattern[bytes]"]]:
    compiled: list[tuple[str, "re.Pattern[bytes]"]] = []
    for raw in rules:
        s = raw.strip()
        if s.startswith("regex:"):
            compiled.append((raw, re.compile(s[len("regex:"):].encode())))
        else:
            compiled.append((raw, re.compile(re.escape(s.encode()))))
    return compiled


def _scan_bytes(data: bytes, fpath: str, compiled, allow_re) -> list[str]:
    """Apply all compiled rules to a single blob; return violations."""
    violations: list[str] = []
    for raw, pat in compiled:
        for m in pat.finditer(data):
            matched = m.group(0)
            if allow_re and allow_re.search(matched):
                continue
            line = data[:m.start()].count(b"\n") + 1
            snippet = matched.decode("utf-8", "replace")[:40]
            violations.append(f"{fpath}:{line}: matches '{raw}' \u2192 {snippet!r}")
    return violations


def scan_history(repo: Path, rules: list[str], allow_domains: list[str]) -> list[str]:
    """Scan every blob reachable from any ref.

    Uses ``git cat-file --batch-all-objects`` (git 2.30+) to enumerate every
    blob in the object database and reads them in a single batch call. This
    catches secrets that already live in old commits but were never removed
    via filter-repo. Each blob is scanned only once even if it appears in
    many commits (the object store already deduplicates).

    Blobs larger than DEFAULT_MAX_BLOB_BYTES are skipped — they're almost
    always binary artefacts where regex scanning would produce noise.
    """
    if not rules:
        return []
    allow_re = re.compile(b"|".join(re.escape(d.encode()) for d in allow_domains)) if allow_domains else None
    compiled = _compile_rules(rules)
    max_blob = int(os.environ.get("GIT_PRIVATE2PUBLIC_MAX_BLOB_BYTES", DEFAULT_MAX_BLOB_BYTES))

    # 1) Enumerate every blob reachable from any object in the repo.
    enum = subprocess.run(
        ["git", "cat-file", "--batch-check", "--batch-all-objects",
         "--format=%(objectname) %(objecttype) %(objectsize)"],
        cwd=str(repo), capture_output=True, text=True,
    )
    if enum.returncode != 0:
        # Older git without --batch-all-objects: fall back to rev-list.
        rev = subprocess.run(
            ["git", "rev-list", "--all", "--objects"],
            cwd=str(repo), capture_output=True, text=True,
        )
        candidates = []
        for line in rev.stdout.splitlines():
            sha = line.split(maxsplit=1)[0]
            candidates.append(sha)
        # Probe each to filter blobs. This is slower but works everywhere.
        blob_shas: list[str] = []
        if candidates:
            probe_input = "\n".join(candidates).encode() + b"\n"
            probe = subprocess.run(
                ["git", "cat-file", "--batch-check"],
                cwd=str(repo), input=probe_input, capture_output=True,
            )
            for line in probe.stdout.decode("utf-8", "replace").splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "blob":
                    try:
                        if int(parts[2]) <= max_blob:
                            blob_shas.append(parts[0])
                    except ValueError:
                        pass
    else:
        blob_shas = []
        for line in enum.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[1] == "blob":
                try:
                    if int(parts[2]) <= max_blob:
                        blob_shas.append(parts[0])
                except ValueError:
                    pass

    if not blob_shas:
        return []

    # 2) Read every blob in one batch call.
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=str(repo), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        stdout, _ = proc.communicate(
            ("\n".join(blob_shas) + "\n").encode(),
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        return []

    # 3) Parse "<sha> <type> <size>\n<content>" records and scan each.
    violations: list[str] = []
    pos = 0
    while pos < len(stdout):
        # Header line ends at first newline.
        nl = stdout.find(b"\n", pos)
        if nl < 0:
            break
        header = stdout[pos:nl].decode("ascii", "replace")
        parts = header.split()
        if len(parts) < 3 or parts[1] != "blob":
            # Skip non-blob or malformed; advance to next line.
            pos = nl + 1
            continue
        try:
            size = int(parts[2])
        except ValueError:
            pos = nl + 1
            continue
        content_start = nl + 1
        content_end = content_start + size
        data = stdout[content_start:content_end]
        # Format: <sha> <type> <size>\n<content> — no trailing newline guaranteed,
        # but git always emits content bytes followed by next record's header.
        for v in _scan_bytes(data, f"<history:{parts[0][:12]}>", compiled, allow_re):
            violations.append(v)
        pos = content_end

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
    history_violations: list[str] = []
    if getattr(args, "history", True):
        history_violations = scan_history(repo, rules, allow)
        violations.extend(history_violations)

    if violations:
        n_tree = len(violations) - len(history_violations)
        if getattr(args, "format", "text") == "json":
            print(json.dumps({"ok": False, "count": len(violations), "working_tree": n_tree, "history": len(history_violations), "findings": violations}, ensure_ascii=False))
        n_hist = len(history_violations)
        print(f"\u2717 guard: refusing push \u2014 {len(violations)} potential secret(s):",
              file=sys.stderr)
        if n_tree and n_hist:
            print(f"  ({n_tree} in working tree, {n_hist} in git history)",
                  file=sys.stderr)
        for v in violations[:30]:
            print(f"  {v}", file=sys.stderr)
        if len(violations) > 30:
            print(f"  ... and {len(violations) - 30} more", file=sys.stderr)

        # Tailored remediation hints.
        print(file=sys.stderr)
        if n_tree:
            print("Working-tree secrets \u2014 fix in the source files:",
                  file=sys.stderr)
            print("  - edit the file, replace the secret with a placeholder",
                  file=sys.stderr)
            print("  - if the file shouldn't be tracked at all, add it to .gitignore",
                  file=sys.stderr)
            print("    and `git rm --cached <path>`", file=sys.stderr)
            print("  - if the secret is live, ROTATE it first, then commit",
                  file=sys.stderr)
        if n_hist:
            print("History secrets \u2014 the commit object is still in .git/objects/.",
                  file=sys.stderr)
            print("  Editing the file is not enough; you need to rewrite history.",
                  file=sys.stderr)
            print("  Easiest path (if you have a .gitpublic/ config set up):",
                  file=sys.stderr)
            print("    git-private2public publish", file=sys.stderr)
            print("  Manual path (works without .gitpublic/):",
                  file=sys.stderr)
            print("    git filter-repo --replace-text replacements.txt --force",
                  file=sys.stderr)
            print("  where replacements.txt has one '<secret>==><replacement>' per line.",
                  file=sys.stderr)
            print("  Note: rewriting history requires force-push and will break",
                  file=sys.stderr)
            print("  any clones \u2014 coordinate with collaborators first.",
                  file=sys.stderr)
        print("Bypass (NOT recommended):  GIT_PRIVATE2PUBLIC_SKIP_GUARD=1 git push",
              file=sys.stderr)
        print("Skip history only (NOT recommended):  --no-history",
              file=sys.stderr)
        return 1
    if getattr(args, "format", "text") == "json":
        print(json.dumps({"ok": True, "count": 0, "working_tree": 0, "history": 0, "findings": []}, ensure_ascii=False))
    return 0


def cmd_guard(args) -> int:
    """Run or manage the local pre-push guard."""
    if args.action == "run":
        return cmd_guard_run(args)
    repo_root = find_git_root(Path.cwd())
    if not repo_root:
        sys.exit("Not inside a git repo.")
    if args.action == "enable":
        actions = _set_hook_action(repo_root, "guard", True)
        print(f"✓ Guard enabled. Active pre-push actions: {', '.join(sorted(actions))}")
        return 0
    if args.action == "disable":
        actions = _set_hook_action(repo_root, "guard", False)
        print(f"✓ Guard disabled. Active pre-push actions: {', '.join(sorted(actions)) or 'none'}")
        return 0
    _, state_path, original_path = _hook_paths(repo_root)
    actions = _load_hook_actions(state_path)
    print(f"{'✓' if 'guard' in actions else '✗'} Guard is {'enabled' if 'guard' in actions else 'disabled'}.")
    if original_path.exists():
        print(f"  Existing pre-push hook is preserved and chained: {original_path}")
    return 0


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


def _doctor_check(label: str, ok: bool, detail: str) -> bool:
    icon = "✓" if ok else "✗"
    print(f"{icon} {label}: {detail}")
    return ok


def _git_remote_access(url: str) -> tuple[bool, str]:
    if not url:
        return False, "not configured"
    expanded = expand_github_shorthand(url)
    res = subprocess.run(
        ["git", "ls-remote", expanded, "HEAD"],
        capture_output=True, text=True, timeout=20,
    )
    if res.returncode == 0:
        return True, mask_url(expanded)
    err = (res.stderr or res.stdout).strip().splitlines()
    return False, (err[-1] if err else "access failed")


def cmd_doctor(args) -> int:
    """Diagnose installation, repository, config, hooks and remote access."""
    cwd = Path.cwd()
    repo = find_git_root(cwd)
    results: list[bool] = []

    git_bin = shutil.which("git")
    results.append(_doctor_check("git", bool(git_bin), git_bin or "not found"))
    filter_bin = shutil.which("git-filter-repo")
    if not filter_bin:
        user_filter = Path.home() / ".local" / "bin" / "git-filter-repo"
        if user_filter.is_file() and os.access(user_filter, os.X_OK):
            filter_bin = str(user_filter)
    if not filter_bin:
        probe = subprocess.run(["git", "filter-repo", "--version"], capture_output=True, text=True)
        filter_bin = "git filter-repo" if probe.returncode == 0 else None
    results.append(_doctor_check("git-filter-repo", bool(filter_bin), filter_bin or "not found; pip install git-filter-repo"))
    results.append(_doctor_check("repository", repo is not None, str(repo) if repo else "not inside a git repository"))

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = cwd / config_path
    config_ok = config_path.exists()
    results.append(_doctor_check("config", config_ok, str(config_path)))

    cfg = None
    if config_ok:
        try:
            cfg = Config.load(config_path)
            validate_config(cfg, cwd=str(repo or cwd))
            results.append(_doctor_check("config syntax", True, "valid"))
        except BaseException as exc:
            results.append(_doctor_check("config syntax", False, str(exc)))

    if repo:
        tracked = check_local_gitpublic_secrets_not_tracked(str(repo))
        results.append(_doctor_check("secret rule files", not tracked, "untracked" if not tracked else ", ".join(tracked)))
        hook = repo / ".git" / "hooks" / "pre-push"
        if hook.exists():
            text = hook.read_text(errors="replace")
            kind = "guard" if GUARD_HOOK_MARKER in text else ("publish" if "git-private2public hook" in text else "foreign")
            results.append(_doctor_check("pre-push hook", kind != "foreign", f"enabled ({kind})"))
        else:
            results.append(_doctor_check("pre-push hook", False, "disabled; run `git-private2public guard enable`"))

    if cfg:
        ok, detail = _git_remote_access(cfg.source)
        results.append(_doctor_check("source access", ok, detail))
        ok, detail = _git_remote_access(cfg.target)
        results.append(_doctor_check("target access", ok, detail))

    failed = len([x for x in results if not x])
    print(f"\nDoctor: {len(results)-failed}/{len(results)} checks passed.")
    return 1 if failed else 0


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
    return publish(config, scan_only=args.scan, cwd=os.getcwd(), output_format=args.format)


def cmd_scan(args) -> int:
    config = Config.load(args.config)
    return publish(config, scan_only=True, cwd=os.getcwd(), output_format=args.format)


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

    p_doctor = sub.add_parser("doctor", help="diagnose tools, config, hooks and remote access")
    p_doctor.add_argument("-c", "--config", default=".gitpublic")
    p_doctor.set_defaults(func=cmd_doctor)

    p_pub = sub.add_parser("publish", help="sanitize + push to target")
    p_pub.add_argument("-c", "--config", default=".gitpublic")
    p_pub.add_argument("--scan", action="store_true", help="scan only, don't push")
    p_pub.add_argument("--format", choices=("text", "json"), default="text", help="report format")
    p_pub.set_defaults(func=cmd_publish)

    p_scan = sub.add_parser("scan", help="scan only (no push)")
    p_scan.add_argument("-c", "--config", default=".gitpublic")
    p_scan.add_argument("--format", choices=("text", "json"), default="text", help="report format")
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
    p_guard_run_p = p_guard_sub.add_parser(
        "run",
        help="scan the local repo against default + custom rules (used by the hook itself; "
             "also useful manually: `git-private2public guard run`)",
    )
    p_guard_run_p.add_argument(
        "--history", dest="history", action="store_true", default=True,
        help="scan every blob in git history, not just the working tree (default: on)",
    )
    p_guard_run_p.add_argument(
        "--no-history", dest="history", action="store_false",
        help="scan only tracked files in the working tree, skip git history",
    )
    p_guard.set_defaults(func=cmd_guard)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
