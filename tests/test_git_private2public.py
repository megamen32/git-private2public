from pathlib import Path
import subprocess
import json
import os

import pytest

import git_private2public as g


def test_folder_config_loads_allow_and_domains_alias(tmp_path: Path):
    cfgdir = tmp_path / ".gitpublic"
    cfgdir.mkdir()
    (cfgdir / "config").write_text(
        "source = owner/private\ntarget = owner/public\npush_force = false\n"
        "push_branches = main,dev\npreserve_target_tags = true\n"
    )
    (cfgdir / "ignore").write_text("# comment\n.env\n*.key\n")
    (cfgdir / "allow").write_text("github.com\n")
    (cfgdir / "domains").write_text("example.com\n")

    cfg = g.Config.load(cfgdir)

    assert cfg.source == "owner/private"
    assert cfg.target == "owner/public"
    assert cfg.delete == [".env", "*.key"]
    assert cfg.allow_domains == ["github.com", "example.com"]
    assert cfg.push_force is False
    assert cfg.push_branches == ["main", "dev"]
    assert cfg.preserve_target_tags is True


def test_folder_config_loads_snapshot_mode(tmp_path: Path):
    cfgdir = tmp_path / ".gitpublic"
    cfgdir.mkdir()
    (cfgdir / "config").write_text(
        "source = owner/private\ntarget = owner/public\n"
        "mode = snapshot\nsnapshot_include_source_sha = true\n"
    )

    cfg = g.Config.load(cfgdir)

    assert cfg.mode == "snapshot"
    assert cfg.snapshot_include_source_sha is True


def test_folder_config_loads_scan_ignore(tmp_path: Path):
    cfgdir = tmp_path / ".gitpublic"
    cfgdir.mkdir()
    (cfgdir / "config").write_text("source = owner/private\ntarget = owner/public\n")
    (cfgdir / "scan_ignore").write_text("# fixture globs\ntests/fixtures/**\n*.snapshot\n")

    assert g.Config.load(cfgdir).scan_ignore == ["tests/fixtures/**", "*.snapshot"]


def test_yaml_config_accepts_allow_alias(tmp_path: Path):
    path = tmp_path / "rules.yaml"
    path.write_text(
        "source: owner/private\n"
        "target: owner/public\n"
        "ignore: ['.env']\n"
        "allow: ['github.com']\n"
    )

    cfg = g.Config.load(path)

    assert cfg.delete == [".env"]
    assert cfg.allow_domains == ["github.com"]


def test_yaml_config_loads_scan_ignore(tmp_path: Path):
    path = tmp_path / "rules.yaml"
    path.write_text(
        "source: owner/private\n"
        "target: owner/public\n"
        "scan_ignore: ['tests/fixtures/**']\n"
    )

    assert g.Config.load(path).scan_ignore == ["tests/fixtures/**"]


def test_yaml_config_loads_preserve_target_tags(tmp_path: Path):
    path = tmp_path / "rules.yaml"
    path.write_text(
        "source: owner/private\n"
        "target: owner/public\n"
        "mode: snapshot\n"
        "push:\n"
        "  preserve_target_tags: true\n"
    )

    assert g.Config.load(path).preserve_target_tags is True


@pytest.mark.parametrize(
    ("option", "expected_error"),
    [
        ('push:\n  preserve_target_tags: "false"\n', "push.preserve_target_tags"),
        ('push:\n  force: "false"\n', "push.force"),
        ('push:\n  tags: "false"\n', "push.tags"),
        ('snapshot_include_source_sha: "false"\n', "snapshot_include_source_sha"),
    ],
)
def test_yaml_config_rejects_quoted_boolean_options(tmp_path: Path, option: str, expected_error: str):
    path = tmp_path / "rules.yaml"
    path.write_text(
        "source: owner/private\n"
        "target: owner/public\n"
        + option
    )

    with pytest.raises(SystemExit, match=f"{expected_error} must be a YAML boolean"):
        g.Config.load(path)


def test_yaml_config_treats_scalar_branch_as_one_branch_name(tmp_path: Path):
    path = tmp_path / "rules.yaml"
    path.write_text(
        "source: owner/private\n"
        "target: owner/public\n"
        "push:\n"
        "  branches: main\n"
    )

    assert g.Config.load(path).push_branches == ["main"]


def test_delete_rules_map_to_filter_repo_args(tmp_path: Path):
    replace_file = tmp_path / "replace.txt"
    replace_file.write_text("literal==>***\n")
    rules = [g.DeleteRule.parse("secrets/"), g.DeleteRule.parse("*.key"), g.DeleteRule.parse(".env")]

    args = g.make_filter_repo_args(rules, replace_file)

    assert args[args.index("--path") + 1] == "secrets/"
    assert "--path-glob" in args
    assert "*.key" in args
    assert "--invert-paths" in args
    assert "--replace-text" in args


def test_allow_domains_are_bytes_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("download from github.com now private.example.local leaks\n")

    def fake_run(cmd, cwd=None, check=True):
        class Result:
            stdout = "README.md\n"
        return Result()

    monkeypatch.setattr(g, "run", fake_run)
    cfg = g.Config(
        source=".",
        target="owner/public",
        fail_on_match=[r"regex:[a-z0-9.-]+\.[a-z]{2,}"],
        allow_domains=["github.com"],
    )

    violations = g.scan_tree(repo, cfg)

    assert len(violations) == 1
    assert "private.example.local" not in violations[0]
    assert "len=" in violations[0]
    assert "sha256=" in violations[0]


def test_github_shorthand_expansion_and_masking():
    assert g.expand_github_shorthand("owner/repo") == "https://github.com/owner/repo.git"
    assert g.expand_github_shorthand("git@github.com:owner/repo.git") == "git@github.com:owner/repo.git"
    assert g.mask_url("https://x-access-token:secret@github.com/owner/repo.git") == "https://***@github.com/owner/repo.git"


def test_replace_file_uses_literal_prefix(tmp_path: Path):
    path = tmp_path / "replace.txt"
    g.write_replace_file(path, [g.ReplaceRule.parse("private.company.local ==> example.com")])
    assert path.read_text() == "literal:private.company.local==>example.com\n"


def test_replace_rule_strips_separator_whitespace():
    rule = g.ReplaceRule.parse("regex: private\\.local  ==>  example.com  ")
    assert rule.is_regex is True
    assert rule.pattern == "private\\.local"
    assert rule.replacement == "example.com"


