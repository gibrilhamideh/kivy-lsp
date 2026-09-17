# Validation record

Date: 2026-09-17.

Baseline: the supplied `src(2).zip`, SHA-256
`3ecf09dbfe5e6942af62f9ac9e24b85185023a6e82402f5a26bc14d9a82bb928`.
The archive supplied source files but no original tests or package metadata.

## Results

| Check | Result |
| --- | --- |
| Added regression suite | **219 passed**, no failures or skipped cases in the final run. |
| Real Kivy formatter compatibility | 16 passing cases within the suite, including live binding/rebinding and event/conditional order. |
| Actual stdio LSP protocol | UTF-8 and UTF-16 runs passed; capabilities, formatting, hover spans, commands, and diagnostic addition/clearing checked. |
| Pyright on all source | 80 files analyzed, 0 errors, 0 warnings. |
| Ruff `E9,F` on source and tests | All checks passed. |
| Changed/new Python line width | All lines at most 79 characters. |
| Synthetic performance | See `performance.md`; ordinary KV edit invalidation was improved, and remaining costs are disclosed. |

The runtime was Python 3.12 on Linux. Validation dependencies were pygls
2.1.1, lsprotocol 2025.0.0, Kivy 2.3.1, pytest 9.1.1, Ruff 0.16.8, and
Pyright 1.1.414. These describe the validation environment, not a replacement
dependency policy for the original project.

Reproduce from the project root with those dependencies available:

```sh
PYTHONPATH=src python -m pytest -q
python -m pyright src
python -m ruff check --select E9,F src tests
```

The original source already requires Python 3.12 syntax. Kivy compatibility
tests use `pytest.importorskip` when Kivy is absent; install Kivy 2.3.1 to run
the complete compatibility suite. Test fixtures execute controlled code only,
not an uploaded application or its KV directives. In the final run Kivy was
installed and all 219 cases ran.

## Coverage by file

| File | Main purpose |
| --- | --- |
| `test_configuration.py` | Formatter settings, projections, environment/interpreter selection, stdlib paths, and host-package isolation. |
| `test_document_encoding.py` | UTF-8/16/32 positions, AST column overrides, CRLF, and file-URI conversion. |
| `test_parser_regressions.py` | Selectors, tabs, multiline grammar, tolerant diagnostics, and embedded Python mapping. |
| `test_python_regressions.py` | C3 inheritance, accessors, conditional declarations, unions, and generic substitution. |
| `test_semantic_regressions.py` | Operand inference, unpacking, lexical scopes, typed literals, constants, dynamic/Factory types, and translations. |
| `test_workspace.py` | Overlays, stale-error clearing, reload, external changes, includes, exclusions, stub fallback, and bounded KV invalidation. |
| `test_formatting.py` | Width boundaries, indentation exclusion, wrapping, controls, literals/comments, event statements, idempotence, CLI, and LSP adaptation. |
| `test_formatter_compatibility.py` | Official Kivy parser/compiler, watched chains, and headless live binding/event behavior. |
| `test_editor_features.py` | Hover, signatures, folding, selections, quick fixes, and versioned LSP edits. |
| `test_reference_safety.py` | Symbol identity, scope capture, Unicode, Python/KV access patterns, and rejection of unsafe renames. |
| `test_server_protocol.py` | Real subprocess JSON-RPC integration and negotiated encodings. |

## Scope of confidence

These checks support the implemented LSP behavior and controlled Kivy 2.3.1
formatting. They do not replace the unavailable repository tests, packaging
checks, editor integration, or Tree-sitter corpus/query tests. No Tree-sitter
source was supplied or modified. Other Kivy versions and platform-specific
editor behavior still need validation in the original projects.

Unsupported formatting regions are preserved and reported. Rename is limited
to the supported static identities and can refuse uncertain cases. Those
limits are documented in `implementation.md` and tested where they affect
safe edits.
