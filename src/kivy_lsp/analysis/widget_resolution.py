from __future__ import annotations

from hashlib import sha256

from kivy_lsp.analysis.static_values import expression_annotation
from kivy_lsp.kv.index import KvIndex
from kivy_lsp.model.span import Span
from kivy_lsp.model.symbol import (
    ClassSymbol,
    Symbol,
    SymbolKind,
    SymbolLocation,
)
from kivy_lsp.python.index import PythonIndex


def resolve_widget_class(
    name: str,
    python_index: PythonIndex,
    kv_index: KvIndex | None = None,
    *,
    visited: set[str] | None = None,
) -> ClassSymbol | None:
    """Resolve widgets without discarding dynamic identity or bases."""
    visited = set(visited or ())
    if name in visited:
        return None
    visited.add(name)
    class_symbol = python_index.resolve_class(name)
    if class_symbol is not None:
        return class_symbol
    matches = python_index.classes_named(name)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None

    factory_matches = {
        resolved.qualified_name: resolved
        for registration in python_index.factory_registrations_named(name)
        if (resolved := python_index.resolve_factory_class(registration))
        is not None
    }
    if len(factory_matches) == 1:
        return next(iter(factory_matches.values()))
    if factory_matches or kv_index is None:
        return None

    definitions = tuple(
        symbol for symbol in kv_index.find(name) if symbol.is_dynamic
    )
    if len(definitions) != 1:
        return None
    definition = definitions[0]
    bases = tuple(
        resolve_widget_class(base, python_index, kv_index, visited=visited)
        for base in definition.bases
    )
    inherited = {
        member.name
        for base in bases if base is not None
        for member in python_index.members_of(base)
    }
    identity = sha256(definition.uri.encode()).hexdigest()[:16]
    qualified_name = f"_kv_{identity}.{definition.name}"
    members: dict[str, Symbol] = {}
    for node in definition.properties:
        if (
            node.value is None or node.name in inherited
            or node.name == "id" or node.is_event_handler
            or node.clear_previous is not None
            or not node.name.isidentifier()
        ):
            continue
        members[node.name] = Symbol(
            name=node.name,
            qualified_name=f"{qualified_name}.{node.name}",
            kind=SymbolKind.PROPERTY,
            location=SymbolLocation(
                uri=definition.uri,
                span=node.span,
                selection_span=Span(
                    node.name_tokens[0].span.start,
                    node.name_tokens[-1].span.end,
                ),
            ),
            annotation=expression_annotation(node.value.text),
            documentation="KV-created property",
        )
    return ClassSymbol(
        symbol=Symbol(
            name=definition.name,
            qualified_name=qualified_name,
            kind=SymbolKind.CLASS,
            location=SymbolLocation(
                uri=definition.uri,
                span=definition.span,
                selection_span=definition.span,
            ),
        ),
        bases=definition.bases,
        members=tuple(members.values()),
        resolved_bases=bases,
    )
