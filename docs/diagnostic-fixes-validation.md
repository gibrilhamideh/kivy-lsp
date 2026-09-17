# Diagnostic fixes validation

Validated against the cumulative source after `kivy-lsp-values-cli.zip`.

- Full test suite: **484 passed**, including 85 new regression cases.
- Pyright: **0 errors, 0 warnings** across the source package.
- Ruff `E9,F`: passed for source and tests.
- New and changed Python lines: at most 79 characters.

The property tests compare controlled `EventDispatcher` assignments with
Kivy 2.3.1 behavior, including tuple inputs, shorthand list expansion,
numeric units, bounds, and valid versus invalid sequence lengths. Separate
regressions cover Python 3.12 aliases, uncertain alias targets, inheritance
completeness, and index refresh. Existing completion, formatting, CLI, and
stdio protocol tests remain in the full suite.

Reproduction commands from the repository root, in an environment with the
development dependencies installed:

```sh
KIVY_NO_ARGS=1 KIVY_NO_CONSOLELOG=1 python -m pytest -q
python -m pyright src
python -m ruff check --select E9,F src tests
```

The report supplied by the user was used to identify failure patterns.
Their application source was not present for this validation, so neither
the remaining diagnostic count nor every app-specific cause is established.
