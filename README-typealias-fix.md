# Legacy TypeAlias resolution

This archive contains only files changed since `kivy-lsp-cli-colors.zip`.
From your updated `kivy-lsp` repository root:

```sh
unzip -o ~/Downloads/kivy-lsp-typealias-fix.zip -d .
```

An editable installation uses the update on the next CLI invocation.
Restart the Kivy language server in your editor to reload its Python index.
Then run `kivy-lsp check` from your application project root.

The reported keyboard API declares:

```python
from typing import TypeAlias

FnKeycode: TypeAlias = str | None
```

Therefore `key: FnKey | FnKeycode` accepts a `FnKey` instance, a string, or
`None`. The previous update supported the newer `type Alias = ...` syntax,
but did not mark this annotated assignment as an alias. It was consequently
checked as an unresolved named type, producing false argument errors.

This update indexes explicit legacy module aliases in the shared resolver.
Imported aliases expand recursively inside unions and container types.
Both the editor and CLI use that resolved type information. Valid keyboard
strings are accepted; incompatible inputs still produce diagnostics.

Keep the existing keyboard declarations and KV calls. The fix for these
six argument errors belongs to the LSP's alias indexing.

Validation: **542 tests passed**, including 47 new regression cases.
Pyright reported no errors or warnings; Ruff `E9,F` passed for source and
tests. The CLI regression reproduces the imported package structure,
checks editor/CLI agreement, accepts the six keyboard calls, and verifies
that an integer argument still produces `kv-argument-type`.
