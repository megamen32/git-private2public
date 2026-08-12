# Verification notes

These notes separate repository evidence from product intent. They are kept
short so the README can lead with the user outcome.

## Checked in

- `tests/test_git_private2public.py` covers folder/YAML configuration, ignore
  and replace rule translation, allowlisted domains, version consistency, and
  guard detection of provider-style tokens.
- `pyproject.toml` preserves the supported install paths shown in the README:
  PyPI installation and local `python3 -m pip install .`.

## Deliberate limits

`publish` rewrites a temporary clone and pushes only after its configured scan;
it is not a claim that the source repository or its prior public copies are
secret-free. The guard catches its documented patterns and custom rules, but
cannot identify every possible credential or undo a credential exposure.
