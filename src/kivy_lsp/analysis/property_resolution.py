# src/kivy_lsp/analysis/property_resolution.py

from __future__ import annotations

from dataclasses import dataclass, replace

from kivy_lsp.analysis.scope import KvValue
from kivy_lsp.model.property import KivyPropertyInfo
from kivy_lsp.model.symbol import (
    ClassSymbol,
    Symbol,
    SymbolKind,
)
from kivy_lsp.python.index import PythonIndex


@dataclass(frozen=True, slots=True)
class ResolvedKivyProperty:
    """A Kivy property resolved on a Python widget class."""

    owner: ClassSymbol
    symbol: Symbol
    info: KivyPropertyInfo | None

    @property
    def name(self) -> str:
        return self.symbol.name

    @property
    def has_type_information(self) -> bool:
        return self.info is not None


class KivyPropertyResolver:
    """Resolve property names against Python widget classes."""

    def __init__(self, python_index: PythonIndex) -> None:
        self._python_index = python_index

    def resolve(
        self,
        widget_value: KvValue,
        name: str,
    ) -> ResolvedKivyProperty | None:
        class_symbol = self.class_for_value(widget_value)

        if class_symbol is None:
            return None

        symbol = self._property_named(
            class_symbol,
            name,
        )

        if symbol is None:
            return None

        return ResolvedKivyProperty(
            owner=class_symbol,
            symbol=symbol,
            info=self._resolved_info(symbol),
        )

    def class_for_value(
        self,
        value: KvValue,
    ) -> ClassSymbol | None:
        if value.class_symbol is not None:
            return value.class_symbol

        symbol = value.symbol

        if (
            symbol is not None
            and symbol.kind is SymbolKind.CLASS
        ):
            class_symbol = self._python_index.class_named(
                symbol.qualified_name,
            )

            if class_symbol is not None:
                return class_symbol

        type_name = value.type_name

        if type_name is None:
            return None

        class_symbol = self._python_index.class_named(type_name)

        if class_symbol is not None:
            return class_symbol

        return self._python_index.resolve_class(
            type_name,
            from_module=value.module_name,
        )

    def properties_of(
        self,
        widget_value: KvValue,
    ) -> tuple[ResolvedKivyProperty, ...]:
        class_symbol = self.class_for_value(widget_value)

        if class_symbol is None:
            return ()

        properties: list[ResolvedKivyProperty] = []

        for symbol in self._python_index.members_of(class_symbol):
            if symbol.kind is not SymbolKind.PROPERTY:
                continue

            properties.append(
                ResolvedKivyProperty(
                    owner=class_symbol,
                    symbol=symbol,
                    info=self._resolved_info(symbol),
                )
            )

        return tuple(properties)

    def _property_named(
        self,
        class_symbol: ClassSymbol,
        name: str,
    ) -> Symbol | None:
        for symbol in self._python_index.members_of(class_symbol):
            if symbol.name != name:
                continue

            if symbol.kind is not SymbolKind.PROPERTY:
                return None

            return symbol

        return None

    def _resolved_info(
        self,
        symbol: Symbol,
    ) -> KivyPropertyInfo | None:
        info = symbol.property_info

        if (
            info is None
            or info.options
            or info.options_reference is None
        ):
            return info

        module_name = self._python_index.module_name_for_symbol(
            symbol,
        )
        options_symbol = self._python_index.resolve_symbol(
            info.options_reference,
            from_module=module_name,
        )

        if (
            options_symbol is None
            or not options_symbol.literal_values
        ):
            return info

        return replace(
            info,
            options=options_symbol.literal_values,
        )
