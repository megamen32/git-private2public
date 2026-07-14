# Changelog

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
