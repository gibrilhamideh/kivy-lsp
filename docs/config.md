# Configuring Kivy LSP

Create `kivy-lsp.toml` at the application project root. This is the only
configuration source for the editor, `kivy-lsp check`, and `kivy-lsp format`.
Settings in `pyproject.toml` are not read or merged. Python packaging and
other tools may continue using that file.

Top-level settings appear before any TOML table headers. Do not wrap the
file in `[tool.kivy-lsp]`; the loader reports that old layout as an error.
Paths are relative to the directory containing the application config,
unless absolute paths are supplied.

## Application example

The supplied `examples/gabtech-pro/kivy-lsp.toml` preserves the settings
from the existing application configuration:

```toml
app-class = "app.windows.primary.window.MainWindow"
source-roots = ["src"]
kv-paths = ["src"]

[globals]
Formatter = "app.shared.formatter.Formatter"
I18nRef = "kivyfn.i18n.ref.I18nRef"

[i18n]
source = "src/app/resources/locales/en.json"
properties = ["i18n_key", "hint_i18n_key"]
```

Move the Kivy LSP sections out of `pyproject.toml`, remove their
`tool.kivy-lsp` prefixes, and save them in the new file. Preserve unrelated
build, dependency, and other tool settings in `pyproject.toml`.

## Project options

| Key | Meaning | Default |
| --- | --- | --- |
| `app-class` | Fully qualified Python class used to resolve `app`. | Unspecified |
| `source-roots` | Nonempty array of Python source directories. | `["src"]` when that directory exists; otherwise `["."]` |
| `kv-paths` | Nonempty array of KV source paths. | Same as `source-roots` |
| `excludes` | Discovery patterns for paths and directory names. | Environment, cache, VCS, and build directories listed below |
| `python-environment` | Path to the application's virtual environment, containing `pyvenv.cfg`. | Automatic discovery |
| `python-interpreter` | Path to the application's Python executable. | Automatic discovery |

Choose at most one of `python-environment` and `python-interpreter`.
Explicit selection is useful when the LSP runs in its own development
environment. With automatic selection, the LSP looks for the project's
`.venv` or `venv`, then its running virtual environment. It reads linked
source paths from simple `.pth` and `.egg-link` files without executing
their import statements.

```toml
source-roots = ["src"]
kv-paths = ["src"]
python-environment = ".venv"
```

The LSP reads application and dependency Python source statically. Selecting
an interpreter may run an isolated standard-library path probe; it does not
import the application to discover its types or globals.

## Global names

Both tables map a KV name to a fully qualified Python target:

```toml
[globals]
Formatter = "app.shared.formatter.Formatter"

[global-imports]
I18nRef = "kivyfn.i18n.ref.I18nRef"
```

Targets may be classes, variables, or modules. Both tables request indexing
of the relevant dependency. If the same name appears in both application
tables, `[globals]` wins. Use one table per name to keep intent clear.

These declarations tell the analyzer about names the runtime already
provides. They do not register globals with Kivy. A per-file `#:import` or
`#:set` declaration takes precedence over application and library globals.

Names present only in application configuration are added to inherited
library names. Declaring an inherited name overrides that name alone;
other library globals remain available.

## Translation checking

```toml
[i18n]
source = "src/app/resources/locales/en.json"
properties = ["i18n_key", "hint_i18n_key"]
```

`source` is required when `[i18n]` is present and selects the canonical JSON
translation catalog. `properties` lists KV property names to check, with
the two values above as the default. These settings continue to power
translation diagnostics and editor assistance. Omitting `[i18n]` disables
catalog-specific checking.

## Formatting

```toml
[format]
line-length = 79
```

`line-length` must be an integer of at least 4. The KV formatter measures
characters after leading indentation; internal spaces, punctuation, and a
trailing continuation backslash count toward the limit. Short expressions
stay on one line. Multiline values start below a property-only header and
use explicit backslash continuations with aligned expression lines.

CLI `--line-length` overrides the file setting for that invocation.
See [cli.md](cli.md) for formatting commands and preservation behavior.

## Diagnostic policy

```toml
[diagnostics]
strict = false
```

Definite invalid values remain errors. `strict = true` additionally enables
warnings for property values whose compatibility cannot be proved, such as
a broad string assigned to a finite option property. The setting applies
to both the editor and `check`. `--warnings-as-errors` controls the check
command's exit status without changing diagnostic severity.

