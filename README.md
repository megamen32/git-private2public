# git-private2public

**[English](./README.md)** · **[Русский](./README.ru.md)**

---

**Like `.gitignore`, but for what goes public.**

You have a private repo. You want a public one — without the secrets. This
tool keeps them in sync. Automatically.

## Quick start

```bash
pip install git-private2public
git-private2public init          # creates .gitpublic/ folder
```

Edit `.gitpublic/config` — set source + target. Values can be `owner/repo`, a full Git URL, or a local path:

```
source = you/private-repo
target = you/public-repo
```

Edit `.gitpublic/ignore` — files to hide, one per line (like `.gitignore`):

```
.env
secrets/
*.key
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

## The `.gitpublic/` folder

Each file is one concern. Like `.gitignore` — one rule per line, `#` for
comments. If a file is missing, that setting is just empty.

| File | What goes in it | Format |
|------|-----------------|--------|
| `config` | source, target, push settings | `key = value` |
| `ignore` | files to NOT publish | one path/glob per line |
| `replace` | find → replace in file contents | `old ==> new` per line |
| `scan` | refuse to push if matched | one pattern per line |
| `allow` | domains OK to publish when scan matches nearby text | one domain per line |

**Easy** — just edit `ignore`:

```
.env
secrets/
*.key
```

**Medium** — also edit `replace`:

```
<PRIVATE_IP> ==> 203.0.113.5
real-token ==> ***
regex:[A-Fa-f0-9]{64} ==> ***
```

**Hard** — also edit `scan` + `allow`:

```
# scan:
regex:github_pat_[A-Za-z0-9_]{30,}
regex:192\.168\.
regex:[a-z0-9.-]+\.[a-z]{2,}

# allow:
github.com
get.docker.com
```

## Commands

```
init        create .gitpublic/ config
scan        clean into a temp repo, scan, don't push
publish     clean + push
hook        enable / disable / status
```

## How `allow` / domains work

`allow` is not a replacement rule. It is an allowlist used during scanning.

Example: put a broad domain detector into `.gitpublic/scan`:

```
regex:[a-z0-9.-]+\.[a-z]{2,}
```

Now every domain-like string fails the scan unless the matched domain is listed in `.gitpublic/allow`:

```
github.com
get.docker.com
```

Use `.gitpublic/replace` to rewrite private domains, for example:

```
private.company.local ==> example.com
regex:.*\.corp\.internal ==> example.com
```

## Install

```bash
pip install git-private2public
```

That's it. Now you have the `git-private2public` command.

> No pip? [Single-file manual install](./git_private2public.py) — download +
> `chmod +x` (needs `pip install git-filter-repo pyyaml`).

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