def test_version_constant_matches_package_version():
    assert g.__version__ == "0.2.4"
    assert 'version = "0.2.4"' in (Path(__file__).parent.parent / "pyproject.toml").read_text()


def test_gitpublic_secret_check_finds_only_replace_and_scan(tmp_path: Path):
    """Only .gitpublic/replace and .gitpublic/scan are flagged.

    .gitpublic/config / .gitpublic/ignore / .gitpublic/allow are safe
    to commit and must NOT show up in the result.
    """
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    gp = repo / ".gitpublic"
    gp.mkdir()
    (gp / "config").write_text("source = a\ntarget = b\n")
    (gp / "ignore").write_text(".env\n")
    (gp / "allow").write_text("github.com\n")
    (gp / "replace").write_text("whisper.bezrabotnyi.com==>example.com\n")
    (gp / "scan").write_text("XYZ123\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitpublic/"])

    flagged = g.check_local_gitpublic_secrets_not_tracked(str(repo))
    assert flagged == [".gitpublic/replace", ".gitpublic/scan"]


def test_gitpublic_secret_check_clean_when_only_safe_files_tracked(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    gp = repo / ".gitpublic"
    gp.mkdir()
    (gp / "config").write_text("source = a\ntarget = b\n")
    (gp / "ignore").write_text(".env\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitpublic/"])

    assert g.check_local_gitpublic_secrets_not_tracked(str(repo)) == []


def test_gitpublic_secret_check_allows_tracked_generic_regex_scan(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    gp = repo / ".gitpublic"
    gp.mkdir()
    (gp / "scan").write_text(
        "# Generic provider signatures are safe policy\n"
        "regex:ghp_[A-Za-z0-9]{30,}\n"
        "regex:AKIA[0-9A-Z]{16}\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", ".gitpublic/scan"], check=True)

    assert g.check_local_gitpublic_secrets_not_tracked(str(repo)) == []


def test_gitpublic_secret_check_rejects_tracked_scan_with_literal_rule(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    gp = repo / ".gitpublic"
    gp.mkdir()
    (gp / "scan").write_text("regex:generic-[0-9]+\nactual-private-value\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitpublic/scan"], check=True)

    assert g.check_local_gitpublic_secrets_not_tracked(str(repo)) == [".gitpublic/scan"]


def test_gitpublic_secret_check_rejects_regex_prefix_without_generic_expression(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    gp = repo / ".gitpublic"
    gp.mkdir()
    (gp / "scan").write_text("regex:actual-private-value\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitpublic/scan"], check=True)

    assert g.check_local_gitpublic_secrets_not_tracked(str(repo)) == [".gitpublic/scan"]


# ---- guard / default-pattern tests ----------------------------------------- #

def test_load_scan_rules_includes_defaults_when_requested(tmp_path: Path):
    """load_scan_rules(include_defaults=True) returns DEFAULT_SECRET_PATTERNS
    first, then any user-defined rules from .gitpublic/scan."""
    scan = tmp_path / ".gitpublic" / "scan"
    scan.parent.mkdir()
    scan.write_text("# comment\nregex:my-private-token-\\d+\n")
    rules = g.load_scan_rules(str(tmp_path), include_defaults=True)
    assert g.DEFAULT_SECRET_PATTERNS[0] in rules
    assert "regex:my-private-token-\\d+" in rules


def test_load_scan_rules_without_defaults_only_returns_custom(tmp_path: Path):
    """load_scan_rules(include_defaults=False) returns only user rules.
    publish() uses this; guard() uses include_defaults=True."""
    scan = tmp_path / ".gitpublic" / "scan"
    scan.parent.mkdir()
    scan.write_text("regex:my-private-token-\\d+\n")
    rules = g.load_scan_rules(str(tmp_path), include_defaults=False)
    assert "regex:my-private-token-\\d+" in rules
    # None of the defaults should leak through
    for default in g.DEFAULT_SECRET_PATTERNS:
        assert default not in rules


def test_load_scan_rules_missing_file_returns_only_defaults(tmp_path: Path):
    rules = g.load_scan_rules(str(tmp_path), include_defaults=True)
    assert rules == g.DEFAULT_SECRET_PATTERNS


def test_scan_local_repo_blocks_openai_key(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    (repo / "src.py").write_text('token = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"\n')
    subprocess.run(["git", "-C", str(repo), "add", "src.py"])
    violations = g.scan_local_repo(
        repo, g.DEFAULT_SECRET_PATTERNS, allow_domains=[]
    )
    assert any("OpenAI project" in v or "sk-proj" in v for v in violations)


def test_scan_local_repo_blocks_github_pat(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    (repo / "token.txt").write_text("ghp_abcdefghijklmnopqrstuvwxyz0123456789\n")
    subprocess.run(["git", "-C", str(repo), "add", "token.txt"])
    violations = g.scan_local_repo(repo, g.DEFAULT_SECRET_PATTERNS, allow_domains=[])
    assert any("GitHub" in v for v in violations)


def test_scan_local_repo_blocks_aws_access_key(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    (repo / "creds.env").write_text("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n")
    subprocess.run(["git", "-C", str(repo), "add", "creds.env"])
    violations = g.scan_local_repo(repo, g.DEFAULT_SECRET_PATTERNS, allow_domains=[])
    assert any("AWS" in v or "AKIA" in v for v in violations)


def test_scan_local_repo_clean_for_normal_source(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    (repo / "main.go").write_text('package main\nfunc main() { println("hello world") }\n')
    (repo / "config.yaml").write_text("server:\n  port: 8080\n  name: app\n")
    subprocess.run(["git", "-C", str(repo), "add", "."])
    assert g.scan_local_repo(repo, g.DEFAULT_SECRET_PATTERNS, allow_domains=[]) == []


def test_scan_local_repo_respects_allow_domains(tmp_path: Path):
    """If the matched text is in allow_domains, skip the violation."""
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    # AWS-shaped key in a config that also happens to mention an allowed domain.
    # The match itself contains the secret, so allow_domains doesn't excuse it.
    # But for a token whose value is the allowed domain — that should pass.
    (repo / "ok.py").write_text('api_host = "api.example.com"\n')
    (repo / "bad.py").write_text('api_key = "AKIAIOSFODNN7EXAMPLE"\n')
    subprocess.run(["git", "-C", str(repo), "add", "."])
    violations = g.scan_local_repo(
        repo,
        g.DEFAULT_SECRET_PATTERNS,
        allow_domains=["example.com"],
    )
    assert any("bad.py" in v for v in violations)
    assert not any("ok.py" in v for v in violations)


def test_guard_hook_installs_and_runs(tmp_path: Path, monkeypatch):
    """End-to-end: guard enable writes the hook, guard run on a repo with a
    secret should fail, on a clean repo should pass."""
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "[email protected]"])
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"])
    (repo / "config.py").write_text("secret = 'ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345'\n")
    subprocess.run(["git", "-C", str(repo), "add", "config.py"])
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"])

    # Pretend we're inside the repo. Use --no-history here because this test
    # focuses on the working-tree path; history scan is covered separately
    # in test_scan_history_finds_old_blob.
    monkeypatch.chdir(repo)
    rc = g.cmd_guard_run(argparse.Namespace(history=False))
    assert rc == 1

    # Now remove the secret and re-run
    (repo / "config.py").write_text("debug = True\n")
    subprocess.run(["git", "-C", str(repo), "add", "config.py"])
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "fix"])
    rc = g.cmd_guard_run(argparse.Namespace(history=False))
    assert rc == 0


