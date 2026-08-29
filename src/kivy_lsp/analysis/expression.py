# src/kivy_lsp/analysis/expression.py

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from enum import StrEnum

from kivy_lsp.analysis.scope import (
    KvScope,
    KvValue,
    KvValueKind,
)
from kivy_lsp.config import ServerConfig
from kivy_lsp.kv.nodes import WidgetNode
from kivy_lsp.model.symbol import (
    ClassSymbol,
    ParameterSymbol,
    Symbol,
    SymbolKind,
)
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.type_resolver import (
    PythonTypeResolver,
    ResolvedPythonType,
)
from kivy_lsp.workspace.document import TextDocument

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class KvResolutionKind(StrEnum):
    """The kind of result produced by expression resolution."""

    UNKNOWN = "unknown"
    VALUE = "value"
    ID_NAMESPACE = "id-namespace"


@dataclass(frozen=True, slots=True)
class KvExpressionResolution:
    """The statically resolved result of a KV expression."""

    kind: KvResolutionKind
    value: KvValue | None = None

    @classmethod
    def unknown(cls) -> KvExpressionResolution:
        return cls(kind=KvResolutionKind.UNKNOWN)

    @classmethod
    def resolved(
        cls,
        value: KvValue,
    ) -> KvExpressionResolution:
        return cls(
            kind=KvResolutionKind.VALUE,
            value=value,
        )

    @classmethod
    def id_namespace(cls) -> KvExpressionResolution:
        return cls(kind=KvResolutionKind.ID_NAMESPACE)

    @property
    def is_resolved(self) -> bool:
        return self.kind is not KvResolutionKind.UNKNOWN


