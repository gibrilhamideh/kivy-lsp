# Command-line tools

Run commands from the application project's root directory. If `kivy-lsp`
is not on your PATH, use the absolute path to the executable inside the
Kivy LSP checkout's virtual environment.

```bash
kivy-lsp check
kivy-lsp format --check .
kivy-lsp format --diff .
kivy-lsp format --in-place .
```

Running `kivy-lsp` without a subcommand starts the language server over
standard input/output, as before. Existing language-server transport flags
remain supported. Top-level help lists both commands. Detailed help is
available with:

```bash
kivy-lsp --help
kivy-lsp check --help
kivy-lsp format --help
```

## Use the same source checkout from the terminal

An editor can use a repository's virtual-environment executable while the
shell finds an older global command. If `check` is rejected and the help
only lists server transport options, inspect the shell command:

```bash
type -a kivy-lsp
```

Run the executable inside the updated repository's `.venv/bin` directory
directly, or install that checkout as an editable uv tool. For example, after
setting `lsp_root` to the absolute repository path:

```bash
uv tool install --force --editable "$lsp_root"
hash -r
kivy-lsp check --help
```

The editable tool uses subsequent source changes from that checkout.
`--force` replaces the existing tool environment and its command entry point.
See the [uv tool installation reference](https://docs.astral.sh/uv/reference/cli/#uv-tool-install).
Run `check` from the application project whose KV files should be checked.

## Check saved code

```bash
# Check all configured KV paths in the current project.
kivy-lsp check

# Limit reported KV diagnostics to a directory or individual files.
kivy-lsp check app/views
kivy-lsp check app/views/panel.kv app/views/runtime.kv

# Produce a JSON report for tooling.
kivy-lsp check --output-format json

# Fail automated checks on warnings as well as errors.
kivy-lsp check --warnings-as-errors
```

Text output uses terminal colors automatically: errors are red, warnings
yellow, information and hints cyan, and zero error/warning totals green.
File locations and diagnostic messages retain their existing text format.
Control coloring explicitly with:

```bash
kivy-lsp check --color auto
kivy-lsp check --color always
kivy-lsp check --color never
```

The default `auto` mode colors each output stream only when it is a terminal.
Pipes and redirected files receive plain text. A nonempty `NO_COLOR` variable
or `TERM=dumb` also disables automatic colors. Explicit `--color always`
overrides these checks; `--color never` always disables colors. JSON output
never includes color escapes, even with `--color always`.

`check` reads saved files and never modifies them. It uses the same parser,
semantic analysis, property checks, translation checks, and include checks
as the editor. Files do not need to be opened in Neovim. Unsaved editor
changes are not visible to the command.

Project discovery searches upward for `kivy-lsp.toml`, stopping at the
nearest Git boundary. Without a dedicated config, the nearest
`pyproject.toml` or Git marker is a fallback root marker; settings in
`pyproject.toml` are never loaded. Without any markers, the input directory
is used. With no paths, discovery starts from the current directory and
checks the project's configured `kv-paths`. Explicit paths may cover
several projects.
Each project is initialized once, with its surrounding Python sources,
KV declarations, includes, dependencies, configuration, and exclusions.
An explicit KV file or directory outside `kv-paths` extends KV discovery
for that run. Selecting a path does not discard the surrounding type context.

Python files supply class, property, and type information. This command is
not a general Python type checker. Python syntax errors encountered while
indexing project sources or dependencies are reported even when they are
outside a selected KV directory: those errors affect the available context.
Explicit `.py`/`.pyi` inputs are accepted for this indexing context; the
`checked_files` count counts KV files only.

Text diagnostics have this shape:

```text
app/views/panel.kv:18:13: error: ... [kv-incompatible-property-value]
Checked 46 KV files: 1 error, 0 warnings
```

Paths inside the current working directory are relative; other paths are
absolute. Line and column numbers are one-based. Columns count Unicode
characters, not UTF-8 bytes or UTF-16 code units. Output is sorted by file,
position, and diagnostic code. Operational problems are written to stderr
and the text summary says `incomplete`.

JSON output contains `schema_version`, `projects`, `files`, `diagnostics`,
`issues`, and `summary`. Each diagnostic has `path`, `line`, `column`,
`end_line`, `end_column`, `severity`, `code`, and `message`; end positions
are exclusive. `issues` describe read, discovery, environment, or indexing
failures. `summary.complete` is false when those problems prevent a complete
check. The JSON document is the only stdout output. Invalid command-line
arguments use the usual argparse usage message on stderr.

| Exit code | Meaning |
| --- | --- |
| `0` | Completed with no errors; warnings alone are allowed by default. |
| `1` | Errors found, or warnings with `--warnings-as-errors`. |
| `2` | Invalid arguments/configuration or an incomplete check. |

Unavailable dependency sources/stubs, invalid environment selection, and
unreadable sources produce an incomplete check. A Python syntax diagnostic
is a code error (`1`), not an operational failure (`2`). Unresolved runtime
include paths remain the same warnings shown in the editor; the checker
does not execute the application to discover runtime resource paths.

## Format KV code

```bash
# Print a formatted file to stdout without modifying it.
kivy-lsp format app/views/panel.kv

# Rewrite one file, several files, or a directory recursively.
kivy-lsp format --in-place app/views/panel.kv
kivy-lsp format --in-place .

# Check whether saved formatting would change, without writing.
kivy-lsp format --check .

# Print a unified diff without writing.
kivy-lsp format --diff .

# Override the configured width for this invocation.
kivy-lsp format --in-place --line-length 79 app/views

# Read stdin; the filename supplies the configuration location.
cat app/views/panel.kv | kivy-lsp format --stdin-filename app/views/panel.kv -
```

Without paths, `format` reads stdin. Use `.` explicitly to process the
current directory. Multiple files require `--in-place`, `--check`, or
`--diff`; these modes are mutually exclusive. Stdin (`-`) must be the only
input and cannot be used with `--in-place`. Directory traversal selects
`.kv` files and prunes excluded paths. Explicit excluded files are skipped.

The formatter uses the same formatting engine as LSP formatting. It keeps
short property values on one line. Multiline values put `property:` on its
own line and use explicit backslashes with aligned continuation lines.
Leading indentation does **not** count toward the width; content spaces,
punctuation, and continuation backslashes do count. Necessary expression
grouping and reactive attribute chains are preserved.

Unsupported or unsafe regions are preserved and reported on stderr.
`# fmt: off`, `# fmt: on`, and `# fmt: skip` can preserve intentional
formatting. Formatting reports are not code diagnostics: run `check` to
check the code itself.

| Exit code | Meaning |
| --- | --- |
| `0` | Successful output/write; or no changes needed in check/diff mode. |
| `1` | `--check` or `--diff` found formatting changes. |
| `2` | Invalid arguments/configuration or a read/write/discovery error. |

Preserved unsupported regions and unbreakable lines can produce formatter
messages without exit code `2`. A successful formatting check therefore
means no safe formatter changes were found, not that every region was
formatted or that the KV code is semantically valid.

## Project configuration

Both tools read `kivy-lsp.toml` and share the editor's configuration.

```toml
source-roots = ["src"]
kv-paths = ["src"]
python-environment = ".venv"

[format]
line-length = 79

[diagnostics]
strict = false
```

Set paths to match your layout; projects without a `src` directory normally
use `source-roots = ["."]` and `kv-paths = ["."]`. Paths in configuration
are relative to the project root. Environment selection is optional; you
may set `python-interpreter` instead of `python-environment`, but not both.
Choose the application's environment when the LSP executable lives in a
separate development environment. Application modules are statically read,
not imported or executed.

The default diagnostic policy reports definite option-value mismatches and
allows general strings when their runtime value is unknown. `strict = true`
enables additional warnings for option assignments that cannot be proven
safe. It applies to both editor diagnostics and `check`.

`excludes` accepts the same patterns used for editor discovery. Simple
patterns match path components; patterns containing `/` match relative
paths. Configured source/KV roots determine discovery-relative paths.
Formatting also recognizes patterns relative to the project and configured
roots when a nested directory is selected. Excluded paths explicitly
included by KV directives can still be loaded for editor-equivalent context.

Setting `excludes` replaces the default list; retain the usual environment
and cache directories when adding your own entries:

```toml
excludes = [
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".venv", "__pycache__", "build", "dist", "venv", "generated",
]
```

Place root-level settings before any TOML table headers. The checker also
honors `app-class`, globals, global imports, generic projections, and
translation configuration. See [config.md](config.md) for every option,
migration instructions, and library-provided globals.