def test_scan_history_finds_old_blob(tmp_path: Path):
    """A secret committed in an old commit and then removed in a later commit
    must still be caught by the history scan — that's the whole point of
    scanning history."""
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "[email protected]"])
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"])
    (repo / "creds.txt").write_text("OPENAI_KEY=sk-proj-aBcDeFgHiJkLmNoPqRsTuVwXyZ01234567890XYZ\n")
    subprocess.run(["git", "-C", str(repo), "add", "creds.txt"])
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"])
    # Remove the secret in a follow-up commit.
    (repo / "creds.txt").write_text("# credentials moved to vault\n")
    subprocess.run(["git", "-C", str(repo), "add", "creds.txt"])
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "move to vault"])

    violations = g.scan_history(repo, g.DEFAULT_SECRET_PATTERNS, allow_domains=[])
    assert any("OpenAI project" in v for v in violations), \
        f"history scan missed the old blob: {violations}"


def test_scan_history_blocks_oversized_blobs_instead_of_skipping(tmp_path: Path, monkeypatch):
    """A blob beyond the configured safe scan bound must fail closed."""
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    monkeypatch.setenv("GIT_PRIVATE2PUBLIC_MAX_BLOB_BYTES", "10")
    (repo / "big.bin").write_bytes(b"x" * 4096)
    subprocess.run(["git", "-C", str(repo), "add", "big.bin"])
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "[email protected]"])
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"])
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "big"])
    violations = g.scan_history(repo, rules=["regex:never-matches"], allow_domains=[])
    assert any("oversized blob" in violation for violation in violations)


def test_scan_history_empty_rules_returns_empty(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    assert g.scan_history(repo, rules=[], allow_domains=[]) == []


import argparse


def subprocess_completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_validate_config_single_repo_falls_back_to_origin(tmp_path: Path, monkeypatch):
    """When source and target are both empty, validate_config picks up the
    current repo's origin URL and uses it for both sides (single-repo mode)."""
    cfg = g.Config(source="", target="")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: subprocess_completed(
        stdout="git@github.com:me/repo.git\n", returncode=0
    ))
    g.validate_config(cfg, cwd=str(tmp_path))
    assert cfg.source == "git@github.com:me/repo.git"
    assert cfg.target == "git@github.com:me/repo.git"


def test_validate_config_single_repo_errors_without_origin(tmp_path: Path, monkeypatch):
    cfg = g.Config(source="", target="")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: subprocess_completed(
        stdout="", returncode=128
    ))
    with pytest.raises(SystemExit, match="single-repo mode"):
        g.validate_config(cfg, cwd=str(tmp_path))


def test_validate_config_mirror_mode_fills_missing_side():
    cfg = g.Config(source="git@github.com:me/priv.git", target="")
    g.validate_config(cfg)
    assert cfg.target == "git@github.com:me/priv.git"


def test_guard_run_message_mentions_publish_for_history(tmp_path: Path, monkeypatch, capsys):
    """When a violation lives only in git history, the error message must
    point the user at `git-private2public publish` or `git filter-repo`."""
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "[email protected]"])
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"])
    (repo / "creds.txt").write_text("OPENAI_KEY=sk-proj-aBcDeFgHiJkLmNoPqRsTuVwXyZ01234567890XYZ\n")
    subprocess.run(["git", "-C", str(repo), "add", "creds.txt"])
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "leak"])
    # Now overwrite the working-tree file so the leak lives only in history.
    (repo / "creds.txt").write_text("# moved to vault\n")
    subprocess.run(["git", "-C", str(repo), "add", "creds.txt"])
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "fix"])

    monkeypatch.chdir(repo)
    rc = g.cmd_guard_run(argparse.Namespace(history=True))
    out = capsys.readouterr().err
    assert rc == 1
    assert "git-private2public publish" in out
    assert "filter-repo" in out
    # Working-tree section must NOT appear (no live leak in HEAD).
    assert "Working-tree secrets" not in out


def test_guard_run_message_mentions_working_tree_fix(tmp_path: Path, monkeypatch, capsys):
    """When a violation is in the working tree, the message must point at
    the file and the simple edits / gitignore / git rm --cached path —
    not at history rewriting."""
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"])
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "[email protected]"])
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"])
    (repo / "config.py").write_text("debug = True\n")
    subprocess.run(["git", "-C", str(repo), "add", "config.py"])
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"])
    # Live leak in the working tree (not yet committed).
    (repo / "config.py").write_text("token = 'ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345'\n")

    monkeypatch.chdir(repo)
    rc = g.cmd_guard_run(argparse.Namespace(history=False))
    out = capsys.readouterr().err
    assert rc == 1
    assert "Working-tree secrets" in out
    assert "git rm --cached" in out
    # History section should NOT show because we ran --no-history.
    assert "git-private2public publish" not in out


def test_publish_uses_explicit_force_with_lease():
    """Forced publication must be tied to the exact target SHA observed before rewrite."""
    import inspect
    src = inspect.getsource(g.publish)
    assert "--force-with-lease=refs/heads/" in src
    assert 'push_cmd.append("--force")' not in src