## Generic projections

Mappings select a zero-based generic type argument that supplies either
members or subscript results. For example, with hypothetical wrapper types:

```toml
[member-projections]
"example.State" = 0

[subscript-projections]
"example.Collection" = 0
```

`State[User]` exposes members of `User`; indexing `Collection[User]` produces
a `User`. Use the actual qualified names of your wrapper classes. The
resolver checks qualified mappings first and supports short-name mappings
as a fallback. Values must be non-negative integers, excluding booleans.

## Exclusions

Simple patterns match path components; patterns containing `/` match
relative paths. Setting `excludes` replaces the defaults, so retain the
usual entries when extending them:

```toml
excludes = [
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".venv", "__pycache__", "build", "dist", "venv", "generated",
]
```

Put this root-level setting before any `[table]` headers. Explicit KV
includes can still load excluded files needed for analysis context.

## Library-provided configuration

A library ships `kivy-lsp.toml` inside its importable Python package. For
example, a package named `kivyfn` can ship:

`src/kivyfn/kivy-lsp.toml`

A library exposed under a shared namespace can ship the file in its own
subpackage, such as `src/kivyfn/store/kivy-lsp.toml`. These paths use Python
import names, which can differ from distribution names such as
`kivyfn-store`.

Only these tables are inherited by applications:

- `[globals]`
- `[global-imports]`
- `[member-projections]`
- `[subscript-projections]`

For example, a library that registers `I18nRef` can declare:

```toml
[globals]
I18nRef = "kivyfn.i18n.ref.I18nRef"
```

For `md_icons`, declare the exact module and symbol that the library exports.
The supplied source does not establish that path, so this update does not
invent an import or add a hardcoded `kivyfn` global. Add the declaration
to the library metadata once its real target is known.

Project paths, `app-class`, interpreter selection, translations, formatting,
exclusions, and strictness are owned by the application. A library's values
for those settings are not inherited.

The LSP discovers metadata for referenced packages and their package
ancestors in the selected environment. It reads static files, including
linked source packages, without executing dependency code. A library should
declare globals guaranteed by its normal KV initialization; optional runtime
registrations need explicit application configuration when not guaranteed.

Merge precedence is per name:

1. Explicit KV declarations override configured global names.
2. Application declarations override library exports.
3. Library exports are combined; identical targets are deduplicated.

Conflicting library targets or projection values produce a configuration
issue unless an application entry resolves the conflict. The check command
returns exit code `2` for incomplete checking. The editor publishes library
configuration diagnostics on the affected metadata file. Other unaffected
exports remain usable.

### Include the file in the wheel

The metadata file must actually be included in the installed package.
For a setuptools-built `kivyfn` package, this packaging declaration in
`pyproject.toml` includes the file:

```toml
[tool.setuptools.package-data]
kivyfn = ["kivy-lsp.toml"]
```

For a nested package, use its actual qualified name, such as
`"kivyfn.store"`. With a different build backend, use its package-data
inclusion mechanism. This is packaging configuration; all Kivy LSP settings
remain in `kivy-lsp.toml`.

See the [setuptools package-data reference](https://setuptools.pypa.io/en/stable/userguide/datafiles.html#package-data).

## Discovery and reload

CLI commands and server initialization search upward for the nearest
`kivy-lsp.toml`, stopping at the nearest Git repository boundary. If none is
found, the nearest `pyproject.toml` or Git marker identifies a project root;
its Kivy LSP settings are still ignored. With no markers, the starting
directory is used. Missing configuration uses built-in defaults plus
discovered library exports for analysis.

Save configuration before reloading. Watched changes to the application or
discovered library metadata refresh the project. For external library
locations, automatic notifications depend on client watcher support.
External watch paths are registered when the server starts. If an edit
introduces a previously unused library, its exports are loaded immediately;
later edits to that library's external metadata require
`kivy-lsp.reloadProject`, or a restart to register its new watch path.
Each CLI run loads saved configuration anew. Formatting uses only
application settings and does not scan dependencies.

Include the dedicated filename in your Neovim server's root markers:

```lua
root_markers = {
  "kivy-lsp.toml",
  "pyproject.toml",
  ".git",
},
```

`kivy-lsp.projectStatus` reports the selected environment and configuration
issues. Configuration files can be edited normally; no Kivy app startup is
needed to apply them to the analyzer.
