# git-private2public

**[English](./README.md)** · **[Русский](./README.ru.md)**

---

**Like `.gitignore`, but for what goes public.**

You have a private repo. You want a public one — without the secrets. This
tool keeps them in sync. Automatically.

## Quick start

```bash
pip install git-filter-repo pyyaml
git-private2public init          # creates .git-private2public.yaml
```

Edit the config — say what to hide:

```yaml
source: you/private-repo
target: you/public-repo

ignore:
  - ".env"
  - "secrets/"
  - "*.key"
```

Publish:

```bash
git-private2public publish
```

Done. Your public repo is clean.

## Auto-publish on every `git push`

```bash
git-private2public hook enable     # on
git push                           # also publishes public mirror
git-private2public hook disable    # off
```

Native git hook. No CI, no GitHub Actions. Works offline.

## Modes

**Easy** — just ignore files (the example above).

**Medium** — also scrub secrets inside files:

```yaml
replace:
  - "10.0.0.5==>203.0.113.5"
  - "real-token==>***"
```

**Hard** — regex, scan, refuse to push if anything survives:

```yaml
replace:
  - "regex:[A-Fa-f0-9]{64}==>***"
fail_on_match:
  - "regex:github_pat_[A-Za-z0-9_]{30,}"
  - "regex:192\\.168\\."
allow_domains:           # public URLs that are OK
  - "get.docker.com"
```

## Commands

```
init        create config
scan        check, don't push
publish     clean + push
hook        enable / disable / status
```

## Install

```bash
pip install git-filter-repo pyyaml
curl -fsSL https://raw.githubusercontent.com/megamen32/git-private2public/main/git-private2public.py \
  -o git-private2public && chmod +x git-private2public
```

## Why

Git has no "private file in a public repo". So you need two repos. This keeps
them in sync — without leaking.

| | delete files | replace text | scan | auto push |
|---|:---:|:---:|:---:|:---:|
| git-filter-repo | ✅ | ✅ | ❌ | ❌ |
| BFG | ✅ | ✅ | ❌ | ❌ |
| dupligit | ❌ | ❌ | ❌ | ✅ |
| **git-private2public** | ✅ | ✅ | ✅ | ✅ |

## License

MIT
