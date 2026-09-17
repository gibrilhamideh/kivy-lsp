# Kivy LSP diagnostic corrections

This archive contains only files changed since `kivy-lsp-values-cli.zip`.
Unpack it over the existing updated `kivy-lsp` repository:

```sh
unzip -o ~/Downloads/kivy-lsp-diagnostic-fixes.zip -d .
```

Restart the editor's Kivy language server after unpacking.

The update fixes reproduced checker defects involving Kivy property input
conversion, variable-length list shorthand, simple Python 3.12 `type` aliases,
and incomplete inherited-member information. The tests use controlled source
fixtures and actual Kivy properties; the user's application was not available
in this workspace, so the update does not claim to resolve every reported
application diagnostic.

Validation: **484 tests passed**, with no Pyright errors or warnings and no
Ruff `E9,F` findings. See
[docs/diagnostic-fixes-validation.md](docs/diagnostic-fixes-validation.md).

See [docs/diagnostic-troubleshooting.md](docs/diagnostic-troubleshooting.md)
for the distinction between checker limitations, missing global-import
configuration, and actionable code diagnostics.

## Select the updated terminal command

An editor may use the repository's virtual environment while the terminal
uses a separately installed global executable. Inspect the latter with:

```sh
type -a kivy-lsp
```

To point a uv-installed terminal tool at this checkout, run from the
`kivy-lsp` repository root:

```sh
uv tool install --force --editable .
hash -r
kivy-lsp check --help
```

Then run `kivy-lsp check` from the application project directory. Alternatively,
continue using the absolute path to this repository's `.venv/bin/kivy-lsp`.
The [command reference](docs/cli.md) includes this setup and the available
checking and formatting options.
