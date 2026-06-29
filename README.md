# git-privado

**Sanitize & mirror a private repo to a public one — config-driven.**

`git-privado` is a small CLI that keeps a public mirror of a private repo,
scrubbed according to rules you define in a YAML file. It wraps
[`git-filter-repo`](https://github.com/newren/git-filter-repo) with a sane,
declarative config, and adds a secret-scan gate so the push is refused if
anything sensitive survives.

> Born from a real need: maintaining an open-core project where the private
> repo has real server names, IPs, tokens, and personal config — and the
> public repo must never leak any of it.

## Why

Git and GitHub have no built-in "mark this file private in a public repo".
Visibility is repo-level. So you need two repos — but syncing them by hand is
error-prone. Existing tools each do part of the job:

| Tool | Path delete | Text replace | Auto-sync | Config file |
|------|:-----------:|:------------:|:---------:|:-----------:|
| git-filter-repo | ✅ | ✅ | ❌ | ❌ |
| BFG | ✅ | ✅ | ❌ | ❌ |
| dupligit | ❌ | ❌ | ✅ | ✅ |
| **git-privado** | ✅ | ✅ | ✅ | ✅ |

## Install

```bash
pip install git-filter-repo pyyaml
curl -fsSL https://raw.githubusercontent.com/megamen32/git-privado/main/git-privado.py \
  -o /usr/local/bin/git-privado && chmod +x /usr/local/bin/git-privado
```

## Quick start

```bash
# 1. Write a config
git-privado init > .git-privado.yaml
# edit it

# 2. Scan (no push) — see what would change
git-privado scan -c .git-privado.yaml

# 3. Publish (sanitize + push to target)
GITPRIVADO_TOKEN=ghp_xxx git-privado publish -c .git-privado.yaml
```

## Config reference

```yaml
source: owner/private-repo        # private repo (owner/name, URL, or local path)
target: owner/public-repo         # public repo to push to

delete:                           # remove from ENTIRE history
  - "secrets/"                    # directory (trailing /)
  - "*.env"                       # glob
  - "config/real-domain.conf"     # exact file

replace:                          # replace in file CONTENTS across history
  - "real-token-xxx==>***REMOVED***"
  - "10.0.0.==>192.0.2."          # literal
  - "regex:[A-Fa-f0-9]{64}==>***REMOVED-HEX***"        # regex
  - "glob:*.json:secret==>x"      # scoped to *.json files only

allow_domains:                    # domains OK to publish (skip fail_on_match)
  - "example.com"
  - "get.docker.com"

fail_on_match:                    # refuse to push if these appear in final tree
  - "regex:github_pat_[A-Za-z0-9_]{30,}"
  - "regex:sk-[A-Za-z0-9]{40,}"
  - "regex:192\\.168\\."

push:
  force: true                     # required (history is rewritten)
  branches: [main]
  tags: false
```

## How it works

1. **Clones** the source repo to a temp dir (full history)
2. **Runs `git-filter-repo`** with `--invert-paths` (delete rules) and
   `--replace-text` (replace rules) — rewrites ALL history
3. **Scans** the final tree against `fail_on_match` patterns (with
   `allow_domains` as escape hatch)
4. **Pushes** (force) to the target repo — only if scan passes

## Auth

For GitHub targets, set `GITPRIVADO_TOKEN` env var (a PAT with push access to
the target repo). It's injected as `x-access-token:` in the push URL.

For other hosts, embed creds in the `target:` URL or use a git credential
helper.

## CI (GitHub Actions)

```yaml
# .github/workflows/publicize.yml
on:
  push:
    branches: [main]
jobs:
  publicize:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - run: pip install git-filter-repo pyyaml
      - run: curl -fsSL https://raw.githubusercontent.com/megamen32/git-privado/main/git-privado.py -o git-privado && chmod +x git-privado
      - run: ./git-privado publish -c .git-privado.yaml
        env:
          GITPRIVADO_TOKEN: ${{ secrets.PUBLIC_REPO_PAT }}
```

## Rule format cheatsheet

| Rule type | Syntax | Example |
|-----------|--------|---------|
| Delete dir | `dir/` | `secrets/` |
| Delete glob | `*.ext` | `*.env` |
| Delete file | `path` | `config/real.conf` |
| Replace literal | `old==>new` | `secret==>***` |
| Replace regex | `regex:pat==>new` | `regex:[0-9a-f]{64}==>*` |
| Replace scoped | `glob:*.json:old==>new` | glob + literal |

## Why not just git-filter-repo?

`git-filter-repo` is the engine. git-privado adds:
- **One config file** instead of re-typing CLI args every time
- **Secret-scan gate** — refuse to push if `fail_on_match` hits anything
- **Allowlist** — keep public install URLs while scrubbing private IPs
- **Push automation** — clone → filter → scan → push in one command

## License

MIT