class KvExpressionResolver:
    """Resolve KV expressions against semantic and Python indexes."""

    def __init__(
        self,
        python_index: PythonIndex,
        config: ServerConfig | None = None,
    ) -> None:
        self._python_index = python_index
        self._type_resolver = (
            PythonTypeResolver(
                python_index,
                config,
            )
            if config is not None
            else None
        )

    def resolve(
        self,
        expression: str,
        scope: KvScope,
        *,
        self_value: KvValue | None = None,
    ) -> KvExpressionResolution:
        expression = expression.strip()

        while expression.endswith("."):
            expression = expression[:-1].rstrip()

        if not expression:
            return KvExpressionResolution.unknown()

        try:
            parsed = ast.parse(
                expression,
                mode="eval",
            )
        except SyntaxError:
            return self._resolve_dotted_fallback(
                expression,
                scope,
                self_value,
            )

        return self._resolve_node(
            parsed.body,
            scope,
            self_value,
        )

    def members_of(
        self,
        resolution: KvExpressionResolution,
    ) -> tuple[Symbol, ...]:
        if resolution.kind is not KvResolutionKind.VALUE:
            return ()

        value = resolution.value

        if value is None:
            return ()

        local_members = value.local_members
        resolved_type = self._resolved_type_for_value(value)

        if (
            resolved_type is not None
            and self._type_resolver is not None
        ):
            members = self._type_resolver.members_of(
                resolved_type,
            )

            if members:
                return _merge_members(
                    local_members,
                    members,
                )

        if value.class_symbol is not None:
            return _merge_members(
                local_members,
                self._python_index.members_of(
                    value.class_symbol,
                ),
            )

        if value.module_name is not None:
            return _merge_members(
                local_members,
                self._module_members(value.module_name),
            )

        return local_members

    def member_definitions(
        self,
        resolution: KvExpressionResolution,
        name: str,
    ) -> tuple[Symbol, ...]:
        """Return declarations represented by one resolved member."""
        if resolution.kind is not KvResolutionKind.VALUE:
            return ()

        value = resolution.value

        if value is None:
            return ()

        local_member = value.local_member_named(name)

        if local_member is not None:
            return (local_member,)

        if value.module_name is not None:
            member = self._module_member_value(
                value.module_name,
                name,
            )

            if member is None:
                return ()

            if member.symbol is not None:
                return (member.symbol,)

            if member.class_symbol is not None:
                return (member.class_symbol.symbol,)

            return ()

        resolved_type = self._resolved_type_for_value(value)

        if (
            resolved_type is not None
            and self._type_resolver is not None
        ):
            definitions = self._type_resolver.member_definitions(
                resolved_type,
                name,
            )

            if definitions:
                return definitions

        if value.class_symbol is None:
            return ()

        member = self._python_index.member_named(
            value.class_symbol,
            name,
        )

        if member is None:
            return ()

        return (member,)

    def type_of_parameter(
        self,
        function: Symbol,
        parameter: ParameterSymbol,
    ) -> ResolvedPythonType | None:
        """
        Resolve a callable parameter annotation.
        """
        resolver = self._type_resolver

        if resolver is None:
            return None

        source_module = resolver.module_name_for_symbol(
            function,
        )

        return resolver.resolve_annotation(
            parameter.annotation,
            from_module=source_module,
        )

    def self_value(
        self,
        document: TextDocument,
        scope: KvScope,
        widget: WidgetNode | None,
    ) -> KvValue:
        """Resolve the value represented by `self` at the cursor."""
        if widget is None or widget is scope.owner:
            return scope.root_binding.value

        widget_value = scope.value_for_widget(widget)

        if widget_value is not None:
            return widget_value

        class_name = _widget_name(
            document,
            widget,
        )

        if class_name is None:
            return KvValue.unknown()

        class_symbol = self._resolve_class(class_name)

        return KvValue.instance(
            class_name,
            class_symbol,
        )

    def _resolve_node(
        self,
        node: ast.expr,
        scope: KvScope,
        self_value: KvValue | None,
    ) -> KvExpressionResolution:
        if isinstance(node, ast.Name):
            return self._resolve_name(
                node.id,
                scope,
                self_value,
            )

        if isinstance(node, ast.Attribute):
            return self._resolve_attribute(
                node,
                scope,
                self_value,
            )

        if isinstance(node, ast.Subscript):
            return self._resolve_subscript(
                node,
                scope,
                self_value,
            )

        if isinstance(node, ast.Call):
            return self._resolve_call(
                node,
                scope,
                self_value,
            )

        if isinstance(node, ast.IfExp):
            return self._resolve_conditional(
                node,
                scope,
                self_value,
            )

        return KvExpressionResolution.unknown()

    def _resolve_name(
        self,
        name: str,
        scope: KvScope,
        self_value: KvValue | None,
    ) -> KvExpressionResolution:
        if name == "self":
            value = self_value or scope.root_binding.value
            return KvExpressionResolution.resolved(value)

        binding = scope.binding_named(name)

        if binding is None:
            return KvExpressionResolution.unknown()

        return KvExpressionResolution.resolved(binding.value)

    def _resolve_attribute(
        self,
        node: ast.Attribute,
        scope: KvScope,
        self_value: KvValue | None,
    ) -> KvExpressionResolution:
        if _is_root_ids(node):
            return KvExpressionResolution.id_namespace()

        owner = self._resolve_node(
            node.value,
            scope,
            self_value,
        )

        if owner.kind is KvResolutionKind.ID_NAMESPACE:
            binding = scope.id_named(node.attr)

            if binding is None:
                return KvExpressionResolution.unknown()

            return KvExpressionResolution.resolved(binding.value)

        if owner.value is None:
            return KvExpressionResolution.unknown()

        value = self._member_value(
            owner.value,
            node.attr,
        )

        if value is None:
            return KvExpressionResolution.unknown()

        return KvExpressionResolution.resolved(value)

    def _resolve_subscript(
        self,
        node: ast.Subscript,
        scope: KvScope,
        self_value: KvValue | None,
    ) -> KvExpressionResolution:
        owner = self._resolve_node(
            node.value,
            scope,
            self_value,
        )

        if owner.kind is KvResolutionKind.ID_NAMESPACE:
            id_name = _string_constant(node.slice)

            if id_name is None:
                return KvExpressionResolution.unknown()

            binding = scope.id_named(id_name)

            if binding is None:
                return KvExpressionResolution.unknown()

            return KvExpressionResolution.resolved(binding.value)

        if owner.value is None:
            return KvExpressionResolution.unknown()

        resolved_type = self._resolved_type_for_value(
            owner.value,
        )

        if (
            resolved_type is None
            or self._type_resolver is None
        ):
            return KvExpressionResolution.unknown()

        item_type = self._type_resolver.subscript_result(
            resolved_type,
        )

        if item_type is None:
            return KvExpressionResolution.unknown()

        return KvExpressionResolution.resolved(
            self._value_from_resolved_type(item_type)
        )

    def _resolve_call(
        self,
        node: ast.Call,
        scope: KvScope,
        self_value: KvValue | None,
    ) -> KvExpressionResolution:
        id_value = self._resolve_ids_get(
            node,
            scope,
        )

        if id_value is not None:
            return KvExpressionResolution.resolved(id_value)

        callee = self._resolve_node(
            node.func,
            scope,
            self_value,
        )

        if callee.value is None:
            return KvExpressionResolution.unknown()

        value = self._called_value(callee.value)

        if value is None:
            return KvExpressionResolution.unknown()

        return KvExpressionResolution.resolved(value)

    def _resolve_conditional(
        self,
        node: ast.IfExp,
        scope: KvScope,
        self_value: KvValue | None,
    ) -> KvExpressionResolution:
        first = self._resolve_node(
            node.body,
            scope,
            self_value,
        )
        second = self._resolve_node(
            node.orelse,
            scope,
            self_value,
        )

        if _same_value_type(first.value, second.value):
            return first

        return KvExpressionResolution.unknown()

    def _resolve_ids_get(
        self,
        node: ast.Call,
        scope: KvScope,
    ) -> KvValue | None:
        function = node.func

        if not isinstance(function, ast.Attribute):
            return None

        if function.attr != "get":
            return None

        if not _is_root_ids(function.value):
            return None

        if not node.args:
            return None

        id_name = _string_constant(node.args[0])

        if id_name is None:
            return None

        binding = scope.id_named(id_name)

        if binding is None:
            return None

        return binding.value

    def _member_value(
        self,
        owner: KvValue,
        member_name: str,
    ) -> KvValue | None:
        local_member = owner.local_member_named(member_name)

        if local_member is not None:
            return self._symbol_value(local_member)

        if owner.module_name is not None:
            return self._module_member_value(
                owner.module_name,
                member_name,
            )

        resolved_type = self._resolved_type_for_value(owner)

        if (
            resolved_type is not None
            and self._type_resolver is not None
        ):
            member = self._type_resolver.member_named(
                resolved_type,
                member_name,
            )

            if member is not None:
                if member.kind in {
                    SymbolKind.CLASS,
                    SymbolKind.FUNCTION,
                    SymbolKind.METHOD,
                }:
                    return self._symbol_value(member)

                member_type = self._type_resolver.member_type(
                    resolved_type,
                    member_name,
                )

                if (
                    member_type is not None
                    and not member_type.is_unknown
                ):
                    return self._value_from_resolved_type(
                        member_type,
                        symbol=member,
                    )

                return self._symbol_value(member)

        class_symbol = owner.class_symbol

        if class_symbol is None:
            return None

        member = self._python_index.member_named(
            class_symbol,
            member_name,
        )

        if member is None:
            return None

        return self._symbol_value(member)

    def _module_member_value(
        self,
        module_name: str,
        member_name: str,
    ) -> KvValue | None:
        qualified_name = f"{module_name}.{member_name}"
        class_symbol = self._python_index.class_named(
            qualified_name,
        )

        if class_symbol is not None:
            return KvValue.class_value(class_symbol)

        symbol = self._python_index.symbol_named(
            qualified_name,
        )

        if symbol is not None:
            return self._symbol_value(symbol)

        module = self._python_index.module_named(
            qualified_name,
        )

        if module is not None:
            return KvValue.module(module.name)

        return None

    def _symbol_value(self, symbol: Symbol) -> KvValue:
        if symbol.kind is SymbolKind.CLASS:
            class_symbol = self._python_index.class_named(
                symbol.qualified_name,
            )

            if class_symbol is not None:
                return KvValue.class_value(class_symbol)

        if symbol.kind in {
            SymbolKind.FUNCTION,
            SymbolKind.METHOD,
        }:
            return KvValue(
                kind=KvValueKind.FUNCTION,
                type_name=symbol.return_annotation,
                symbol=symbol,
            )

        class_symbol = self._class_from_annotation(
            symbol.annotation,
            symbol,
        )

        return KvValue(
            kind=KvValueKind.VALUE,
            type_name=symbol.annotation,
            class_symbol=class_symbol,
            symbol=symbol,
        )

    def _called_value(
        self,
        value: KvValue,
    ) -> KvValue | None:
        if (
            value.kind is KvValueKind.CLASS
            and value.class_symbol is not None
        ):
            class_symbol = value.class_symbol

            return KvValue.instance(
                class_symbol.symbol.qualified_name,
                class_symbol,
            )

        symbol = value.symbol

        if (
            value.kind is not KvValueKind.FUNCTION
            or symbol is None
        ):
            return None

        if self._type_resolver is not None:
            return_type = self._type_resolver.return_type_of_symbol(
                symbol,
            )

            if return_type.is_unknown:
                return KvValue.unknown()

            return self._value_from_resolved_type(
                return_type,
                symbol=symbol,
            )

        class_symbol = self._class_from_annotation(
            symbol.return_annotation,
            symbol,
        )

        if symbol.return_annotation is None:
            return KvValue.unknown()

        return KvValue(
            kind=KvValueKind.VALUE,
            type_name=symbol.return_annotation,
            class_symbol=class_symbol,
            symbol=symbol,
        )

    def _resolved_type_for_value(
        self,
        value: KvValue,
    ) -> ResolvedPythonType | None:
        resolver = self._type_resolver

        if resolver is None:
            return None

        if value.kind is KvValueKind.FUNCTION:
            return None

        source_module = None

        if value.symbol is not None:
            source_module = resolver.module_name_for_symbol(
                value.symbol,
            )

        if value.type_name is not None:
            return resolver.resolve_annotation(
                value.type_name,
                from_module=source_module,
            )

        if value.class_symbol is not None:
            class_module = self._python_index.module_for_class(
                value.class_symbol,
            )
            module_name = (
                class_module.name
                if class_module is not None
                else source_module
            )

            return resolver.resolve_annotation(
                value.class_symbol.qualified_name,
                from_module=module_name,
            )

        return None

    @staticmethod
    def _value_from_resolved_type(
        resolved_type: ResolvedPythonType,
        *,
        symbol: Symbol | None = None,
    ) -> KvValue:
        if resolved_type.is_unknown:
            return KvValue.unknown()

        return KvValue(
            kind=KvValueKind.VALUE,
            type_name=resolved_type.value_type.display,
            class_symbol=resolved_type.class_symbol,
            symbol=symbol,
        )

    def _class_from_annotation(
        self,
        annotation: str | None,
        symbol: Symbol,
    ) -> ClassSymbol | None:
        if annotation is None:
            return None

        if self._type_resolver is not None:
            resolved_type = self._type_resolver.resolve_annotation(
                annotation,
                from_module=self._module_name_for_symbol(symbol),
            )
            return resolved_type.class_symbol

        module_name = self._module_name_for_symbol(symbol)

        for reference in _annotation_references(annotation):
            class_symbol = self._python_index.resolve_class(
                reference,
                from_module=module_name,
            )

            if class_symbol is not None:
                return class_symbol

        return None

    def _module_name_for_symbol(
        self,
        symbol: Symbol,
    ) -> str | None:
        if self._type_resolver is not None:
            return self._type_resolver.module_name_for_symbol(
                symbol,
            )

        qualified_name = symbol.qualified_name
        matches = tuple(
            module.name
            for module in self._python_index.modules
            if qualified_name.startswith(f"{module.name}.")
        )

        if not matches:
            return None

        return max(matches, key=len)

    def _module_members(
        self,
        module_name: str,
    ) -> tuple[Symbol, ...]:
        module = self._python_index.module_named(module_name)

        if module is None:
            return ()

        members: dict[str, Symbol] = {}

        for class_symbol in module.symbol.classes:
            symbol = class_symbol.symbol
            members.setdefault(symbol.name, symbol)

        for symbol in module.symbol.symbols:
            members.setdefault(symbol.name, symbol)

        return tuple(members.values())

    def _resolve_class(
        self,
        name: str,
    ) -> ClassSymbol | None:
        class_symbol = self._python_index.resolve_class(name)

        if class_symbol is not None:
            return class_symbol

        matches = self._python_index.classes_named(name)

        if len(matches) == 1:
            return matches[0]

        return None

    def _resolve_dotted_fallback(
        self,
        expression: str,
        scope: KvScope,
        self_value: KvValue | None,
    ) -> KvExpressionResolution:
        parts = tuple(
            part.strip()
            for part in expression.split(".")
            if part.strip()
        )

        if not parts:
            return KvExpressionResolution.unknown()

        if any(
            _IDENTIFIER_PATTERN.fullmatch(part) is None
            for part in parts
        ):
            return KvExpressionResolution.unknown()

        resolution = self._resolve_name(
            parts[0],
            scope,
            self_value,
        )

        for member_name in parts[1:]:
            if resolution.value is None:
                return KvExpressionResolution.unknown()

            value = self._member_value(
                resolution.value,
                member_name,
            )

            if value is None:
                return KvExpressionResolution.unknown()

            resolution = KvExpressionResolution.resolved(value)

        return resolution


