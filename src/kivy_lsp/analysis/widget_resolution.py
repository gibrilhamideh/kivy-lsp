from __future__ import annotations

from kivy_lsp.kv.index import KvIndex
from kivy_lsp.model.symbol import ClassSymbol
from kivy_lsp.python.index import PythonIndex


def resolve_widget_class(
    name: str,
    python_index: PythonIndex,
    kv_index: KvIndex,
    *,
    visited: set[str] | None = None,
) -> ClassSymbol | None:
    """Resolve a Python class behind a Python or dynamic KV widget."""
    if visited is None:
        visited = set()

    if name in visited:
        return None

    visited.add(name)
    class_symbol = python_index.resolve_class(name)

    if class_symbol is not None:
        return class_symbol

    matches = python_index.classes_named(name)

    if len(matches) == 1:
        return matches[0]

    for registration in python_index.factory_registrations_named(name):
        class_symbol = python_index.resolve_factory_class(
            registration,
        )

        if class_symbol is not None:
            return class_symbol

    for kv_symbol in kv_index.find(name):
        for base in kv_symbol.bases:
            class_symbol = resolve_widget_class(
                base,
                python_index,
                kv_index,
                visited=visited,
            )

            if class_symbol is not None:
                return class_symbol

    return None

