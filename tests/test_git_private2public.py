from pathlib import Path

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
    assert g.__version__ == "0.1.1"
