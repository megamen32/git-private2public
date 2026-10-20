# Changelog

## 0.2.4

- Scan preserved public release tags before publishing and retain only their redacted pre-existing findings.
- Independently reject any new scan findings after publication while keeping unchanged tag refs intact.

## 0.2.3

- Preserve existing public target tags unchanged during snapshot publication when explicitly enabled.
- Continue rejecting unconfigured refs and verify preserved tag object IDs after the branch push.
- Add current-tree scan path exemptions and prune unreachable pre-snapshot objects before the history scan.
- Keep history scanning complete because one blob can be reachable through both exempt and non-exempt paths.

## 0.2.2

- Fail closed with a concise redacted error when target-ref preflight times out.
- Prevent Git remote errors from echoing credential-bearing target URLs.

## 0.2.1

- Add `.gitpublic/public/` overlays for public-only files after sanitization.
- Keep snapshot publications at exactly one root commit after applying overlays.
- Reject overlay symlinks, special files, reserved paths, and destination escapes.
- Permit tracked `.gitpublic/scan` policy when every active rule is generic `regex:`.

## 0.2.0

- Add fail-closed scanning of every reachable blob, commit, and annotated tag.
- Apply built-in credential rules to every publish in addition to custom rules.
- Add one-commit `snapshot` mode for first public releases without private history.
- Require the public target to contain exactly the configured branches and tag set.
- Prevent pre-push publication from cloning a stale matching private remote.
- Remove all credential fragments from scan findings.
