# Import-path completion update

This follow-up adds the missing completion context inside `#:import` targets.
The previous update could resolve completed imports, but did not offer
suggestions while their dotted paths were being typed.

Examples (`|` marks the cursor):

| Typed text | Suggested item |
| --- | --- |
| `#:import Formatter ap|` | `app` |
| `#:import Formatter app.sha|` | `shared` |
| `#:import Formatter app.shared.fo|` | `formatter` |
| `#:import Formatter app.shared.formatter.For|` | `Formatter` |
| `#:import Widget kivy.ui|` | `uix` |
| `#:import Widget kivy.uix.wi|` | `widget` |
| `#:import Widget kivy.uix.widget.Wi|` | `Widget` |

Project suggestions depend on the modules present in the configured source
roots. Installed package suggestions use the environment selected by the
language server. Candidates use prefix matching; this feature does not
correct an already misspelled parent path.

Only the active path component is replaced, including any remaining letters
after the cursor in that component. The alias and surrounding path remain
intact. Completion supports namespace packages, `.pyi` files, stub packages,
module-level classes/functions/constants, and explicit static reexports.
Existing indexed Python overlays take priority over saved source.

The completion service lists immediate children and can read a selected
module's source without importing it. It does not recursively scan a package
for each completion request, execute application code, or change the shared
Python index. Static reexport lookup is bounded to prevent cycles or large
import trees from producing unbounded work. Wildcard and dynamically created
exports are not expanded. Compiled module names may be suggested, but their
members require available indexed metadata or Python stubs.

Tab-separated directives now parse their names and arguments consistently
with space-separated directives. Completion ignores comments, strings,
other directive types, and the alias portion of an import.

## Apply

Apply this after `kivy-lsp-update.zip`. From the `kivy-lsp` project root:

```sh
unzip -o ~/Downloads/kivy-lsp-import-completion.zip -d .
```

Restart Neovim so its language-server process reloads the source. No change
to the provided Neovim configuration is required. This assumes the editable
installation described previously; if necessary, establish it from that
same project root:

```sh
uv pip install --python .venv/bin/python -e .
```

The server already advertises `.` as a completion trigger. Open a KV buffer
and try the examples above with actual module paths in your project.

## Validation

The complete updated suite passes **257 tests**. The 38 added cases cover
directive contexts, component replacement, Unicode/CRLF positions, unsaved
Python changes, selected-environment discovery, namespaces, stubs, static
reexports, bounded reads, and actual stdio LSP completion requests. Source
passes Pyright with no errors or warnings and Ruff `E9,F` checks.

The ZIP contains only six new/changed runtime files, two new regression-test
files, and this document. It does not require applying another `.patch` file.
