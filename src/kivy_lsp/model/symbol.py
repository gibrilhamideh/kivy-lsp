# src/kivy_lsp/model/symbol.py

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kivy_lsp.model.property import KivyPropertyInfo
from kivy_lsp.model.span import Span
from kivy_lsp.model.value_type import LiteralValue


class SymbolKind(StrEnum):
    """The semantic category of a source symbol."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    PROPERTY = "property"
    VARIABLE = "variable"
    CONSTANT = "constant"
    PARAMETER = "parameter"
    EVENT = "event"
    ID = "id"


class ParameterKind(StrEnum):
    """The calling convention of a Python parameter."""

    POSITIONAL_ONLY = "positional_only"
    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    VAR_POSITIONAL = "var_positional"
    KEYWORD_ONLY = "keyword_only"
    VAR_KEYWORD = "var_keyword"


@dataclass(frozen=True, slots=True)
class SymbolLocation:
    """The source location of a symbol."""

    uri: str
    span: Span
    selection_span: Span

    def __post_init__(self) -> None:
        if not self.uri:
            raise ValueError("Symbol location URI cannot be empty.")

        if self.selection_span.start < self.span.start:
            raise ValueError(
                "Symbol selection cannot start before its full span."
            )

        if self.selection_span.end > self.span.end:
            raise ValueError(
                "Symbol selection cannot end after its full span."
            )


@dataclass(frozen=True, slots=True)
class ParameterSymbol:
    """A Python function or method parameter."""

    name: str
    kind: ParameterKind
    annotation: str | None = None
    default: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Parameter name cannot be empty.")


@dataclass(frozen=True, slots=True)
class Symbol:
    """A named semantic object found in Python source."""

    name: str
    qualified_name: str
    kind: SymbolKind
    location: SymbolLocation
    annotation: str | None = None
    signature: str | None = None
    documentation: str | None = None
    parameters: tuple[ParameterSymbol, ...] = ()
    return_annotation: str | None = None
    property_info: KivyPropertyInfo | None = None
    literal_values: tuple[LiteralValue, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Symbol name cannot be empty.")

        if not self.qualified_name:
            raise ValueError("Qualified symbol name cannot be empty.")

        if (
            self.property_info is not None
            and self.kind is not SymbolKind.PROPERTY
        ):
            raise ValueError(
                "Only property symbols may contain property metadata."
            )

    @property
    def uri(self) -> str:
        return self.location.uri

    @property
    def span(self) -> Span:
        return self.location.span

    @property
    def selection_span(self) -> Span:
        return self.location.selection_span


@dataclass(frozen=True, slots=True)
class ClassSymbol:
    """A Python class and its statically discovered members."""

    symbol: Symbol
    bases: tuple[str, ...]
    members: tuple[Symbol, ...]

    def __post_init__(self) -> None:
        if self.symbol.kind is not SymbolKind.CLASS:
            raise ValueError(
                "ClassSymbol must contain a class symbol."
            )

    @property
    def name(self) -> str:
        return self.symbol.name

    @property
    def qualified_name(self) -> str:
        return self.symbol.qualified_name

    @property
    def uri(self) -> str:
        return self.symbol.uri

    @property
    def location(self) -> SymbolLocation:
        return self.symbol.location

    @property
    def documentation(self) -> str | None:
        return self.symbol.documentation

    def member_named(self, name: str) -> Symbol | None:
        for member in self.members:
            if member.name == name:
                return member

        return None


@dataclass(frozen=True, slots=True)
class ModuleSymbol:
    """The symbols statically discovered in a Python module."""

    name: str
    uri: str
    classes: tuple[ClassSymbol, ...]
    symbols: tuple[Symbol, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Module name cannot be empty.")

        if not self.uri:
            raise ValueError("Module URI cannot be empty.")

    def class_named(self, name: str) -> ClassSymbol | None:
        for class_symbol in self.classes:
            if (
                class_symbol.name == name
                or class_symbol.qualified_name == name
            ):
                return class_symbol

        return None

    def symbol_named(self, name: str) -> Symbol | None:
        for symbol in self.symbols:
            if (
                symbol.name == name
                or symbol.qualified_name == name
            ):
                return symbol

        for class_symbol in self.classes:
            if (
                class_symbol.name == name
                or class_symbol.qualified_name == name
            ):
                return class_symbol.symbol

        return None
