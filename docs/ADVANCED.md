# Advanced configuration

`git-private2public` reads either a `.gitpublic/` folder or one legacy YAML file.
The folder format is preferred because each file has one job.

## Mental model

Publishing is a pipeline:

```text
private repo
  -> temporary clone
  -> remove files from history       (.gitpublic/ignore)
  -> rewrite text in remaining files (.gitpublic/replace)
  -> scan final result               (.gitpublic/scan + .gitpublic/allow)
  -> push to public repo             (.gitpublic/config)
```

Missing custom files mean “no custom rules for that step”. Built-in credential detection remains active for zero-config scans and guard mode.

| File | If present | If missing or empty |
|------|------------|---------------------|
| `.gitpublic/config` | Defines source, target, push settings | `source`/`target` are required, so publish cannot run |
| `.gitpublic/ignore` | Removes matching files/paths from all published history | No files are removed |
| `.gitpublic/replace` | Rewrites matching text in remaining files | No text is rewritten |
| `.gitpublic/scan` | Adds custom blocking patterns | Built-in credential rules still protect zero-config scans and guard mode |
| `.gitpublic/allow` | Exceptions for domain-like matches found by `scan` | No scan exceptions exist |
| `.gitpublic/domains` | Same as `allow` alias | Nothing is allowlisted |

## `.gitpublic/config`

Example:

```ini
source = you/private-repo
target = you/public-repo
push_force = true
push_branches = main
push_tags = false
```

For a first public release, `snapshot` mode publishes only the sanitized
current tracked tree as one neutral root commit. It removes private commit
messages, authors, branches, tags, and previous history before filtering:

```ini
mode = snapshot
snapshot_include_source_sha = false
```

Set `snapshot_include_source_sha = true` only when intentionally exposing the
private source commit ID. Snapshot mode rejects `push_tags = true`. Normal
`history` mode remains the default.

`source` and `target` can be:

```text
you/repo                         # GitHub shorthand -> https://github.com/you/repo.git
https://github.com/you/repo.git  # full HTTPS URL
git@github.com:you/repo.git      # SSH URL
/path/to/local/repo              # local path
```

`push_force = true` uses `--force-with-lease`. That is intentional: public mirrors are often rewritten sanitized histories.

## `.gitpublic/ignore`

One rule per line, `#` comments allowed.

```gitignore
.env
secrets/
*.key
*.pem
private/*.json
```

What it does:

- exact file paths remove that file from published history;
- paths ending with `/` remove that directory/prefix;
- glob rules like `*.key` or `private/*.json` remove matching paths.

It removes files from Git history in the temporary sanitized clone. It does not edit your private repo.

## `.gitpublic/replace`

One replacement per line:

```text
old text ==> new text
private.company.local ==> example.com
regex:192\.168\.[0-9]+\.[0-9]+ ==> 203.0.113.10
glob:*.json:real-token ==> ***
```

Forms:

| Form | Meaning |
|------|---------|
| `old ==> new` | literal text replacement in all text blobs |
| `regex:pattern ==> replacement` | regex replacement in all text blobs |
| `glob:*.json:old ==> new` | replacement scoped to files matching that glob |

Whitespace around `==>` is ignored.

Use `replace` for private domains. Example:

```text
internal.company.local ==> example.com
regex:.*\.corp\.internal ==> example.com
```

## `.gitpublic/scan`

`.gitpublic/scan` adds custom blocking rules. Built-in credential rules are always available for zero-config scans and guard mode.

One rule per line:

```text
regex:github_pat_[A-Za-z0-9_]{30,}
regex:sk-[A-Za-z0-9_-]{20,}
regex:192\.168\.
regex:[a-z0-9.-]+\.[a-z]{2,}
```

Forms:

| Form | Meaning |
|------|---------|
| `literal text` | fail if exact text remains |
| `regex:pattern` | fail if regex matches |

If `.gitpublic/scan` is missing, no custom rules are added. Built-in credential detection still works in zero-config scans and guard mode.

## Domains and `.gitpublic/allow`

Deny-all-with-exceptions is opt-in. There is no automatic “block all domains” mode.

Domains are blocked only if you add a domain rule to `.gitpublic/scan`, for example:

```text
regex:[a-z0-9.-]+\.[a-z]{2,}
```

That rule means: “fail on any domain-looking string”.

Then `.gitpublic/allow` says which matched domains are okay:

```text
github.com
pypi.org
example.com
```

Important details:

- `allow` does not scan by itself; it only cancels a matching `scan` violation when the whole matched value exactly equals the allowed domain (case-insensitive);
- `allow` does not replace anything;
- `allow` only matters when a `scan` rule matched;
- `allow` is checked against the matched text itself, not nearby text;
- `domains` is just an alias file for `allow`.

### Domain examples

No `.gitpublic/scan`:

```text
README contains private.company.local
.gitpublic/allow contains nothing
```

Result: publish is not blocked, because no scan rule exists.

Broad domain scan, no allowlist:

```text
# scan
regex:[a-z0-9.-]+\.[a-z]{2,}
```

Result: every domain-looking string blocks: `github.com`, `example.com`, `private.company.local`, etc.

Broad domain scan with allowlist:

```text
# scan
regex:[a-z0-9.-]+\.[a-z]{2,}

# allow
github.com
example.com
```

Result:

- `github.com` passes;
- `example.com` passes;
- `private.company.local` blocks.

Private domain replacement + broad scan:

```text
# replace
private.company.local ==> example.com

# scan
regex:[a-z0-9.-]+\.[a-z]{2,}

# allow
github.com
example.com
```

Result:

- `private.company.local` is rewritten to `example.com`;
- `example.com` is allowed;
- publish passes.

## `scan` vs `replace` vs `allow`

| Goal | Use |
|------|-----|
| Delete files | `ignore` |
| Rewrite a private value | `replace` |
| Refuse publish if value survived | `scan` |
| Make an exception for a public domain matched by broad domain scan | `allow` |
| Auto-block every domain | add a broad domain regex to `scan` |
| Auto-block known credential formats | built in; no custom rule required |

## Hook mode

```bash
git-private2public hook enable
```

Installs a Git `pre-push` hook in the current private repo. On every `git push`, it runs:

```bash
git-private2public publish -c .gitpublic
```

Existing user hooks are preserved and chained. Guard and auto-publish can coexist in one managed dispatcher.

## YAML compatibility

Legacy YAML config still works:

```yaml
source: you/private-repo
target: you/public-repo
ignore:
  - .env
  - secrets/
replace:
  - private.company.local ==> example.com
fail_on_match:
  - regex:github_pat_[A-Za-z0-9_]{30,}
allow:
  - github.com
push:
  force: true
  branches: [main]
```

Folder mode is clearer and is recommended for real projects.
