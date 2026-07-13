# git-private2public

[![PyPI version](https://img.shields.io/pypi/v/git-private2public.svg)](https://pypi.org/project/git-private2public/) [![CI](https://github.com/megamen32/git-private2public/actions/workflows/ci.yml/badge.svg)](https://github.com/megamen32/git-private2public/actions/workflows/ci.yml)

**[English](./README.md)** · **[Русский](./README.ru.md)**

**Like `.gitignore`, but for what goes public.**

Install it, run it inside a Git repository, and follow the prompts.

```bash
pip install git-private2public
git-private2public
```

That is enough to scan tracked files and Git history for known credentials. No config is required. In an interactive terminal, the tool can also enable automatic checks before every `git push`.

## What do you want to do?

### Check this repository

```bash
git-private2public
```

The tool scans the repository and explains what it found. Secret values are never printed in full.

### Publish a safe public copy

```bash
git-private2public init
git-private2public publish
```

`init` guides you through the one-time setup. `publish` creates a cleaned public mirror without changing the private repository.

### Something is not working

```bash
git-private2public doctor
```

`doctor` checks Git, configuration, hooks and access to both repositories, then tells you what to fix.

## Simple by default

- no setup is needed for scanning;
- safe credential rules are built in;
- one clear command performs one clear action;
- interactive sessions explain the next step;
- advanced settings remain available when needed.

Most users only need:

```text
git-private2public           scan now
git-private2public init      prepare private → public publishing
git-private2public publish   publish the clean copy
git-private2public doctor    diagnose a problem
```

Everything else—custom rules, replacements, CI JSON, hooks, branches, tags, YAML compatibility and implementation details—is documented separately:

**[Advanced configuration](./docs/ADVANCED.md)**

## Safety

Publishing works in a temporary clone. The private repository is not rewritten. Concurrent public updates are protected with `--force-with-lease`, pushed commits are verified, and existing tags are never force-overwritten.

## License

MIT
