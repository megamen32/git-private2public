# Changelog

## 0.2.0

- Add fail-closed scanning of every reachable blob, commit, and annotated tag.
- Apply built-in credential rules to every publish in addition to custom rules.
- Add one-commit `snapshot` mode for first public releases without private history.
- Require the public target to contain exactly the configured branches and tag set.
- Prevent pre-push publication from cloning a stale matching private remote.
- Remove all credential fragments from scan findings.