def test_remote_ref_sha_returns_empty_for_missing_ref(monkeypatch):
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **kw: subprocess_completed(stdout="", returncode=0))
    assert g._remote_ref_sha("example", "refs/heads/main") == ""


def test_remote_ref_sha_reads_exact_sha(monkeypatch):
    sha = "a" * 40
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **kw: subprocess_completed(stdout=f"{sha}\trefs/heads/main\n", returncode=0))
    assert g._remote_ref_sha("example", "refs/heads/main") == sha


def test_remote_ref_sha_timeout_fails_closed_without_url_leak(monkeypatch):
    secret_url = "https://x-access-token:super-secret-token@example.test/private.git"

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=30)

    monkeypatch.setattr(g.subprocess, "run", timeout)
    with pytest.raises(SystemExit) as error:
        g._remote_ref_sha(secret_url, "refs/heads/main")

    message = str(error.value)
    assert message == "cannot read target ref: operation timed out"
    assert secret_url not in message
    assert "super-secret-token" not in message


@pytest.mark.parametrize(
    ("url", "diagnostic", "secret"),
    [
        (
            "https://x-access-token:private-publish-token@github.com/owner/public.git",
            "fatal: repository 'https://x-access-token:private-publish-token@github.com/owner/public.git/' unavailable",
            "private-publish-token",
        ),
        (
            "http://mirror-user:mirror-password@mirror.example/private.git/",
            "fatal: repository 'http://mirror-user:mirror-password@mirror.example/private.git' unavailable",
            "mirror-password",
        ),
        (
            "ssh://mirror-user:mirror-password@mirror.example/private.git",
            "fatal: repository 'ssh://mirror-user:mirror-password@mirror.example/private.git' unavailable",
            "mirror-password",
        ),
    ],
)
def test_target_push_error_redacts_url_credentials_and_token(url: str, diagnostic: str, secret: str):
    safe = g.redact_target_output(diagnostic, url, secret)

    assert secret not in safe
    assert "***@" in safe


@pytest.mark.parametrize(
    "unexpected_ref",
    ["refs/heads/stale", "refs/tags/stale", "refs/notes/private"],
)
def test_target_ref_allowlist_blocks_unexpected_refs_before_push(monkeypatch, unexpected_ref: str):
    monkeypatch.setattr(g, "_remote_refs", lambda url: {unexpected_ref: "a" * 40})

    with pytest.raises(SystemExit, match="unexpected target refs"):
        g._verify_target_ref_names("target", {"refs/heads/main"})


def test_target_ref_allowlist_accepts_configured_head_and_tags(monkeypatch):
    expected = {
        "refs/heads/main": "a" * 40,
        "refs/tags/v1.0.0": "b" * 40,
    }
    monkeypatch.setattr(g, "_remote_refs", lambda url: dict(expected))

    g._verify_target_ref_names("target", set(expected))
    g._verify_target_refs_exact("target", expected)


def test_target_ref_allowlist_can_preserve_existing_target_tags(monkeypatch):
    remote = {
        "refs/heads/main": "a" * 40,
        "refs/tags/node-v1024": "b" * 40,
        "refs/tags/v1021": "c" * 40,
    }
    monkeypatch.setattr(g, "_remote_refs", lambda url: dict(remote))

    preserved = g._verify_target_ref_names(
        "target", {"refs/heads/main"}, preserve_target_tags=True
    )

    assert preserved == {
        "refs/tags/node-v1024": "b" * 40,
        "refs/tags/v1021": "c" * 40,
    }


def test_preserve_target_tags_does_not_allow_unconfigured_branches(monkeypatch):
    monkeypatch.setattr(
        g,
        "_remote_refs",
        lambda url: {"refs/heads/stale": "a" * 40, "refs/tags/v1": "b" * 40},
    )

    with pytest.raises(SystemExit, match="unexpected target refs"):
        g._verify_target_ref_names(
            "target", {"refs/heads/main"}, preserve_target_tags=True
        )


def test_preserved_target_tag_change_fails_post_publish_verification(monkeypatch):
    expected = {
        "refs/heads/main": "a" * 40,
        "refs/tags/v1": "b" * 40,
    }
    monkeypatch.setattr(
        g,
        "_remote_refs",
        lambda url: {"refs/heads/main": "a" * 40, "refs/tags/v1": "c" * 40},
    )

    with pytest.raises(SystemExit, match="post-push target refs differ.*changed=refs/tags/v1"):
        g._verify_target_refs_exact("target", expected)


@pytest.mark.parametrize(
    "actual",
    [
        {"refs/tags/v1": "c" * 40},
        {"refs/tags/v1": "b" * 40, "refs/tags/v2": "d" * 40},
        {},
    ],
    ids=["changed", "added", "removed"],
)
def test_target_tag_snapshot_change_aborts_before_publication(actual):
    with pytest.raises(SystemExit, match="public target tags changed during sanitization"):
        g._verify_target_tags_unchanged({"refs/tags/v1": "b" * 40}, actual)


def test_preserve_target_tags_cannot_be_combined_with_pushing_source_tags():
    cfg = g.Config(
        source="owner/private",
        target="owner/public",
        push_tags=True,
        preserve_target_tags=True,
    )

    with pytest.raises(SystemExit, match="push_tags and preserve_target_tags"):
        g.validate_config(cfg)


def test_post_push_ref_verification_blocks_missing_or_changed_refs(monkeypatch):
    expected = {"refs/heads/main": "a" * 40, "refs/tags/v1.0.0": "b" * 40}
    monkeypatch.setattr(g, "_remote_refs", lambda url: {"refs/heads/main": "c" * 40})

    with pytest.raises(SystemExit, match="post-push target refs differ"):
        g._verify_target_refs_exact("target", expected)


def test_remote_refs_ignores_symbolic_head_and_peeled_tag(monkeypatch):
    tag = "b" * 40
    monkeypatch.setattr(
        g.subprocess,
        "run",
        lambda *args, **kwargs: subprocess_completed(
            stdout=(
                f"{'a' * 40}\tHEAD\n"
                f"{tag}\trefs/tags/v1\n"
                f"{'c' * 40}\trefs/tags/v1^{{}}\n"
            )
        ),
    )

    assert g._remote_refs("target") == {"refs/tags/v1": tag}


def test_reachable_scan_objects_returns_empty_mapping_for_unborn_repository(tmp_path: Path):
    repo = tmp_path / "unborn"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    assert g._reachable_scan_objects(repo, None) == {}


