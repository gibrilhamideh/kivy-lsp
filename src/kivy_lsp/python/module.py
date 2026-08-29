# src/kivy_lsp/python/module.py

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto

from kivy_lsp.model.symbol import (
    ModuleSymbol,
    SymbolLocation,
)


class ImportKind(StrEnum):
    """The semantic form of a Python import."""

    MODULE = auto()
    SYMBOL = auto()
    WILDCARD = auto()


@dataclass(frozen=True, slots=True)
class ImportBinding:
    """A local Python name bound by an import statement."""

    local_name: str
    target_module: str
    target_name: str | None
    relative_level: int
    location: SymbolLocation

    def __post_init__(self) -> None:
        if not self.local_name:
            raise ValueError("import local name cannot be empty")

        if self.relative_level < 0:
            raise ValueError("relative import level cannot be negative")

        if not self.target_module and self.relative_level == 0:
            raise ValueError(
                "absolute import target module cannot be empty"
            )

        if self.target_name == "*" and self.local_name != "*":
            raise ValueError(
                "wildcard import must use '*' as its local name"
            )

    @property
    def kind(self) -> ImportKind:
        if self.target_name == "*":
            return ImportKind.WILDCARD

        if self.target_name is not None:
            return ImportKind.SYMBOL

        return ImportKind.MODULE

    @property
    def is_relative(self) -> bool:
        return self.relative_level > 0

    @property
    def target(self) -> str:
        prefix = "." * self.relative_level
        module = f"{prefix}{self.target_module}"

        if self.target_name is None:
            return module

        if self.target_module:
            return f"{module}.{self.target_name}"

        return f"{prefix}{self.target_name}"


@dataclass(frozen=True, slots=True)
class FactoryRegistration:
    """A widget or template registered with Kivy's Factory."""

    name: str
    location: SymbolLocation
    class_reference: str | None = None
    module_name: str | None = None
    baseclasses: tuple[str, ...] = ()
    is_template: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError(
                "factory registration name cannot be empty"
            )

        if (
            self.class_reference is not None
            and not self.class_reference
        ):
            raise ValueError(
                "factory class reference cannot be empty"
            )

        if self.module_name is not None and not self.module_name:
            raise ValueError(
                "factory module name cannot be empty"
            )

        if any(not base for base in self.baseclasses):
            raise ValueError(
                "factory base class names cannot be empty"
            )

    @property
    def has_static_target(self) -> bool:
        """Whether the registration exposes resolvable type information."""

        return (
            self.class_reference is not None
            or self.module_name is not None
            or bool(self.baseclasses)
        )


@dataclass(frozen=True, slots=True)
class PythonModule:
    """Symbols, imports, and registrations from one Python module."""

    symbol: ModuleSymbol
    imports: tuple[ImportBinding, ...]
    factory_registrations: tuple[
        FactoryRegistration,
        ...,
    ] = ()

    @property
    def name(self) -> str:
        return self.symbol.name

    @property
    def uri(self) -> str:
        return self.symbol.uri

    def import_named(
        self,
        name: str,
    ) -> ImportBinding | None:
        for binding in self.imports:
            if binding.local_name == name:
                return binding

        return None

    def factory_registrations_named(
        self,
        name: str,
    ) -> tuple[FactoryRegistration, ...]:
        return tuple(
            registration
            for registration in self.factory_registrations
            if registration.name == name
        )
