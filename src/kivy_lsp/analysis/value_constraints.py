"""Complete finite value domains shared by completion and diagnostics."""

from __future__ import annotations

import ast

from kivy_lsp.analysis.expression import KvExpressionResolver
from kivy_lsp.analysis.property_resolution import (
    KivyPropertyResolver, resolve_literal_aliases,
)
from kivy_lsp.analysis.scope import KvScope, KvValue
from kivy_lsp.analysis.type_narrowing import KvTypeNarrowings
from kivy_lsp.analysis.value_inference import (
    KvTypeConfidence,
    KvValueInferer,
)
from kivy_lsp.model.property import KivyPropertyInfo, KivyPropertyKind
from kivy_lsp.model.value_type import (
    LiteralValue, ValueType, ValueTypeKind, value_type_from_annotation,
)
from kivy_lsp.python.index import PythonIndex


def finite_values(value_type: ValueType) -> tuple[LiteralValue, ...] | None:
    """Return all possible literals, or None if any arm is unbounded."""
    if value_type.kind is ValueTypeKind.LITERAL:
        return value_type.literals
    if value_type.kind is ValueTypeKind.NONE:
        return (None,)
    if value_type.kind is ValueTypeKind.UNION:
        values: list[LiteralValue] = []
        for argument in value_type.arguments:
            literals = finite_values(argument)
            if literals is None:
                return None
            values.extend(literals)
        return _unique_values(values)
    return None


def property_values(
    info: KivyPropertyInfo,
) -> tuple[LiteralValue, ...] | None:
    """Resolve an exhaustive property domain without guessing defaults."""
    if info.kind is KivyPropertyKind.OPTION and info.options_complete:
        values = info.options
    else:
        values = finite_values(info.accepted_type)
    if values is not None and info.allow_none and None not in values:
        return (*values, None)
    return values


class ValueConstraintResolver:
    """Find finite domains from resolved properties and Literal types."""

    def __init__(
        self,
        python_index: PythonIndex,
        expression_resolver: KvExpressionResolver,
    ) -> None:
        self._index = python_index
        self._properties = KivyPropertyResolver(python_index)
        self._resolver = expression_resolver
        self._inferer = KvValueInferer(expression_resolver)

    def for_expression(
        self,
        source: str,
        scope: KvScope,
        *,
        self_value: KvValue | None = None,
        narrowings: KvTypeNarrowings | None = None,
    ) -> tuple[LiteralValue, ...] | None:
        try:
            node = ast.parse(source.strip(), mode="eval").body
        except SyntaxError:
            return None
        return self._for_node(node, scope, self_value, narrowings)

    def _for_node(
        self,
        node: ast.expr,
        scope: KvScope,
        self_value: KvValue | None,
        narrowings: KvTypeNarrowings | None,
    ) -> tuple[LiteralValue, ...] | None:
        if isinstance(node, ast.IfExp):
            first = self._for_node(
                node.body, scope, self_value, narrowings,
            )
            second = self._for_node(
                node.orelse, scope, self_value, narrowings,
            )
            if first is None or second is None:
                return None
            return _unique_values([*first, *second])

        if isinstance(node, ast.Attribute):
            owner = self._resolver.resolve(
                ast.unparse(node.value), scope, self_value=self_value,
            )
            definitions = self._resolver.member_definitions(owner, node.attr)
            if len(definitions) > 1:
                member_values: list[LiteralValue] = []
                for symbol in definitions:
                    info = self._properties.info_for_symbol(symbol)
                    if info is not None:
                        domain = property_values(info)
                    else:
                        module = self._index.module_name_for_symbol(symbol)
                        domain = finite_values(resolve_literal_aliases(
                            self._index,
                            value_type_from_annotation(symbol.annotation),
                            module,
                        ))
                    if domain is None:
                        return None
                    member_values.extend(domain)
                return _unique_values(member_values)

        source = ast.unparse(node)
        resolution = self._resolver.resolve(
            source, scope, self_value=self_value,
        )
        value = resolution.value
        if value is not None and value.symbol is not None:
            info = self._properties.info_for_symbol(value.symbol)
            if info is not None:
                values = property_values(info)
                if values is not None:
                    return values
                if info.kind is KivyPropertyKind.OPTION:
                    # A default or a partial options list is not exhaustive.
                    return None

        inferred = self._inferer.infer(
            source, scope, self_value=self_value, narrowings=narrowings,
        )
        if inferred.confidence is not KvTypeConfidence.CERTAIN:
            return None
        if inferred.literal_known:
            return (inferred.literal,)
        module_name = value.module_name if value is not None else None
        if value is not None and value.symbol is not None:
            module_name = self._index.module_name_for_symbol(value.symbol)
        return finite_values(resolve_literal_aliases(
            self._index, inferred.value_type, module_name,
        ))


def _unique_values(values: list[LiteralValue]) -> tuple[LiteralValue, ...]:
    unique: list[LiteralValue] = []
    for value in values:
        if not any(
            type(item) is type(value) and item == value for item in unique
        ):
            unique.append(value)
    return tuple(unique)
