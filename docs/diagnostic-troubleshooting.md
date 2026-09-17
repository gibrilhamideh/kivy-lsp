# Diagnosing a project report

The checker reports what it can resolve statically. Its output can expose
application mistakes, missing analysis configuration, or checker defects.
Do not change valid KV solely to satisfy a diagnostic before identifying
which case applies.

## Kivy setter inputs and property read types

A property's annotation can describe the value returned when reading it,
while Kivy accepts additional input forms when setting it. For example, a
`ListProperty` returns a list but accepts tuple input. A `VariableListProperty`
expands shorthand input into the configured stored length. For length four,
one, two, or four sequence items are valid; three are not.

The checker preserves the distinction between those Kivy setter contracts
and ordinary Python function parameter types. It also retains explicit
`Literal` restrictions. The same setter rules apply when a read annotation
uses a local or imported `type` alias. These rules use actual Kivy 2.3.1
property assignments as well as static analysis tests. Kivy documents the
expansion behavior in its
[property reference](https://kivy.org/doc/stable/api-kivy.properties.html#kivy.properties.VariableListProperty).

Custom descriptors and `AliasProperty` setters may have their own rules;
their declarations are needed to determine the appropriate accepted inputs.

## Unresolved bases and aliases

If an indexed class inherits from a base that cannot be resolved, the known
members are incomplete. Missing-member errors must not treat that list as
exhaustive. Known members remain available for completion, while definitive
missing-member checks require complete ancestry.

Simple Python 3.12 `type` aliases can supply property and argument types,
including finite string values:

```python
from typing import Literal

type KeyName = Literal["backspace", "left", "right"]
type KeyCode = int
```

Explicit legacy module aliases are also supported:

```python
from typing import TypeAlias

FnKeycode: TypeAlias = str | None
```

Here, a parameter annotated `FnKey | FnKeycode` accepts strings such as
`"backspace"`, `"left"`, and `"right"`, as well as `None` or a `FnKey`
instance. A diagnostic rejecting those strings was a checker defect in
legacy alias indexing. The shared resolver expands imported and nested
aliases for both editor diagnostics and CLI checks.

Unresolved or unsupported generic aliases remain conservative. A concrete
enum class and a string literal are not automatically interchangeable;
inspect the actual parameter and type declarations when such a diagnostic
persists.

## Names registered at runtime

Runtime registration of a global such as `I18nRef` or `md_icons` does not
by itself add it to the static KV scope. Libraries can publish its actual
fully qualified target in their packaged `kivy-lsp.toml` file. The analyzer
inherits those declarations when the package is referenced by the project.
Application `[globals]` and `[global-imports]` declarations can add or
override individual names. A per-file KV import also works.

See [config.md](config.md) for metadata discovery and precedence. The LSP
does not import or execute the app to discover its namespace, and does not
guess framework-specific paths.

## Other reports to inspect

- An argument outside a complete known `Literal` set is actionable. For
  example, `outside-humidty` differs from the allowed `outside_humidity` in
  both spelling and separator.
- An unknown translation key means it was not found in the configured
  translation catalog. Check the key and the selected catalog.
- Assigning a widget object to `text_size` needs inspection of the actual KV
  expression. The intended value is often dimensions, but the checker should
  not automatically substitute `.size` or another expression.
- A KV rule for a behavior/mixin may rely on members of its eventual widget
  subclasses. Its class declarations and intended use are needed to assess
  unresolved member reports accurately.

For follow-up diagnosis, include the relevant KV lines, Python declarations
and imports, and the `kivy-lsp.toml` configuration. A CLI exit code of
`1` means diagnostics were found; `2` means checking could not complete.
