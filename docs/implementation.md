# Kivy LSP update

This update implements the LSP work from the approved plan against the
uploaded `src(2).zip`. The existing architecture remains: LSP adapters call
workspace services, the workspace owns source snapshots and indexes, and
analysis consumes shared syntax and symbol models. Formatting is a separate
text-to-text service and does not initialize a workspace or run an app.

The source-only upload did not contain package metadata, the original test
suite, editor integration, or `tree-sitter-kivy`. This package therefore
supplies source changes and focused tests, rather than claiming a complete
release of either repository. Tree-sitter changes remain unverified; the LSP
does not need Tree-sitter at runtime.

## Formatter configuration

Add this to the project's `kivy-lsp.toml`:

```toml
[format]
line-length = 79
```

The default is 79. The value must be an integer of at least 4. It measures
Unicode characters after leading indentation, not bytes or display columns.
Internal spaces, punctuation, and the trailing space plus backslash count.
For example, 16 indentation spaces plus 79 content characters is permitted.

Short properties stay on one line. If wrapping is needed, the property name
and colon occupy their own line and the complete value starts one structural
level below. All physical continuation lines have that same indentation.
The formatter emits explicit backslashes and does not introduce redundant
outer parentheses. Necessary grouping, tuple commas, and call/container
delimiters are preserved.

```kv
<MyWidget>:
    text_color:
        app.theme.color.error if root.active else \
        app.theme.color.primary if root.enabled else \
        app.theme.color.with_tone(app.theme.color.onBackground, 45)
```

Long conditions can break before `and`/`or`. Conditional alternatives break
after `else`. Calls, lists, dictionaries, tuples, and sets expand at safe
boundaries, keeping one argument, entry, or item per line when expansion is
needed. Nested content stays compact when it fits and uses the same KV
continuation indentation when it does not:

```kv
<MyWidget>:
    options:
        { \
        "minimum_temperature": root.minimum_temperature, \
        "maximum_temperature": root.maximum_temperature, \
        "fallback_color": app.theme.color.with_tone( \
        app.theme.color.onBackground, \
        root.inactive_tone, \
        ), \
        }
```

No continuation starts with `property: expression \\` on the property header.
Dotted chains remain contiguous so formatting does not disrupt Kivy's
binding discovery. An indivisible token or chain can exceed the limit; the
formatter reports this instead of changing its meaning.

The initial emitted structural indentation is four spaces. If an unsupported
or protected region requires keeping existing indentation, the formatter
keeps that document's original structural indentation to avoid changing only
part of its hierarchy. This is deliberate preservation, not another style
option.

### Preservation and controls

Standalone comments support:

```kv
# fmt: off
# This region retains its current layout.
# fmt: on
# fmt: skip
<KeepThisConstruct>:
    text: "This complete construct is skipped."
```

The formatter preserves literal spelling, ordering, and statement boundaries.
It does not join independent event-handler statements with backslashes.
Comment-bearing expressions, multiline literals, complex event suites,
incomplete expressions, and unsupported long comprehensions/lambdas remain
unchanged with a skip explanation. Independent safe regions can still be
formatted. Uniform LF or CRLF style and final-newline presence are retained.

### Command line

These commands work with the updated source on the Python import path or in
an existing installation using that source:

```sh
python -m kivy_lsp format ui/main.kv
python -m kivy_lsp format ui/ --check
python -m kivy_lsp format ui/ --diff
python -m kivy_lsp format ui/ --in-place
python -m kivy_lsp format ui/main.kv --line-length 100
python -m kivy_lsp format - --stdin-filename ui/main.kv
```

The default writes one input's formatted text to stdout. Multiple files or a
directory require `--check`, `--diff`, or `--in-place`. Directory discovery
respects configured exclusions. Stdin uses `--stdin-filename` to discover
project configuration; an explicit `--line-length` takes precedence.

Exit codes: 0 for success/no required changes, 1 when check/diff finds changes,
and 2 for configuration, argument, I/O, or decoding errors. A safely skipped
region is reported on stderr but is not an exit-code-2 failure; check mode
does not promise every oversized or unsupported region was reformatted.

Editor whole-document formatting uses the same engine and project setting.
Use the editor's normal format command or format-on-save setting. Formatter
limitations appear in the language-server log. Range and on-type formatting
remain later work.

## Project checking and value suggestions

`kivy-lsp check` analyzes saved project files using the same workspace and
diagnostic engine as the language server. It accepts file/directory filters,
text or JSON output, and an optional failure policy for warnings. See
[cli.md](cli.md) for the complete check and format command reference.

Equality and inequality completion use the opposite operand's resolved
`OptionProperty` values or `Literal` type. Known invalid option assignments
are errors, while ordinary string bindings remain quiet by default. Optional
strict checking enables warnings for possibly incompatible values. See
[value-checking.md](value-checking.md) for examples and configuration.

## Editor features

| Capability | Implemented scope |
| --- | --- |
| General hover | Resolved types, method signatures/docs, property metadata, IDs and globals; translation hover retained. |
| Signature help | Active positional/keyword arguments, nested calls, multiline calls, and incomplete calls. |
| Folding | Rules, widget bodies, canvas blocks, and multiline values. |
| Selection expansion | Enclosing identifier/expression, property, widget/rule, and document ranges. |
| Quick fixes | Valid `OptionProperty` alternatives for an invalid literal, with bounded, versioned edits. |
| Find references | Resolved KV IDs and dynamic KV classes, including supported static Python ID accesses. |
| Rename | The same supported identities, with collision checks and versioned document edits. |

