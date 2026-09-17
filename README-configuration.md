# Standalone Kivy LSP configuration

This archive contains only files changed since `kivy-lsp-typealias-fix.zip`.
Apply it to the existing, updated `kivy-lsp` checkout. From that repository's
root directory:

```sh
unzip -o ~/Downloads/kivy-lsp-configuration.zip -d .
```

The editor, checker, and formatter now read only `kivy-lsp.toml` for Kivy LSP
settings. The old `[tool.kivy-lsp]` configuration is no longer loaded.
`pyproject.toml` remains available for Python packaging, other tools, and
project root detection.

## Move the application's configuration

Copy `examples/gabtech-pro/kivy-lsp.toml` into your **gabtech-pro project
root**, naming it `kivy-lsp.toml`. It preserves your supplied `app-class`,
Python/KV source paths, `Formatter`, `I18nRef`, and translation settings.
For example, replace the destination below with your application's path:

```sh
cp examples/gabtech-pro/kivy-lsp.toml /path/to/gabtech-pro/kivy-lsp.toml
```

If the destination already exists, merge the example with your other
settings. Move any additional Kivy LSP settings from `pyproject.toml` too,
removing the `tool.kivy-lsp` prefix from each table name. Root settings go
before table headers. Remove the old Kivy LSP sections from `pyproject.toml`;
keep its other settings.

Restart the language server in Neovim. Editable installations use the
changed source on the next CLI invocation. From your application root:

```sh
kivy-lsp check
kivy-lsp format --check .
```

Add `"kivy-lsp.toml"` to your editor's `root_markers` before
`"pyproject.toml"` and `".git"`. See [docs/config.md](docs/config.md) for all
configuration options, defaults, environment selection, and reload rules.

## Library exports

Referenced libraries can ship their own `kivy-lsp.toml` inside their
importable package. The analyzer combines their globals and generic
projections. Application entries override individual inherited entries;
unrelated library exports remain available. Conflicts produce configuration
issues until resolved. Library formatter settings, source paths, and
translation catalogs cannot change the application's settings.

The file must be included in the library's installed package. A repository
root config alone does not publish exports to consuming applications.
Metadata is discovered for referenced packages and their ancestors, read
statically, and cached without running library code.

This archive updates the LSP. To supply `md_icons` automatically, `kivyfn`
must also ship a declaration mapping that name to its actual Python export.
That export's source was not supplied, so no guessed path is included.
The guide explains the library file layout and package-data inclusion.

## Validation

- **601 tests passed**, including real stdio editor tests and CLI discovery.
- Pyright reported **0 errors and 0 warnings**; Ruff `E9,F` passed.
- Covered library merging, conflicts, app overrides, namespace packages,
  editable paths, watched repairs, provider removal, and i18n overlays.
- The 251-module import-completion benchmark performed **zero dependency
  source reads while typing**. Median update times were 0.49–1.63 ms and
  completion times 0.69–1.21 ms in this environment; local timings vary.

External library watcher paths are registered at server startup. Libraries
first discovered later still supply exports immediately; use reload for
subsequent external metadata edits, or restart to register the new paths.
The original application's full source and packaging were not included, so
run the checker again in its actual environment after applying the update.
