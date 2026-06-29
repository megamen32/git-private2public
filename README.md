# git-private2public

**Keep a public copy of your private repo. Automatically scrub secrets.**

You work in a private repo. It has your real server names, IPs, tokens, personal
config. You want to also have a public repo — but without leaking any of that.

This tool does it for you. Every time you run it, it:

1. Copies your private repo
2. Deletes files you said to delete
3. Replaces secrets with `***`
4. Checks the result for anything you missed
5. Pushes the clean version to your public repo

## The 30-second version

```
pip install git-filter-repo pyyaml

git-private2public init          # writes a config file
# edit .git-private2public.yaml — say what to delete and replace
git-private2public publish       # done
```

That's it. Your public repo is now clean.

## Easy mode (just ignore files)

Most people just want to **not publish some files**. Like `.gitignore`, but for
the public version.

```yaml
# .git-private2public.yaml
source: you/private-repo
target: you/public-repo

ignore:          # these won't be in the public repo. that's it.
  - ".env"
  - "secrets/"
  - "*.key"
  - "my-personal-notes.md"
  - "deploy/nginx/real-domain.conf"
```

Run `git-private2public publish`. Done.

## Medium mode (also scrub secrets inside files)

Sometimes a file has both public stuff AND a secret inside. Like a config
template with your real IP.

```yaml
source: you/private-repo
target: you/public-repo

ignore:
  - ".env"

replace:         # find → replace, in file contents
  - "10.0.0.5==>203.0.113.5"       # your real IP → example IP
  - "real-token-xxx==>***"         # your token → stars
  - "my-server.example.com==>example.com"
```

## Hard mode (regex, scan, CI, allowlists)

For power users. Full config with all options:

```yaml
source: you/private-repo
target: you/public-repo

# Files/dirs to remove from the entire history.
ignore:
  - "secrets/"
  - "*.env"
  - "*.key"

# Text to replace inside files. Literal by default.
# Prefix with "regex:" for regex. "glob:*.json:" to scope to file type.
replace:
  - "real-token==>***REMOVED***"
  - "regex:[A-Fa-f0-9]{64}==>***REMOVED***"        # catch any 64-char hex
  - "glob:*.json:secret==>x"                       # only in .json files

# Domains that are OK to publish (won't trigger the scan below).
# Use this so public install URLs (like get.docker.com) survive.
allow_domains:
  - "get.docker.com"
  - "example.com"

# Refuse to push if these appear in the final result.
# Catches anything your rules above missed.
fail_on_match:
  - "regex:github_pat_[A-Za-z0-9_]{30,}"     # GitHub tokens
  - "regex:sk-[A-Za-z0-9]{40,}"              # OpenAI keys
  - "regex:192\\.168\\."                     # private IPs
  - "regex:10\\.0\\.0\\."                    # private IPs

push:
  force: true
  branches: [main]
```

## Commands

```
git-private2public init        # write an example config
git-private2public scan        # check what would happen (no push)
git-private2public publish     # sanitize + push to public repo
```

## Auth

For GitHub, set a token so it can push to your public repo:

```bash
export GIT_PRIVATE2PUBLIC_TOKEN=ghp_xxx
git-private2public publish
```

Or put the token in the target URL in the config.

## Auto-run in CI

Add this to `.github/workflows/publish.yml` in your **private** repo. Every
push to `main` → clean public mirror.

```yaml
on:
  push:
    branches: [main]
jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - run: pip install git-filter-repo pyyaml
      - run: |
          curl -fsSL https://raw.githubusercontent.com/megamen32/git-private2public/main/git-private2public.py \
            -o git-private2public && chmod +x git-private2public
      - run: ./git-private2public publish -c .git-private2public.yaml
        env:
          GIT_PRIVATE2PUBLIC_TOKEN: ${{ secrets.PUBLIC_REPO_PAT }}
```

## Why

Git and GitHub have no built-in "make this file private in a public repo".
Visibility is repo-level. So you need two repos. This tool keeps them in sync
without leaking.

Other tools do part of the job:

| | delete files | replace text | scan for leaks | auto push | one config |
|---|:---:|:---:|:---:|:---:|:---:|
| git-filter-repo | ✅ | ✅ | ❌ | ❌ | ❌ |
| BFG | ✅ | ✅ | ❌ | ❌ | ❌ |
| dupligit | ❌ | ❌ | ❌ | ✅ | ✅ |
| **git-private2public** | ✅ | ✅ | ✅ | ✅ | ✅ |

## Install

```bash
pip install git-filter-repo pyyaml
curl -fsSL https://raw.githubusercontent.com/megamen32/git-private2public/main/git-private2public.py \
  -o git-private2public && chmod +x git-private2public
```

## License

MIT