def test_history_scan_never_uses_scan_ignore_to_skip_an_aliased_blob(tmp_path: Path):
    repo = tmp_path / "private"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.test"], check=True)
    fake_token = "ghp_" + "A" * 36
    (repo / "ignored").mkdir()
    (repo / "ignored" / "fixture.txt").write_text(fake_token + "\n")
    (repo / "public.txt").write_text(fake_token + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "ignored/fixture.txt", "public.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "fixture alias"], check=True)

    violations = g.scan_history(
        repo, g.DEFAULT_SECRET_PATTERNS, [], scan_ignore=["ignored/**"]
    )

    assert violations
    assert any("<history:" in finding for finding in violations)


def test_current_tree_scan_ignore_skips_matching_fixture_path(tmp_path: Path):
    repo = tmp_path / "tree"
    (repo / "tests" / "fixtures").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    fake_token = "ghp_" + "C" * 36
    (repo / "tests" / "fixtures" / "sample.txt").write_text(fake_token + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "tests/fixtures/sample.txt"], check=True)
    cfg = g.Config(
        source="owner/private", target="owner/public",
        fail_on_match=g.DEFAULT_SECRET_PATTERNS,
        scan_ignore=["tests/fixtures/**"],
    )

    assert g.scan_tree(repo, cfg) == []


def test_hook_dispatcher_preserves_foreign_hook_and_combines_actions(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    hook_dir = repo / ".git" / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook = hook_dir / "pre-push"
    hook.write_text("#!/bin/sh\necho original\n")
    hook.chmod(0o755)
    monkeypatch.chdir(repo)

    g._set_hook_action(repo, "guard", True)
    g._set_hook_action(repo, "publish", True, str(repo / ".gitpublic"))

    content = hook.read_text()
    assert g.HOOK_DISPATCHER_MARKER in content
    assert "guard run" in content
    assert "publish -c" in content
    original = hook_dir / "pre-push.git-private2public-original"
    assert original.exists()
    assert "echo original" in original.read_text()

    g._set_hook_action(repo, "guard", False)
    g._set_hook_action(repo, "publish", False)
    assert "echo original" in hook.read_text()
    assert not original.exists()


def test_guard_json_report_is_machine_readable(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "token.txt").write_text("ghp_" + "A" * 36 + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "token.txt"], check=True)
    monkeypatch.chdir(repo)
    rc = g.cmd_guard_run(argparse.Namespace(history=False, format="json"))
    payload = json.loads(capsys.readouterr().out.strip())
    assert rc == 1
    assert payload["ok"] is False
    assert payload["count"] >= 1
    assert payload["findings"]


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-" + "api03-" + "A" * 32,
        "ASIA" + "A" * 16,
        "123456789:" + "A" * 35,
        "npm_" + "A" * 36,
        "pypi-" + "A" * 48,
        "sk_" + "live_" + "A" * 24,
        "rk_" + "test_" + "A" * 24,
        "SG." + "A" * 16 + "." + "B" * 20,
        "MTA" + "A" * 24 + "." + "B" * 6 + "." + "C" * 24,
        "SK" + "0123456789abcdef" * 2,
        "key-" + "0123456789abcdef" * 2,
        "dop_v1_" + "A" * 64,
    ],
)
def test_extended_default_patterns_detect_provider_credentials(tmp_path: Path, secret: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "credential.txt").write_text(secret + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "credential.txt"], check=True)

    violations = g.scan_local_repo(repo, g.DEFAULT_SECRET_PATTERNS, allow_domains=[])
    assert violations, f"expected built-in guard to detect {secret[:12]}…"


@pytest.mark.parametrize(
    "safe_text",
    [
        "accessToken: string",
        "clientSecret?: string",
        "sha1-deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        "0123456789abcdef0123456789abcdef01234567",
        "example npm token: npm_your_token_here",
        "Telegram token format: <bot-id>:<token>",
    ],
)
def test_extended_default_patterns_avoid_common_false_positives(tmp_path: Path, safe_text: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "safe.txt").write_text(safe_text + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "safe.txt"], check=True)

    assert g.scan_local_repo(repo, g.DEFAULT_SECRET_PATTERNS, allow_domains=[]) == []


def test_redacted_report_never_contains_secret():
    secret = b"ghp_" + b"A" * 32 + b"1b2C"
    report = g.format_violation("config.env", 7, g.DEFAULT_SECRET_PATTERNS[3], secret)
    assert secret.decode() not in report
    assert "1b2C" not in report
    assert "len=40" in report
    assert "sha256=" in report
    assert "config.env:7" in report
    assert "GitHub" in report


@pytest.mark.parametrize(
    ("secret", "hint"),
    [
        (b"sk-ant-" + b"A" * 20 + b"9XyZ", "sk-ant-…9XyZ"),
        (b"123456789:" + b"A" * 30 + b"7QwE", "123…:…7QwE"),
        (b"AKIA" + b"A" * 12 + b"4RtY", "AKIA…4RtY"),
    ],
)
def test_redacted_report_has_rotation_friendly_hint(secret: bytes, hint: str):
    rendered = g.redact_match(secret)
    assert hint not in rendered
    assert secret.decode() not in rendered


def test_effective_publish_rules_always_include_defaults_and_custom():
    custom = ["regex:company-secret-[0-9]+"]

    rules = g.effective_scan_rules(custom)

    assert rules[: len(g.DEFAULT_SECRET_PATTERNS)] == g.DEFAULT_SECRET_PATTERNS
    assert rules[-1] == custom[0]


def test_allow_domain_requires_exact_match(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "domains.txt").write_text("github.com\ngithub.com.evil.example\n")
    subprocess.run(["git", "-C", str(repo), "add", "domains.txt"], check=True)

    violations = g.scan_local_repo(
        repo,
        [r"regex:[a-z0-9.-]+\.[a-z]{2,}"],
        allow_domains=["github.com"],
    )

    assert len(violations) == 1


def test_history_enumeration_timeout_fails_closed(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=1)

    monkeypatch.setattr(g.subprocess, "run", timeout)
    with pytest.raises(g.ScanError, match="history enumeration timed out"):
        g.scan_history(repo, ["regex:secret"], [])


