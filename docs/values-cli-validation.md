# Values and CLI update validation

Date: 2026-09-17.

This update builds on the previously delivered
`kivy-lsp-import-performance.zip`. The new archive contains only files
changed or added since that update.

## Final checks

| Check | Result |
| --- | --- |
| Complete regression suite | 399 passed, no failures or skips |
| Pyright on all source files | 85 files; 0 errors and 0 warnings |
| Ruff E9/F on source and tests | Passed |
| Changed/new Python line width | At most 79 characters |
| Existing formatter and import regressions | Passed within the complete suite |
| Real stdio LSP value integration | Passed, including UTF-16 completion edits and configuration reload |

The validation environment used Python 3.12, Kivy 2.3.1, pygls 2.1.1,
lsprotocol 2025.0.0, pytest 9.1.1, Pyright 1.1.414, and Ruff 0.16.8.
These are validation versions, not changes to package dependencies.

Run the checks from the repository root with those development dependencies
available in the selected Python environment:

```sh
PYTHONPATH=src python -m pytest -q
python -m pyright src
python -m ruff check --select E9,F src tests
```

## Added coverage

- Actual indexed `OptionProperty` values and invalid literal diagnostics.
- General strings, unknown types, optional and imported `Literal` aliases,
  incomplete option lists, conditional/reassigned constants, and strict mode.
- Union owners with different finite domains or unrestricted members, plus
  numeric unary expressions and Python equality semantics.
- Equality/inequality completion in both directions, incomplete expressions,
  prefixes, quotes, replacement suffixes, Unicode, and explicit multiline KV.
- Prevention of enclosing-property suggestions leaking into unrelated
  strings and expressions.
- CLI text/JSON reports, project context, explicit targets, exclusions,
  Unicode locations, exit codes, indexing failures, and unreadable sources.
- Existing formatter CLI and server startup behavior, including command help.
- Real LSP completion edits, option error publication/clearing, quiet string
  bindings, and strict configuration reload with unsaved editor contents.

The source upload did not include the original repository's package metadata,
original tests, editor configuration files, or Tree-sitter source. Those
external integration and release checks remain outside this validation.
