# Kivy LSP values and CLI update

This archive contains only changed or new files relative to the previous
`kivy-lsp-import-performance.zip` update. Unpack it into the existing
`kivy-lsp` repository root after applying the earlier updates:

```sh
unzip -o ~/Downloads/kivy-lsp-values-cli.zip -d .
```

Restart Neovim after unpacking. The existing editable installation uses the
updated source. This update adds no third-party dependencies and makes no
Tree-sitter changes.

## Included changes

- Completion for `OptionProperty` and `Literal` values in equality and
  inequality comparisons, including reversed operands and quoted values.
- Fixed option validation that previously skipped known invalid literals.
- Conservative default diagnostics: general string bindings stay quiet;
  uncertain property compatibility warnings require strict checking.
- Warnings for comparisons whose fully known value sets cannot match.
- A project checker that shares the editor's analysis engine and reports
  file locations, diagnostic codes, summaries, and incomplete scans.
- A developer command reference at [docs/cli.md](docs/cli.md), plus examples
  and checking policy at [docs/value-checking.md](docs/value-checking.md).

## Commands from your application project

Use the `kivy-lsp` executable from the environment where the updated server
is installed:

```sh
kivy-lsp check
kivy-lsp check app/views
kivy-lsp check --output-format json
kivy-lsp format --check .
kivy-lsp format --diff .
kivy-lsp format --in-place .
```

When working inside the `kivy-lsp` repository with its virtual environment,
use `.venv/bin/kivy-lsp` if the command is not on your PATH. You can also use
`.venv/bin/python -m kivy_lsp` with the same arguments.

The `check` command reads saved source files. The editor continues to analyze
unsaved buffer contents. Python declarations provide context for KV analysis;
general Python type checking remains the responsibility of your Python
checker.

To enable warnings about possibly incompatible property values, add this to
the application's `kivy-lsp.toml`:

```toml
[diagnostics]
strict = true
```

Leave this setting absent or `false` for conservative checking. Formatter
configuration and the indentation-excluding width calculation are unchanged.