def _is_root_ids(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "ids"
        and isinstance(node.value, ast.Name)
        and node.value.id == "root"
    )


def _string_constant(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Constant):
        return None

    if not isinstance(node.value, str):
        return None

    return node.value


def _same_value_type(
    first: KvValue | None,
    second: KvValue | None,
) -> bool:
    if first is None or second is None:
        return False

    if (
        first.class_symbol is not None
        and second.class_symbol is not None
    ):
        return (
            first.class_symbol.symbol.qualified_name
            == second.class_symbol.symbol.qualified_name
        )

    return (
        first.type_name is not None
        and first.type_name == second.type_name
    )


def _annotation_references(annotation: str) -> tuple[str, ...]:
    annotation = annotation.strip().strip("'\"")

    if annotation.startswith("Optional[") and annotation.endswith("]"):
        annotation = annotation[9:-1]

    references: list[str] = []

    for part in annotation.split("|"):
        reference = part.strip()

        if reference in {"", "None", "NoneType"}:
            continue

        references.append(reference)

    return tuple(references)


def _merge_members(
    primary: tuple[Symbol, ...],
    secondary: tuple[Symbol, ...],
) -> tuple[Symbol, ...]:
    members: dict[str, Symbol] = {}

    for member in primary:
        members.setdefault(
            member.name,
            member,
        )

    for member in secondary:
        members.setdefault(
            member.name,
            member,
        )

    return tuple(members.values())


def _widget_name(
    document: TextDocument,
    widget: WidgetNode,
) -> str | None:
    line_end = document.text.find(
        "\n",
        widget.span.start,
        widget.span.end,
    )

    if line_end == -1:
        line_end = widget.span.end

    header = document.text[
        widget.span.start:line_end
    ].strip()
    name, separator, _ = header.partition(":")

    if not separator:
        return None

    name = name.strip()

    if _IDENTIFIER_PATTERN.fullmatch(name) is None:
        return None

    return name

