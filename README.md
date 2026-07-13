# git-private2public

[![PyPI version](https://img.shields.io/pypi/v/git-private2public.svg)](https://pypi.org/project/git-private2public/) [![CI](https://github.com/megamen32/git-private2public/actions/workflows/ci.yml/badge.svg)](https://github.com/megamen32/git-private2public/actions/workflows/ci.yml)

**[English](./README.md)** · **[Русский](./README.ru.md)**

---

**Like `.gitignore`, but for what goes public.**

Need the full rule-by-rule explanation? Read [Advanced configuration](./docs/ADVANCED.md) / [RU](./docs/ADVANCED.ru.md).

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

## Guard — pre-push safety net

`guard` installs a lightweight pre-push hook that **blocks** `git push` if
scanned content matches known secret patterns. Unlike `hook` (which also
rewrites history and force-pushes a public mirror), `guard` is purely a
refusal mechanism — no clone, no filter-repo, no push.

```bash
git-private2public guard enable    # install pre-push hook
git-private2public guard status    # is it on?
git-private2public guard disable   # remove the hook
git-private2public guard run       # manual scan (also what the hook does)
```

**What it scans — by default:**
1. Every tracked file in the working tree (`git ls-files`).
2. **Every blob in git history** — catches secrets committed in old commits
   and later removed from HEAD but never rewritten via filter-repo.
   Use `--no-history` to skip this (faster, but won't catch old leaks).

**Default secret patterns (always on, no `.gitpublic/` required):**
- OpenAI and Anthropic: `sk-...`, `sk-proj-...`, `sk-ant-...`
- GitHub: `ghp_...`, `github_pat_...`, `gho_...`, `ghs_...`, `ghr_...`
- HuggingFace and Slack: `hf_...`, `xox[baprs]-...`
- AWS long-term and temporary access key IDs: `AKIA...`, `ASIA...`
- Google API/OAuth keys and GitLab PATs
- Telegram bot, npm, PyPI, Stripe, SendGrid and Discord tokens
- Twilio, Mailgun and DigitalOcean API credentials
- Generic JWTs and PEM private-key headers

**Custom patterns:** drop them into `.gitpublic/scan`, one per line (literal
or `regex:...`). They layer on top of the defaults.

**Allowlist:** anything in `.gitpublic/allow` is treated as an exception for
broad regex rules that happen to match a public domain.

**Bypass for one push (NOT recommended):**

```bash
GIT_PRIVATE2PUBLIC_SKIP_GUARD=1 git push
```

**What guard tells you when it refuses the push:**

The error message is tailored to where the secret lives:

- **In the working tree** — points at the file, suggests editing,
  `.gitignore` + `git rm --cached`, and reminds you to rotate a live
  secret before committing.
- **In git history** — explains that editing HEAD isn't enough, and
  prints the exact next step:
  - `git-private2public publish` (if you have a `.gitpublic/` set up), or
  - `git filter-repo --replace-text replacements.txt --force` (manual),
    with the `replacements.txt` format.

When both happen, you get both sections.

If you only want guard and not `hook` (publish) — `guard enable` works
independently. They share the same pre-push file, so `guard enable` refuses
to install if `hook` is already there; disable one before enabling the
other.

### Why scan history?

A secret leaked into a commit last month, removed from HEAD yesterday, but
the commit object still exists in `.git/objects/`. Pushing HEAD won't
expose it directly, but anyone who already has the repo and runs
`git log -p` will see it. Guard's history scan catches this. To actually
*remove* it from history, run `git-private2public publish` (which uses
filter-repo), or `git filter-repo --replace-text` manually.

## The `.gitpublic/` folder

Each file is one concern. Like `.gitignore` — one rule per line, `#` for
comments. If a file is missing, that setting is just empty.

| File | What goes in it | Format |
|------|-----------------|--------|
| `config` | source, target, push settings | `key = value` |
| `ignore` | files to NOT publish | one path/glob per line |
| `replace` | find → replace in file contents | `old ==> new` per line |
| `scan` | refuse to push if matched | one pattern per line |
| `allow` | exceptions for domain rules in `scan` | one allowed matched domain per line |

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
guard       enable / disable / status / run  (pre-push secret scanner)
```

## How `allow` / domains work

Nothing is auto-blocked just because it is a domain.

`allow` is an exception list for `scan`. If `.gitpublic/scan` is missing or empty, `allow` does nothing and domains are not checked at all.

To block domain-looking strings, add a broad domain rule to `.gitpublic/scan`:

```
regex:[a-z0-9.-]+\.[a-z]{2,}
```

Now every matched domain fails the scan unless the matched domain itself is listed in `.gitpublic/allow`:

```
github.com
get.docker.com
example.com
```

`allow` does not replace private domains. Use `.gitpublic/replace` for that:

```
private.company.local ==> example.com
regex:.*\.corp\.internal ==> example.com
```

Analogy: `scan` says “ban everything matching this pattern”, `allow` says “except these exact public domains”.

Rule of thumb:

| You want to... | File |
|---|---|
| remove files | `.gitpublic/ignore` |
| rewrite private text/domain/IP | `.gitpublic/replace` |
| fail if a secret/domain/IP survived | `.gitpublic/scan` |
| make exceptions for public domains caught by scan | `.gitpublic/allow` |

More examples: [Advanced configuration](./docs/ADVANCED.md) / [RU](./docs/ADVANCED.ru.md).

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

| | delete files | replace text | scan | auto push | pre-push guard |
|---|:---:|:---:|:---:|:---:|:---:|
| git-filter-repo | ✅ | ✅ | ❌ | ❌ | ❌ |
| BFG | ✅ | ✅ | ❌ | ❌ | ❌ |
| dupligit | ❌ | ❌ | ❌ | ✅ | ❌ |
| gitleaks / trufflehog | ❌ | ❌ | ✅ | ❌ | ✅ (separate tool) |
| **git-private2public** | ✅ | ✅ | ✅ | ✅ | ✅ (built-in) |

## License

MIT

### Diagnose setup

```bash
git-private2public doctor
```

`doctor` checks Git, `git-filter-repo`, repository/config health, secret-rule files,
the pre-push hook, and source/target remote access. Secret findings are always
redacted and shown as type + location + safe prefix/suffix hint + length + short SHA-256 fingerprint; the
matched credential itself is never printed.

### Safe publication transactions

`publish` records the current target branch SHA before sanitizing and uses an
explicit `--force-with-lease` tied to that SHA. If somebody updates the public
branch while the private history is being cleaned, publication stops instead
of overwriting their work. After a successful push, the remote SHA is verified.
Targets under 100 MiB are cloned and scanned again automatically; larger repos
skip the extra clone but still receive SHA verification. Existing tags are
never force-overwritten.

For CI, `scan`, `publish --scan`, and `guard run` support `--format json`.

The managed pre-push dispatcher can run both guard and auto-publish while
preserving and chaining an existing user hook.
