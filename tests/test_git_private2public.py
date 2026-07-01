from pathlib import Path
import subprocess

import pytest

import git_private2public as g


def test_folder_config_loads_allow_and_domains_alias(tmp_path: Path):
    cfgdir = tmp_path / ".gitpublic"
    cfgdir.mkdir()
    (cfgdir / "config").write_text("source = owner/private\ntarget = owner/public\npush_force = false\npush_branches = main,dev\n")
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
    assert "private.example.local" in violations[0]


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
    assert g.__version__ == "0.1.4"


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
    assert any("GitHub PAT" in v or "ghp_" in v for v in violations)


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

    # Pretend we're inside the repo
    monkeypatch.chdir(repo)
    rc = g.cmd_guard_run(argparse.Namespace())
    assert rc == 1

    # Now remove the secret and re-run
    (repo / "config.py").write_text("debug = True\n")
    subprocess.run(["git", "-C", str(repo), "add", "config.py"])
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "fix"])
    rc = g.cmd_guard_run(argparse.Namespace())
    assert rc == 0


import argparse


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


import subprocess as _sp


def subprocess_completed(stdout: str = "", returncode: int = 0) -> _sp.CompletedProcess:
    return _sp.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")
