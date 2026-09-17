# Option values, Literal completion, and diagnostics

The editor and `kivy-lsp check` use the same analysis engine. Completion uses
the resolved Python declarations, including inherited Kivy properties and
supported `Literal` annotations. Application code is not executed to discover
the values.

## Completion in comparisons

Given a property such as:

```python
mode = OptionProperty(
    "continuous", options=["emergency", "cycle", "continuous"]
)
```

or an annotation such as:

```python
mode: Literal["emergency", "cycle", "continuous"]
```

completion inside the string in this KV expression suggests the modes:

```kv
RuntimeContinuousVentilationContent:
    display: root.ventilation.mode == ""
```

The suggestions come from `mode`, even though the expression is assigned to
`display`. Equality and inequality (`==`, `!=`) work with the typed expression
on either side. Prefixes filter the choices, and accepting a completion
replaces the value being edited rather than adding a second pair of quotes.
The intermediate objects in a chain must have enough type information for
the server to resolve the final member.

This supports explicit backslash continuation with the same KV indentation
rules as the formatter:

```kv
display:
    root.enabled and \
    root.ventilation.mode == "continuous"
```

## Conservative default validation

| Situation | Default result |
| --- | --- |
| A literal is one of a property's allowed options | No diagnostic |
| A literal is outside a property's complete known options | Error |
| All values in a finite inferred set are invalid for the property | Error |
| Some inferred values are valid and others are invalid | No warning unless strict checking is enabled |
| A general `str` or `StringProperty` supplies an option value | No option-value diagnostic |
| An expression or its allowed values cannot be resolved | No speculative option-value diagnostic |
| Equality compares two known, disjoint finite value sets | Warning that the comparison cannot match |

For example, `halign: "hello"` is an error when the resolved `halign` options
exclude `"hello"`. However, `halign: root.alignment` stays quiet when
`alignment` is a general string. Its actual value might be valid. Kivy still
validates the value at runtime.

The default in `StringProperty("left")` does not restrict that property's
future values to `"left"`. Likewise, a union containing an unrestricted `str`
does not define an exhaustive set of string values. Partially resolved
dynamic option lists are not treated as complete constraints.

Comparison warnings concern the resolved value sets; they are not a general
proof of program reachability. Runtime-generated attributes and values can
remain unknown to static analysis.

## Optional strict checking

Add this to the project's `kivy-lsp.toml`:

```toml
[diagnostics]
strict = true
```

Strict checking enables warnings for possibly incompatible property values,
including a general string whose membership in known options cannot be
proved. It does not turn unknown declarations into invented types. The
default is `false`.

See [cli.md](cli.md) for checking saved files and using the formatter from a
terminal.