Rename is intentionally conservative. Ambiguous ownership, duplicate/shared
rules, computed or escaped ID keys, unsupported accesses, and dynamic class
lookups through arbitrary expressions/strings can prevent it. Inherited
Python ID access without a uniquely established owner blocks rename. A
refusal explains why; unsupported Python property/method renames are not
advertised as complete. References can show known uses even when rename is
blocked. Reflective runtime behavior is outside this static model.

Existing completion, definitions, document symbols, semantic tokens, and
translation assistance remain registered. Definition navigation also resolves
supported static `#:include` paths. Client-specific menus/settings still need
integration in the original editor project.

## Correctness and shared analysis

- Dynamic KV classes preserve declaration identity, all bases, and local
  members. Shared resolution serves completion, definitions, and diagnostics;
  Factory aliases are resolved through the same path.
- Python inheritance uses cached C3 ordering. Property setters/deleters retain
  getter metadata. Union members and method returns combine branch types;
  generic substitution survives optional and inherited types.
- Boolean `and`/`or` inference models operand results. Starred sequence lengths
  remain conservative when unknown. Lambda/comprehension names obey lexical
  scope. Typed literals distinguish `True`, `1`, and `1.0`.
- `#:set` receives static bindings. Module-level conditional Python imports
  and declarations are indexed without executing conditions. Conflicting
  conditional definitions remain approximate.
- `<-Button>`, `<.warning>`, tabs, and supported multiline forms have parser
  regressions. Invalid inline multiline property headers and non-flat KV
  continuations receive diagnostics while recovery remains available.
- Embedded Python uses a shared normalized-source mapper that translates
  Python AST UTF-8 columns back to KV source spans. LSP positions honor the
  client's negotiated encoding.
- Escaped JSON translation placeholders retain decoded-to-source spans.
  Qualified projection configuration matches exact qualified names;
  explicitly unqualified rules retain basename matching.

## Workspace behavior

Changed KV text is parsed once and that parse supplies the KV index. Python,
KV, and translation overlays remain authoritative over disk changes. Changes
rebuild affected open KV analysis conservatively and publish both new and
cleared diagnostics. Invalid Python buffers keep the last valid index until
a valid edit or overlay removal; closing restores disk state or removes a
deleted source.

Watched changes and LSP file create/delete/rename notifications refresh saved
sources. Python and KV discovery share exclusions and prune matching
directories. Supported includes are tracked with cycle protection, quoted
path handling, diagnostics, and source-relative/project-root resolution.
Runtime resource paths are not executed or guessed.

Use these `workspace/executeCommand` names:

- `kivy-lsp.reloadProject`: reload configuration, environment, dependencies,
  and sources while retaining unsaved overlays.
- `kivy-lsp.projectStatus`: report roots, selected environment/interpreter,
  Python version, module/document counts, loaded stubs, and indexing issues.

Clients supporting dynamic file-watch registration receive a project watcher.
Other clients can send file notifications themselves or invoke reload.
Project configuration remains in `kivy-lsp.toml`; the configuration-change
notification reloads that file.

Choose either selector, never both:

```toml
python-environment = ".venv"
# Alternatively: python-interpreter = ".venv/bin/python"
```

Selectors are relative to the project root or absolute. Automatic discovery
remains available when neither is set. Explicit selection avoids accidentally
using host third-party packages. Interpreter inspection uses isolated stdlib
queries; it does not import the application or execute `.pth` import lines.
Dependency refresh covers new Kivy-family Python imports and explicit KV or
configured imports. Referenced Python packages and their ancestors are also
checked for packaged `kivy-lsp.toml` exports. This metadata is read statically
and cached; declared global targets and projections seed dependency indexing.
Other arbitrary Python imports retain the existing selective indexing policy.
Application settings override library exports per name. See `config.md`
for the complete schema, conflict behavior, packaging, and reload rules.

## Validation and remaining integration

Focused regressions cover the reviewed correctness defects, workspace
lifecycle, formatter behavior, editor adapters, reference safety, and actual
stdio LSP requests. Protocol tests negotiate UTF-8 and UTF-16, exercise
formatting/hover, and verify that Python edits add then clear KV diagnostics.
Controlled Kivy 2.3.1 tests validate formatter syntax and runtime behavior.
See `validation.md` for the final commands, versions, and results, and
`performance.md` for the synthetic timing comparison.

Still requiring the original repositories:

1. Run the existing project test/build/release gates and reconcile dependency
   versions with its package metadata.
2. Check editor configuration, watch support, and formatting/folding provider
   selection in the actual editor integration.
3. Review `tree-sitter-kivy`, run a shared syntax corpus, and change its grammar
   or queries only where those tests demonstrate a mismatch. No claim is made
   that it needs changes or that it already accepts every emitted layout.

Later optional features remain visible: range/on-type formatting, color
previews, richer translation tooling, broader Python typing/refactoring, and
finer background dependency scheduling if measurements justify it.
