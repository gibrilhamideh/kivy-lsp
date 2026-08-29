# kivy-lsp

An experimental language server for Kivy's KV language.

`kivy-lsp` understands the relationship between KV files and the Python
classes behind them. It provides completion, diagnostics, semantic
highlighting, hover information, and go-to-definition without importing or
executing the application.

This project is an initial preview. Please report false diagnostics and
completion gaps with a small Python and KV example.

## Features

### KV completion

- Root and child widget names.
- Python-backed and dynamic KV classes.
- Inherited and custom Kivy properties.
- `OptionProperty`, `Literal`, boolean, and nullable values.
- Names and deep member expressions such as
  `runtime.cycle.state.exists`.
- Methods and method arguments.
- Literal choices for annotated method arguments.
- Kivy IDs and the types of the widgets they reference.
- Instance-local properties declared directly in a KV widget body.
- Correct widget scope inside `canvas`, `canvas.before`, and
  `canvas.after` blocks.

### Python `ids` intelligence

The server can attach to Python files alongside Pyright and provide
Kivy-specific completion for:

```python
self.ids.
self.ids.toolbar
self.ids["toolbar"]
```

After an ID is selected, member completion uses the widget type declared in
KV. Go-to-definition on the ID navigates to its `id:` declaration.

Outside Kivy ID expressions, `kivy-lsp` returns no Python completion items, so
it can run beside a general Python language server.

### Diagnostics

- Invalid KV syntax and indentation.
- Unknown widgets, properties, members, and IDs.
- Invalid property values and incompatible expression types.
- Invalid `OptionProperty` and `Literal` values.
- Missing or incompatible method arguments.
- Missing commas between method arguments.
- Optional values used without a safe `None` guard.
- Duplicate and reserved KV IDs.
- Invalid translation keys and translation parameters.

### Navigation and hover

Go-to-definition resolves the exact identifier under the cursor:

- A KV widget name navigates to its Python class.
- A dynamic class navigates to its KV declaration.
- Properties, methods, events, and deep members navigate to Python.
- KV IDs and Python `self.ids` references navigate to KV.
- Translation keys navigate to their JSON catalog entries.
- Translation parameters navigate to their placeholders.

Hovering over a configured translation key displays its translated text and
required placeholders.

### Semantic highlighting

Semantic tokens distinguish widgets, classes, properties, methods, events,
IDs, variables, constants, keywords, and other KV symbols.

## LSP and Tree-sitter

The two projects are complementary:

| Project | Responsibility |
| --- | --- |
| `kivy-lsp` | Completion, diagnostics, types, navigation, hover, and semantic tokens |
| `tree-sitter-kivy` | Parsing, editor highlighting, indentation, folding, injections, and structural navigation |

The companion grammar is maintained in the separate
`tree-sitter-kivy` repository.

## Requirements

- Python 3.12 or newer.
- An editor with Language Server Protocol support.
- The analyzed project's dependencies available in its virtual environment.

The server first looks for `.venv` or `venv` in the project root. It indexes
Python source and stubs statically and does not import the application.

## Installation

After the package is published:

```bash
uv tool install kivy-lsp
```

Alternatively:

```bash
pipx install kivy-lsp
```

For development from a source checkout:

```bash
git https://github.com/gibrilhamideh/kivy-lsp
cd kivy-lsp
uv sync
uv run kivy-lsp
```


## Neovim

Register `.kv` as the `kivy` filetype and attach the server to both `kivy` and
`python` buffers. The Python filetype enables `self.ids` completion and
navigation.

### Installed command

With Neovim 0.11 or newer:

```lua
vim.filetype.add({
  extension = {
    kv = "kivy",
  },
})

vim.lsp.config("kivy_lsp", {
  cmd = { "kivy-lsp" },
  filetypes = {
    "kivy",
    "python",
  },
  root_markers = {
    "pyproject.toml",
    ".git",
  },
})

vim.lsp.enable("kivy_lsp")
```

### LazyVim development setup

This configuration uses local sibling checkouts of `kivy-lsp` and
`tree-sitter-kivy`:

```lua
local development_root = vim.fn.expand("~/Development/tree-sitter-kivy")
local ecosystem_root = development_root .. "/kivyfn-ecosystem"
local kivy_lsp_root = ecosystem_root .. "/kivy-lsp"
local kivy_tree_sitter_root =
  ecosystem_root .. "/tree-sitter-kivy"

return {
  {
    "neovim/nvim-lspconfig",

    init = function()
      vim.filetype.add({
        extension = {
          kv = "kivy",
        },
      })
    end,

    opts = {
      servers = {
        kivy_lsp = {
          mason = false,

          cmd = {
            kivy_lsp_root .. "/.venv/bin/kivy-lsp",
          },

          filetypes = {
            "kivy",
            "python",
          },

          root_markers = {
            "pyproject.toml",
            ".git",
          },
        },
      },
    },
  },

  {
    "nvim-treesitter/nvim-treesitter",

    init = function()
      vim.opt.runtimepath:prepend(kivy_tree_sitter_root)

      vim.api.nvim_create_autocmd("User", {
        pattern = "TSUpdate",

        callback = function()
          require("nvim-treesitter.parsers").kivy = {
            ---@diagnostic disable-next-line: missing-fields
            install_info = {
              path = kivy_tree_sitter_root,
              queries = "queries/kivy",
            },

            tier = 3,
          }
        end,
      })
    end,

    opts = function(_, opts)
      opts.ensure_installed = opts.ensure_installed or {}

      if not vim.tbl_contains(opts.ensure_installed, "kivy") then
        table.insert(opts.ensure_installed, "kivy")
      end
    end,
  },

  {
    "saghen/blink.cmp",
    optional = true,

    opts = {
      sources = {
        per_filetype = {
          kivy = {
            "lsp",
            "path",
            "buffer",
          },
        },
      },
    },
  },
}
```

