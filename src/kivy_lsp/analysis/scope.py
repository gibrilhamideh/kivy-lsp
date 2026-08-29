# src/kivy_lsp/analysis/scope.py

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kivy_lsp.kv.nodes import RuleNode, WidgetNode
from kivy_lsp.model.diagnostic import Diagnostic
from kivy_lsp.model.span import Span
from kivy_lsp.model.symbol import ClassSymbol, Symbol


class KvValueKind(StrEnum):
    """The semantic category of a value available in KV."""

    UNKNOWN = "unknown"
    INSTANCE = "instance"
    CLASS = "class"
    MODULE = "module"
    FUNCTION = "function"
    VALUE = "value"


@dataclass(frozen=True, slots=True)
class KvValue:
    """A statically known value and its optional Python definition."""

    kind: KvValueKind
    type_name: str | None = None
    class_symbol: ClassSymbol | None = None
    symbol: Symbol | None = None
    module_name: str | None = None
    local_members: tuple[Symbol, ...] = ()

    @classmethod
    def unknown(
        cls,
        type_name: str | None = None,
    ) -> KvValue:
        return cls(
            kind=KvValueKind.UNKNOWN,
            type_name=type_name,
        )

    @classmethod
    def instance(
        cls,
        type_name: str,
        class_symbol: ClassSymbol | None = None,
        *,
        local_members: tuple[Symbol, ...] = (),
    ) -> KvValue:
        return cls(
            kind=KvValueKind.INSTANCE,
            type_name=type_name,
            class_symbol=class_symbol,
            local_members=local_members,
        )

    @classmethod
    def class_value(
        cls,
        class_symbol: ClassSymbol,
    ) -> KvValue:
        return cls(
            kind=KvValueKind.CLASS,
            type_name=class_symbol.symbol.qualified_name,
            class_symbol=class_symbol,
            symbol=class_symbol.symbol,
        )

    @classmethod
    def module(
        cls,
        module_name: str,
    ) -> KvValue:
        return cls(
            kind=KvValueKind.MODULE,
            type_name=module_name,
            module_name=module_name,
        )

    @classmethod
    def from_symbol(
        cls,
        symbol: Symbol,
        *,
        class_symbol: ClassSymbol | None = None,
    ) -> KvValue:
        if class_symbol is not None:
            type_name = class_symbol.symbol.qualified_name
        else:
            type_name = symbol.annotation

        return cls(
            kind=KvValueKind.VALUE,
            type_name=type_name,
            class_symbol=class_symbol,
            symbol=symbol,
        )

    @property
    def is_known(self) -> bool:
        return self.kind is not KvValueKind.UNKNOWN

    def local_member_named(
        self,
        name: str,
    ) -> Symbol | None:
        for member in self.local_members:
            if member.name == name:
                return member

        return None

    def with_local_members(
        self,
        members: tuple[Symbol, ...],
    ) -> KvValue:
        return KvValue(
            kind=self.kind,
            type_name=self.type_name,
            class_symbol=self.class_symbol,
            symbol=self.symbol,
            module_name=self.module_name,
            local_members=members,
        )


class KvBindingKind(StrEnum):
    """How a name entered the current KV expression scope."""

    ROOT = "root"
    SELF = "self"
    APP = "app"
    ID = "id"
    GLOBAL = "global"
    BUILTIN = "builtin"


@dataclass(frozen=True, slots=True)
class KvBinding:
    """A name that can be referenced inside a KV expression."""

    name: str
    kind: KvBindingKind
    value: KvValue
    declaration_span: Span | None = None
    widget: WidgetNode | None = None

    @property
    def is_id(self) -> bool:
        return self.kind is KvBindingKind.ID


type KvScopeOwner = RuleNode | WidgetNode


@dataclass(frozen=True, slots=True)
class KvWidgetValue:
    """The semantic value assigned to one concrete widget node."""

    widget: WidgetNode
    value: KvValue


@dataclass(frozen=True, slots=True)
class KvScope:
    """Names visible inside one KV rule or root widget declaration."""

    uri: str
    owner: KvScopeOwner
    span: Span
    root_binding: KvBinding
    app_binding: KvBinding | None = None
    id_bindings: tuple[KvBinding, ...] = ()
    global_bindings: tuple[KvBinding, ...] = ()
    widget_values: tuple[KvWidgetValue, ...] = ()

    def id_named(self, name: str) -> KvBinding | None:
        for binding in self.id_bindings:
            if binding.name == name:
                return binding

        return None

    def value_for_widget(
        self,
        widget: WidgetNode,
    ) -> KvValue | None:
        for item in self.widget_values:
            if item.widget is widget:
                return item.value

        return None

    def binding_named(
        self,
        name: str,
        *,
        self_binding: KvBinding | None = None,
    ) -> KvBinding | None:
        for binding in self.visible_bindings(
            self_binding=self_binding,
        ):
            if binding.name == name:
                return binding

        return None

    def visible_bindings(
        self,
        *,
        self_binding: KvBinding | None = None,
    ) -> tuple[KvBinding, ...]:
        ordered = (
            self.root_binding,
            self_binding,
            self.app_binding,
            *self.id_bindings,
            *self.global_bindings,
        )

        bindings: list[KvBinding] = []
        names: set[str] = set()

        for binding in ordered:
            if binding is None:
                continue

            if binding.name in names:
                continue

            names.add(binding.name)
            bindings.append(binding)

        return tuple(bindings)


@dataclass(frozen=True, slots=True)
class KvSemanticModel:
    """All semantic scopes and diagnostics for one KV document."""

    uri: str
    scopes: tuple[KvScope, ...]
    diagnostics: tuple[Diagnostic, ...] = ()

    def scope_at(self, offset: int) -> KvScope | None:
        matches = tuple(
            scope
            for scope in self.scopes
            if scope.span.contains_cursor(offset)
        )

        if matches:
            return min(
                matches,
                key=lambda scope: scope.span.length,
            )

        preceding = tuple(
            scope
            for scope in self.scopes
            if scope.span.start <= offset
        )

        if not preceding:
            return None

        return max(
            preceding,
            key=lambda scope: (
                scope.span.start,
                scope.span.end,
            ),
        )

    def scope_for_owner(
        self,
        owner: KvScopeOwner,
    ) -> KvScope | None:
        for scope in self.scopes:
            if scope.owner is owner:
                return scope

        return None