def test_reachable_history_scan_includes_tags_only_when_requested(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "[email protected]"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "value.txt").write_text("company-secret-12345\n")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "tagged secret"], check=True)
    subprocess.run(["git", "-C", str(repo), "tag", "unsafe"], check=True)
    (repo / "value.txt").write_text("public\n")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "clean"], check=True)
    subprocess.run(["git", "-C", str(repo), "rebase", "--onto", "HEAD", "HEAD~1"], check=False)
    # Make the branch root clean while retaining the old secret exclusively via tag.
    subprocess.run(["git", "-C", str(repo), "checkout", "--orphan", "clean-main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "rm", "-rf", "."], check=True, capture_output=True)
    (repo / "value.txt").write_text("public\n")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "clean root"], check=True)

    rule = ["regex:company-secret-[0-9]+"]
    assert g.scan_history(repo, rule, [], refs=["HEAD"]) == []
    assert g.scan_history(repo, rule, [], refs=["HEAD", "refs/tags/unsafe"])


def test_reachable_history_scan_checks_commit_messages(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "[email protected]"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "safe.txt").write_text("safe\n")
    subprocess.run(["git", "-C", str(repo), "add", "safe.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "contains company-secret-98765"],
        check=True,
    )

    violations = g.scan_history(repo, ["regex:company-secret-[0-9]+"], [])

    assert violations


def test_local_source_is_preferred_when_configured_remote_matches_origin(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:alice/private.git"], check=True)

    resolved = g.resolve_publish_source("git@github.com:alice/private.git", repo)

    assert resolved == str(repo.resolve())


def test_snapshot_root_discards_history_tags_and_source_metadata(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "private@example.test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Private Person"], check=True)
    (repo / "public.txt").write_text("first\n")
    subprocess.run(["git", "-C", str(repo), "add", "public.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "private message"], check=True)
    subprocess.run(["git", "-C", str(repo), "tag", "private-tag"], check=True)
    (repo / "public.txt").write_text("current\n")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "another private message"], check=True)

    g.create_snapshot_root(repo, include_source_sha=False)

    assert subprocess.run(["git", "-C", str(repo), "rev-list", "--count", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() == "1"
    assert subprocess.run(["git", "-C", str(repo), "tag", "--list"], capture_output=True, text=True, check=True).stdout.strip() == ""
    log = subprocess.run(["git", "-C", str(repo), "log", "--format=%an%n%ae%n%s"], capture_output=True, text=True, check=True).stdout
    assert "Private Person" not in log
    assert "private message" not in log
    assert (repo / "public.txt").read_text() == "current\n"


def test_snapshot_mode_rejects_tag_publication():
    cfg = g.Config(source="a", target="b", mode="snapshot", push_tags=True)

    with pytest.raises(SystemExit, match="snapshot mode cannot publish tags"):
        g.validate_config(cfg)


def test_publish_scan_blocks_builtin_secret_reachable_only_from_tag(tmp_path: Path):
    source = tmp_path / "private"
    target = tmp_path / "public.git"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "[email protected]"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "value.txt").write_text("ghp_" + "A" * 36 + "\n")
    subprocess.run(["git", "-C", str(source), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "tag only"], check=True)
    subprocess.run(["git", "-C", str(source), "tag", "unsafe"], check=True)
    subprocess.run(["git", "-C", str(source), "checkout", "--orphan", "clean-main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "rm", "-rf", "."], check=True, capture_output=True)
    (source / "value.txt").write_text("public\n")
    subprocess.run(["git", "-C", str(source), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "clean root"], check=True)
    cfg = g.Config(source=str(source), target=str(target), push_tags=True)

    assert g.publish(cfg, scan_only=True, cwd=str(source)) == 1
    assert subprocess.run(["git", "--git-dir", str(target), "show-ref"], capture_output=True).returncode == 1


def test_snapshot_publish_creates_single_public_root_without_tags(tmp_path: Path):
    source = tmp_path / "private"
    target = tmp_path / "public.git"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "private@example.test"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Private Person"], check=True)
    (source / "public.txt").write_text("old\n")
    subprocess.run(["git", "-C", str(source), "add", "public.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "private old"], check=True)
    subprocess.run(["git", "-C", str(source), "tag", "private-tag"], check=True)
    (source / "public.txt").write_text("current\n")
    subprocess.run(["git", "-C", str(source), "commit", "-qam", "private current"], check=True)
    cfg = g.Config(source=str(source), target=str(target), mode="snapshot")

    assert g.publish(cfg, cwd=str(source)) == 0

    count = subprocess.run(
        ["git", "--git-dir", str(target), "rev-list", "--count", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    refs = subprocess.run(
        ["git", "--git-dir", str(target), "for-each-ref", "--format=%(refname)"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    log = subprocess.run(
        ["git", "--git-dir", str(target), "log", "--format=%an%n%ae%n%s", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert count == "1"
    assert refs == ["refs/heads/main"]
    assert "Private Person" not in log
    assert "private current" not in log


def test_snapshot_prunes_secret_in_removed_private_history_before_scan(tmp_path: Path):
    source = tmp_path / "private"
    target = tmp_path / "public.git"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Private Person"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "private@example.test"], check=True)
    fake_token = "ghp_" + "B" * 36
    (source / "old.txt").write_text(fake_token + "\n")
    subprocess.run(["git", "-C", str(source), "add", "old.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "private old"], check=True)
    subprocess.run(["git", "-C", str(source), "rm", "-q", "old.txt"], check=True)
    (source / "safe.txt").write_text("public content\n")
    subprocess.run(["git", "-C", str(source), "add", "safe.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "current public source"], check=True)
    cfg = g.Config(source=str(source), target=str(target), mode="snapshot", push_force=True)

    assert g.publish(cfg, cwd=str(source)) == 0

    public_files = subprocess.run(
        ["git", "--git-dir", str(target), "ls-tree", "-r", "--name-only", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert public_files == ["safe.txt"]


def test_snapshot_publish_preserves_existing_public_release_tags_and_finds_no_new_leaks(tmp_path: Path, capfd):
    source = tmp_path / "private"
    seed = tmp_path / "seed"
    target = tmp_path / "public.git"
    source.mkdir()
    seed.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(seed)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True)

    for repo, name, email in (
        (source, "Private Person", "private@example.test"),
        (seed, "Public Release Bot", "public@example.test"),
    ):
        subprocess.run(["git", "-C", str(repo), "config", "user.name", name], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", email], check=True)

    # A previously published dummy credential-shaped fixture remains reachable
    # only through immutable release tags. Its source and value stay redacted.
    (seed / "public.txt").write_text("previous release " + "sk-" + "x" * 20 + "\n")
    subprocess.run(["git", "-C", str(seed), "add", "public.txt"], check=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "previous public snapshot"], check=True)
    subprocess.run(["git", "-C", str(seed), "tag", "node-v1024"], check=True)
    subprocess.run(["git", "-C", str(seed), "tag", "-a", "v1021", "-m", "release v1021"], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "-q", str(target), "main", "--tags"], check=True)
    before_tags = subprocess.run(
        ["git", "--git-dir", str(target), "for-each-ref", "--format=%(objectname) %(refname)", "refs/tags/"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    (source / "public.txt").write_text("current sanitized source\n")
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Private Person"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "private@example.test"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "public.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "private current source"], check=True)
    subprocess.run(["git", "-C", str(source), "tag", "private-only-tag"], check=True)

    cfg = g.Config(
        source=str(source), target=str(target), mode="snapshot",
        push_force=True, preserve_target_tags=True,
    )
    assert g.publish(cfg, cwd=str(source)) == 0
    publish_output = capfd.readouterr().err
    assert "pre-existing scan finding(s)" in publish_output
    assert "publication adds none" in publish_output

    after_tags = subprocess.run(
        ["git", "--git-dir", str(target), "for-each-ref", "--format=%(objectname) %(refname)", "refs/tags/"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    target_refs = subprocess.run(
        ["git", "--git-dir", str(target), "for-each-ref", "--format=%(refname)"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    public_log = subprocess.run(
        ["git", "--git-dir", str(target), "log", "--format=%an%n%s", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout

    assert after_tags == before_tags
    assert "refs/tags/private-only-tag" not in target_refs
    assert target_refs == ["refs/heads/main", "refs/tags/node-v1024", "refs/tags/v1021"]
    assert "Private Person" not in public_log
    assert "private current source" not in public_log
    assert subprocess.run(
        ["git", "--git-dir", str(target), "rev-list", "--count", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip() == "1"


def test_snapshot_publish_aborts_before_branch_push_if_target_tag_changes(tmp_path: Path, monkeypatch):
    source = tmp_path / "private"
    seed = tmp_path / "seed"
    target = tmp_path / "public.git"
    source.mkdir()
    seed.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(seed)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True)
    for repo in (source, seed):
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.test"], check=True)
    (seed / "public.txt").write_text("first\n")
    subprocess.run(["git", "-C", str(seed), "add", "public.txt"], check=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "first"], check=True)
    first = subprocess.run(["git", "-C", str(seed), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-C", str(seed), "tag", "v1"], check=True)
    (seed / "public.txt").write_text("second\n")
    subprocess.run(["git", "-C", str(seed), "commit", "-qam", "second"], check=True)
    second = subprocess.run(["git", "-C", str(seed), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-C", str(seed), "push", "-q", str(target), "main", "--tags"], check=True)
    original_main = subprocess.run(["git", "--git-dir", str(target), "rev-parse", "refs/heads/main"], capture_output=True, text=True, check=True).stdout.strip()
    (source / "public.txt").write_text("sanitized current\n")
    subprocess.run(["git", "-C", str(source), "add", "public.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "private current"], check=True)

    verify = g._verify_target_ref_names
    changed = False

    def change_target_tag(url, allowed_refs, *, preserve_target_tags=False):
        nonlocal changed
        if not changed:
            subprocess.run(
                ["git", "--git-dir", str(target), "update-ref", "refs/tags/v1", second],
                check=True,
            )
            changed = True
        return verify(url, allowed_refs, preserve_target_tags=preserve_target_tags)

    monkeypatch.setattr(g, "_verify_target_ref_names", change_target_tag)
    cfg = g.Config(source=str(source), target=str(target), mode="snapshot", push_force=True, preserve_target_tags=True)

    with pytest.raises(SystemExit, match="public target tags changed during sanitization"):
        g.publish(cfg, cwd=str(source))

    assert subprocess.run(["git", "--git-dir", str(target), "rev-parse", "refs/heads/main"], capture_output=True, text=True, check=True).stdout.strip() == original_main
    assert subprocess.run(["git", "--git-dir", str(target), "rev-parse", "refs/tags/v1"], capture_output=True, text=True, check=True).stdout.strip() == second
    assert first != second


def test_target_tag_change_after_preflight_is_reported_after_branch_push(tmp_path: Path):
    source = tmp_path / "private"
    seed = tmp_path / "seed"
    target = tmp_path / "public.git"
    source.mkdir()
    seed.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(seed)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True)
    for repo in (source, seed):
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.test"], check=True)
    (seed / "public.txt").write_text("first\n")
    subprocess.run(["git", "-C", str(seed), "add", "public.txt"], check=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "first"], check=True)
    first = subprocess.run(["git", "-C", str(seed), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-C", str(seed), "tag", "v1"], check=True)
    (seed / "public.txt").write_text("second\n")
    subprocess.run(["git", "-C", str(seed), "commit", "-qam", "second"], check=True)
    second = subprocess.run(["git", "-C", str(seed), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-C", str(seed), "push", "-q", str(target), "main", "--tags"], check=True)
    original_main = subprocess.run(["git", "--git-dir", str(target), "rev-parse", "refs/heads/main"], capture_output=True, text=True, check=True).stdout.strip()
    (source / "public.txt").write_text("sanitized current\n")
    subprocess.run(["git", "-C", str(source), "add", "public.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "private current"], check=True)

    hook = target / "hooks" / "pre-receive"
    hook.write_text(
        f'#!/bin/sh\nunset GIT_QUARANTINE_PATH\n'
        f'git --git-dir="$GIT_DIR" update-ref refs/tags/v1 {second}\n'
    )
    hook.chmod(0o755)
    cfg = g.Config(source=str(source), target=str(target), mode="snapshot", push_force=True, preserve_target_tags=True)

    with pytest.raises(SystemExit, match="post-push target refs differ.*changed=refs/tags/v1"):
        g.publish(cfg, cwd=str(source))

    published_main = subprocess.run(["git", "--git-dir", str(target), "rev-parse", "refs/heads/main"], capture_output=True, text=True, check=True).stdout.strip()
    assert published_main != original_main
    assert subprocess.run(["git", "--git-dir", str(target), "rev-parse", "refs/tags/v1"], capture_output=True, text=True, check=True).stdout.strip() == second
    assert first != second


def test_publish_blocks_stale_target_ref_before_writing_configured_head(tmp_path: Path):
    source = tmp_path / "private"
    target = tmp_path / "public.git"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "[email protected]"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "safe.txt").write_text("safe\n")
    subprocess.run(["git", "-C", str(source), "add", "safe.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "safe"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "push", "-q", str(target), "HEAD:refs/heads/stale"],
        check=True,
    )
    cfg = g.Config(source=str(source), target=str(target), mode="snapshot")

    with pytest.raises(SystemExit, match="unexpected target refs"):
        g.publish(cfg, cwd=str(source))

    refs = subprocess.run(
        ["git", "--git-dir", str(target), "for-each-ref", "--format=%(refname)"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert refs == ["refs/heads/stale"]


def test_snapshot_overlay_replaces_private_readme_without_source_history(tmp_path: Path):
    source = tmp_path / "private"
    target = tmp_path / "public.git"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "private@example.test"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Private Person"], check=True)
    (source / "README.md").write_text("private internal instructions\n")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "private history"], check=True)
    overlay = source / ".gitpublic" / "public"
    overlay.mkdir(parents=True)
    (overlay / "README.md").write_text("# Public project\n")
    cfg = g.Config(source=str(source), target=str(target), delete=["README.md"], mode="snapshot")

    assert g.publish(cfg, cwd=str(source)) == 0

    public_readme = subprocess.run(
        ["git", "--git-dir", str(target), "show", "refs/heads/main:README.md"],
        capture_output=True, text=True, check=True,
    ).stdout
    count = subprocess.run(
        ["git", "--git-dir", str(target), "rev-list", "--count", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    log = subprocess.run(
        ["git", "--git-dir", str(target), "log", "--format=%an%n%ae%n%s", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert public_readme == "# Public project\n"
    assert count == "1"
    assert "Private Person" not in log
    assert "private history" not in log


def test_public_overlay_rejects_symlinks_and_non_regular_files(tmp_path: Path):
    overlay = tmp_path / "overlay"
    work = tmp_path / "work"
    overlay.mkdir()
    work.mkdir()
    (overlay / "link").symlink_to(tmp_path / "outside")

    with pytest.raises(g.OverlayError, match="symlink"):
        g.apply_public_overlay(overlay, work)

    (overlay / "link").unlink()
    fifo = overlay / "pipe"
    fifo.parent.mkdir(exist_ok=True)
    os.mkfifo(fifo)
    with pytest.raises(g.OverlayError, match="non-regular"):
        g.apply_public_overlay(overlay, work)


def test_public_overlay_rejects_destination_symlink_escape(tmp_path: Path):
    overlay = tmp_path / "overlay"
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    (overlay / "nested").mkdir(parents=True)
    (overlay / "nested" / "README.md").write_text("public\n")
    work.mkdir()
    outside.mkdir()
    (work / "nested").symlink_to(outside, target_is_directory=True)

    with pytest.raises(g.OverlayError, match="destination symlink"):
        g.apply_public_overlay(overlay, work)

    assert not (outside / "README.md").exists()


def test_overlay_content_is_scanned_before_push(tmp_path: Path):
    source = tmp_path / "private"
    target = tmp_path / "public.git"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(target)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "[email protected]"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "safe.txt").write_text("safe\n")
    subprocess.run(["git", "-C", str(source), "add", "safe.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "safe"], check=True)
    overlay = source / ".gitpublic" / "public"
    overlay.mkdir(parents=True)
    (overlay / "credential.txt").write_text("ghp_" + "A" * 36 + "\n")
    cfg = g.Config(source=str(source), target=str(target), mode="snapshot")

    assert g.publish(cfg, cwd=str(source)) == 1
    assert subprocess.run(["git", "--git-dir", str(target), "show-ref"], capture_output=True).returncode == 1


def test_doctor_reports_missing_tools_and_config(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(g.shutil, "which", lambda name: None)
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **kw: subprocess_completed(returncode=1))
    rc = g.cmd_doctor(argparse.Namespace(config=".gitpublic"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "git-filter-repo" in out
    assert "not inside a git repository" in out
    assert "Doctor:" in out


def test_scan_without_config_uses_builtin_guard(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "safe.txt").write_text("hello\n")
    subprocess.run(["git", "-C", str(repo), "add", "safe.txt"], check=True)
    monkeypatch.chdir(repo)
    assert g.cmd_scan(argparse.Namespace(config=".gitpublic", format="text")) == 0


def test_scan_without_config_detects_secret(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "token.txt").write_text("ghp_" + "A" * 36 + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "token.txt"], check=True)
    monkeypatch.chdir(repo)
    assert g.cmd_scan(argparse.Namespace(config=".gitpublic", format="text")) == 1


def test_default_command_scans_and_does_not_prompt_noninteractive(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "safe.txt").write_text("safe\n")
    subprocess.run(["git", "-C", str(repo), "add", "safe.txt"], check=True)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(g.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(g.sys.stdout, "isatty", lambda: False)
    assert g.cmd_default(argparse.Namespace()) == 0
    assert not (repo / ".git" / "hooks" / "pre-push").exists()


def test_interactive_default_can_enable_guard(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(g.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(g.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    g._interactive_offer_guard()
    hook = repo / ".git" / "hooks" / "pre-push"
    assert hook.exists()
    assert "guard run" in hook.read_text()


def test_interactive_init_uses_origin_and_asks_only_for_public_target(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:alice/private.git"], check=True)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(g.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(g.sys.stdout, "isatty", lambda: True)
    answers = iter(["", "alice/public"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    rc = g.cmd_init(argparse.Namespace(path=".gitpublic", force=False))
    config = (repo / ".gitpublic" / "config").read_text()

    assert rc == 0
    assert "source = git@github.com:alice/private.git" in config
    assert "target = alice/public" in config


def test_noninteractive_init_remains_deterministic(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(g.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(g.sys.stdout, "isatty", lambda: False)

    rc = g.cmd_init(argparse.Namespace(path=".gitpublic", force=False))
    config = (tmp_path / ".gitpublic" / "config").read_text()

    assert rc == 0
    assert "source = you/private-repo" in config
    assert "target = you/public-repo" in config