Restart Neovim after changing the configuration. Use `:LspInfo` or
`:checkhealth vim.lsp` to verify that `kivy-lsp` is attached.

## Project configuration

Configuration is read from the analyzed project's `pyproject.toml`.
Relative paths are resolved from the directory containing that file.

```toml
[tool.kivy-lsp]
source-roots = ["src"]
kv-paths = ["src"]
app-class = "app.windows.primary.window.MainWindow"
excludes = [
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
]

[tool.kivy-lsp.globals]
runtime = "app.runtime.subscriber.runtime"

[tool.kivy-lsp.global-imports]
Formatter = "app.runtime.formatter.Formatter"

[tool.kivy-lsp.member-projections]
"example.state.StateWrapper" = 1

[tool.kivy-lsp.subscript-projections]
"example.store.TypedCollection" = 0

[tool.kivy-lsp.i18n]
source = "src/app/resources/i18n/en.json"
properties = [
    "i18n_key",
    "hint_i18n_key",
]
```

### Configuration reference

| Key | Purpose |
| --- | --- |
| `source-roots` | Python package roots to index. Defaults to `src` when it exists, otherwise the project root. |
| `kv-paths` | Files or directories containing KV files. Defaults to `source-roots`. |
| `app-class` | Qualified application class used to type the KV `app` binding. |
| `excludes` | File or directory patterns excluded from Python indexing. |
| `globals` | Qualified modules, classes, or values available in every KV scope. |
| `global-imports` | Project-wide equivalents of KV `#: import` declarations. |
| `member-projections` | Generic argument whose members are exposed by a wrapper type. |
| `subscript-projections` | Generic argument returned when a wrapper is indexed. |
| `i18n.source` | One canonical JSON translation catalog. |
| `i18n.properties` | KV properties that contain translation keys. |

Projection indexes are zero-based.

### Translation catalog

The first release supports one JSON language file. Nested objects are
flattened into dotted keys:

```json
{
  "features": {
    "ventilation": {
      "title": "Cycle ventilation",
      "navigator": "Stage {number} of {count}"
    }
  }
}
```

This catalog produces keys such as:

```kv
UIText:
    i18n_key: "features.ventilation.navigator"
    i18n_params: {"number": 1, "count": 3}
```

The server completes dotted keys and placeholder names, checks missing,
unknown, and duplicate parameters, shows translation hover text, and
navigates to the corresponding JSON key or placeholder.

## Troubleshooting

### The server does not attach

1. Confirm `:set filetype?` reports `kivy` in a KV buffer.
2. Run `kivy-lsp` from a terminal to confirm it is on `PATH`.
3. Run `:LspInfo` or `:checkhealth vim.lsp`.
4. Confirm the project contains `pyproject.toml` or `.git`.

### Project classes are missing

1. Confirm `source-roots` contains the Python package.
2. Confirm the project `.venv` contains Kivy and its dependencies.
3. Restart the server after changing `pyproject.toml`.

### Python `self.ids.name` is reported by Pyright

Kivy supports attribute-style ID access at runtime, but Python stubs must
model that dynamic behavior. The `Widget.ids` mapping should use a type with
`__getattr__`, for example:

```python
from typing import Any


class _IdsDict(dict[str, Any]):
    def __getattr__(self, name: str, /) -> Any: ...


class Widget:
    ids: _IdsDict
```

This affects Pyright diagnostics. Kivy ID completion and navigation are still
provided by `kivy-lsp`.

### Debug logs

In Neovim:

```lua
vim.lsp.log.set_level("debug")
```

Reproduce the problem and open `:LspLog`.

## Current limitations

- Formatting is not implemented.
- Rename, references, code actions, and document symbols are not implemented.
- Translation intelligence supports one JSON catalog.
- Highly dynamic Python or KV behavior may require explicit configuration.
- Tree-sitter must be installed separately for editor-native folding,
  indentation, injections, and Aerial symbols.

## Development

```bash
uv sync
uv run pyright
uv run ruff check .
uv run pytest -q
uv build
```

## Contributing

Bug reports should include:

- A minimal Python class or stub.
- The smallest KV example that reproduces the issue.
- The expected and actual completion, diagnostic, or navigation result.
- The editor and `kivy-lsp` version.

Pull requests should include regression tests for behavior changes.

## License

MIT

