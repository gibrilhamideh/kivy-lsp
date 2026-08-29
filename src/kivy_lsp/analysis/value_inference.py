# src/kivy_lsp/analysis/value_inference.py

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum

from kivy_lsp.analysis.expression import (
    KvExpressionResolver,
    KvResolutionKind,
)
from kivy_lsp.analysis.scope import (
    KvScope,
    KvValue,
    KvValueKind,
)
from kivy_lsp.analysis.type_narrowing import (
    KvTypeNarrowings,
    branch_narrowings,
    merge_narrowings,
    narrow_value_type,
)
from kivy_lsp.model.value_type import (
    BOOL_TYPE,
    CALLABLE_TYPE,
    FLOAT_TYPE,
    INT_TYPE,
    NUMBER_TYPE,
    STRING_TYPE,
    UNKNOWN_TYPE,
    LiteralValue,
    ValueType,
    ValueTypeKind,
    literal_type,
    object_type,
    union_type,
    value_type_from_annotation,
)


class KvTypeConfidence(StrEnum):
    """How confidently an expression value type was inferred."""

    UNKNOWN = "unknown"
    POSSIBLE = "possible"
    CERTAIN = "certain"


@dataclass(frozen=True, slots=True)
class KvInferredValue:
    """The inferred type and literal value of a KV expression."""

    value_type: ValueType
    confidence: KvTypeConfidence
    literal_known: bool = False
    literal: LiteralValue = None

    @classmethod
    def unknown(cls) -> KvInferredValue:
        return cls(
            value_type=UNKNOWN_TYPE,
            confidence=KvTypeConfidence.UNKNOWN,
        )

    @classmethod
    def typed(
        cls,
        value_type: ValueType,
        confidence: KvTypeConfidence,
    ) -> KvInferredValue:
        return cls(
            value_type=value_type,
            confidence=confidence,
        )

    @classmethod
    def literal_value(
        cls,
        value: LiteralValue,
    ) -> KvInferredValue:
        return cls(
            value_type=literal_type(value),
            confidence=KvTypeConfidence.CERTAIN,
            literal_known=True,
            literal=value,
        )

    @property
    def is_known(self) -> bool:
        return self.confidence is not KvTypeConfidence.UNKNOWN


class KvValueInferer:
    """Infer static types from KV expression syntax and symbols."""

    def __init__(
        self,
        expression_resolver: KvExpressionResolver,
    ) -> None:
        self._expression_resolver = expression_resolver

    def infer(
        self,
        expression: str,
        scope: KvScope,
        *,
        self_value: KvValue | None = None,
        narrowings: KvTypeNarrowings | None = None,
    ) -> KvInferredValue:
        """Infer the static type of a complete KV expression."""
        try:
            parsed = ast.parse(
                expression,
                mode="eval",
            )
        except SyntaxError:
            return KvInferredValue.unknown()

        return self._infer_node(
            parsed.body,
            scope,
            self_value,
            narrowings or {},
        )

    def _infer_node(
        self,
        node: ast.expr,
        scope: KvScope,
        self_value: KvValue | None,
        narrowings: KvTypeNarrowings,
    ) -> KvInferredValue:
        if isinstance(node, ast.Constant):
            return self._infer_constant(node)

        if isinstance(node, ast.UnaryOp):
            return self._infer_unary(
                node,
                scope,
                self_value,
                narrowings,
            )

        if isinstance(node, ast.BinOp):
            return self._infer_binary(
                node,
                scope,
                self_value,
                narrowings,
            )

        if isinstance(node, (ast.BoolOp, ast.Compare)):
            return KvInferredValue.typed(
                BOOL_TYPE,
                KvTypeConfidence.CERTAIN,
            )

        if isinstance(node, ast.IfExp):
            return self._infer_conditional(
                node,
                scope,
                self_value,
                narrowings,
            )

        if isinstance(node, ast.List):
            return self._infer_list(
                node,
                scope,
                self_value,
                narrowings,
            )

        if isinstance(node, ast.Tuple):
            return self._infer_tuple(
                node,
                scope,
                self_value,
                narrowings,
            )

        if isinstance(node, ast.Set):
            return self._infer_set(
                node,
                scope,
                self_value,
                narrowings,
            )

        if isinstance(node, ast.Dict):
            return self._infer_dict(
                node,
                scope,
                self_value,
                narrowings,
            )

        if isinstance(node, ast.JoinedStr):
            return KvInferredValue.typed(
                STRING_TYPE,
                KvTypeConfidence.CERTAIN,
            )

        if isinstance(node, ast.Lambda):
            return KvInferredValue.typed(
                CALLABLE_TYPE,
                KvTypeConfidence.CERTAIN,
            )

        if isinstance(node, ast.NamedExpr):
            return self._infer_node(
                node.value,
                scope,
                self_value,
                narrowings,
            )

        if isinstance(
            node,
            (
                ast.Name,
                ast.Attribute,
                ast.Subscript,
                ast.Call,
            ),
        ):
            return self._infer_resolved_expression(
                node,
                scope,
                self_value,
                narrowings,
            )

        return KvInferredValue.unknown()

    @staticmethod
    def _infer_constant(
        node: ast.Constant,
    ) -> KvInferredValue:
        is_literal, value = _literal_value(node)

        if not is_literal:
            return KvInferredValue.unknown()

        return KvInferredValue.literal_value(value)

    def _infer_unary(
        self,
        node: ast.UnaryOp,
        scope: KvScope,
        self_value: KvValue | None,
        narrowings: KvTypeNarrowings,
    ) -> KvInferredValue:
        if isinstance(node.op, ast.Not):
            return KvInferredValue.typed(
                BOOL_TYPE,
                KvTypeConfidence.CERTAIN,
            )

        number = _number_value(node)

        if number is not None:
            return KvInferredValue.literal_value(number)

        operand = self._infer_node(
            node.operand,
            scope,
            self_value,
            narrowings,
        )

        if _is_numeric(operand.value_type):
            return KvInferredValue.typed(
                operand.value_type,
                operand.confidence,
            )

        return KvInferredValue.unknown()

    def _infer_binary(
        self,
        node: ast.BinOp,
        scope: KvScope,
        self_value: KvValue | None,
        narrowings: KvTypeNarrowings,
    ) -> KvInferredValue:
        left = self._infer_node(
            node.left,
            scope,
            self_value,
            narrowings,
        )
        right = self._infer_node(
            node.right,
            scope,
            self_value,
            narrowings,
        )
        confidence = _combined_confidence(
            left.confidence,
            right.confidence,
        )

        if (
            isinstance(node.op, ast.Add)
            and _is_string(left.value_type)
            and _is_string(right.value_type)
        ):
            return KvInferredValue.typed(
                STRING_TYPE,
                confidence,
            )

        if (
            isinstance(node.op, ast.Mult)
            and (
                (
                    _is_string(left.value_type)
                    and _is_integer(right.value_type)
                )
                or (
                    _is_integer(left.value_type)
                    and _is_string(right.value_type)
                )
            )
        ):
            return KvInferredValue.typed(
                STRING_TYPE,
                confidence,
            )

        if (
            _is_numeric(left.value_type)
            and _is_numeric(right.value_type)
        ):
            return KvInferredValue.typed(
                _numeric_result_type(
                    left.value_type,
                    right.value_type,
                    node.op,
                ),
                confidence,
            )

        return KvInferredValue.unknown()

    def _infer_conditional(
        self,
        node: ast.IfExp,
        scope: KvScope,
        self_value: KvValue | None,
        narrowings: KvTypeNarrowings,
    ) -> KvInferredValue:
        first_narrowings = merge_narrowings(
            narrowings,
            branch_narrowings(
                node.test,
                truthy=True,
            ),
        )
        second_narrowings = merge_narrowings(
            narrowings,
            branch_narrowings(
                node.test,
                truthy=False,
            ),
        )
        first = self._infer_node(
            node.body,
            scope,
            self_value,
            first_narrowings,
        )
        second = self._infer_node(
            node.orelse,
            scope,
            self_value,
            second_narrowings,
        )

        if not first.is_known or not second.is_known:
            return KvInferredValue.unknown()

        return KvInferredValue.typed(
            union_type(
                first.value_type,
                second.value_type,
            ),
            _combined_confidence(
                first.confidence,
                second.confidence,
            ),
        )

    def _infer_list(
        self,
        node: ast.List,
        scope: KvScope,
        self_value: KvValue | None,
        narrowings: KvTypeNarrowings,
    ) -> KvInferredValue:
        item_type = self._sequence_item_type(
            node.elts,
            scope,
            self_value,
            narrowings,
        )

        return KvInferredValue.typed(
            ValueType(
                kind=ValueTypeKind.LIST,
                arguments=(item_type,),
            ),
            KvTypeConfidence.CERTAIN,
        )

    def _infer_tuple(
        self,
        node: ast.Tuple,
        scope: KvScope,
        self_value: KvValue | None,
        narrowings: KvTypeNarrowings,
    ) -> KvInferredValue:
        arguments = tuple(
            self._infer_node(
                element,
                scope,
                self_value,
                narrowings,
            ).value_type
            for element in node.elts
        )

        return KvInferredValue.typed(
            ValueType(
                kind=ValueTypeKind.TUPLE,
                arguments=arguments,
            ),
            KvTypeConfidence.CERTAIN,
        )

    def _infer_set(
        self,
        node: ast.Set,
        scope: KvScope,
        self_value: KvValue | None,
        narrowings: KvTypeNarrowings,
    ) -> KvInferredValue:
        item_type = self._sequence_item_type(
            node.elts,
            scope,
            self_value,
            narrowings,
        )

        return KvInferredValue.typed(
            ValueType(
                kind=ValueTypeKind.SET,
                arguments=(item_type,),
            ),
            KvTypeConfidence.CERTAIN,
        )

    def _infer_dict(
        self,
        node: ast.Dict,
        scope: KvScope,
        self_value: KvValue | None,
        narrowings: KvTypeNarrowings,
    ) -> KvInferredValue:
        key_types: list[ValueType] = []
        value_types: list[ValueType] = []

        for key in node.keys:
            if key is None:
                continue

            inferred_key = self._infer_node(
                key,
                scope,
                self_value,
                narrowings,
            )

            if inferred_key.is_known:
                key_types.append(inferred_key.value_type)

        for value in node.values:
            inferred_value = self._infer_node(
                value,
                scope,
                self_value,
                narrowings,
            )

            if inferred_value.is_known:
                value_types.append(
                    inferred_value.value_type
                )

        key_type = (
            union_type(*key_types)
            if key_types
            else UNKNOWN_TYPE
        )
        value_type = (
            union_type(*value_types)
            if value_types
            else UNKNOWN_TYPE
        )

        return KvInferredValue.typed(
            ValueType(
                kind=ValueTypeKind.DICT,
                arguments=(
                    key_type,
                    value_type,
                ),
            ),
            KvTypeConfidence.CERTAIN,
        )

    def _sequence_item_type(
        self,
        elements: list[ast.expr],
        scope: KvScope,
        self_value: KvValue | None,
        narrowings: KvTypeNarrowings,
    ) -> ValueType:
        item_types: list[ValueType] = []

        for element in elements:
            inferred = self._infer_node(
                element,
                scope,
                self_value,
                narrowings,
            )

            if inferred.is_known:
                item_types.append(inferred.value_type)

        if not item_types:
            return UNKNOWN_TYPE

        return union_type(*item_types)

    def _infer_resolved_expression(
        self,
        node: ast.expr,
        scope: KvScope,
        self_value: KvValue | None,
        narrowings: KvTypeNarrowings,
    ) -> KvInferredValue:
        expression = ast.unparse(node)
        resolution = self._expression_resolver.resolve(
            expression,
            scope,
            self_value=self_value,
        )

        if resolution.kind is not KvResolutionKind.VALUE:
            return KvInferredValue.unknown()

        value = resolution.value

        if value is None:
            return KvInferredValue.unknown()

        if value.type_name is not None:
            value_type = value_type_from_annotation(
                value.type_name,
            )

            if value_type.kind in {
                ValueTypeKind.UNKNOWN,
                ValueTypeKind.ANY,
            }:
                return KvInferredValue.unknown()

            return KvInferredValue.typed(
                narrow_value_type(
                    node,
                    value_type,
                    narrowings,
                ),
                KvTypeConfidence.CERTAIN,
            )

        if value.class_symbol is not None:
            return KvInferredValue.typed(
                narrow_value_type(
                    node,
                    object_type(
                        value.class_symbol.qualified_name
                    ),
                    narrowings,
                ),
                KvTypeConfidence.CERTAIN,
            )

        if value.kind is KvValueKind.FUNCTION:
            return KvInferredValue.typed(
                CALLABLE_TYPE,
                KvTypeConfidence.CERTAIN,
            )

        return KvInferredValue.unknown()


def _literal_value(
    node: ast.Constant,
) -> tuple[bool, LiteralValue]:
    value = node.value

    if value is None:
        return True, None

    if isinstance(value, bool):
        return True, value

    if isinstance(value, int):
        return True, value

    if isinstance(value, float):
        return True, value

    if isinstance(value, str):
        return True, value

    return False, None


def _number_value(
    node: ast.expr,
) -> int | float | None:
    if isinstance(node, ast.Constant):
        value = node.value

        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            return value

        return None

    if not isinstance(node, ast.UnaryOp):
        return None

    operand = _number_value(node.operand)

    if operand is None:
        return None

    if isinstance(node.op, ast.USub):
        return -operand

    if isinstance(node.op, ast.UAdd):
        return operand

    return None


def _combined_confidence(
    first: KvTypeConfidence,
    second: KvTypeConfidence,
) -> KvTypeConfidence:
    if (
        first is KvTypeConfidence.UNKNOWN
        or second is KvTypeConfidence.UNKNOWN
    ):
        return KvTypeConfidence.UNKNOWN

    if (
        first is KvTypeConfidence.POSSIBLE
        or second is KvTypeConfidence.POSSIBLE
    ):
        return KvTypeConfidence.POSSIBLE

    return KvTypeConfidence.CERTAIN


def _is_numeric(value_type: ValueType) -> bool:
    if value_type.kind in {
        ValueTypeKind.INT,
        ValueTypeKind.FLOAT,
        ValueTypeKind.NUMBER,
    }:
        return True

    if value_type.kind is not ValueTypeKind.LITERAL:
        return False

    return all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        for value in value_type.literals
    )


def _is_integer(value_type: ValueType) -> bool:
    if value_type.kind is ValueTypeKind.INT:
        return True

    if value_type.kind is not ValueTypeKind.LITERAL:
        return False

    return all(
        isinstance(value, int)
        and not isinstance(value, bool)
        for value in value_type.literals
    )


def _is_string(value_type: ValueType) -> bool:
    if value_type.kind is ValueTypeKind.STRING:
        return True

    if value_type.kind is not ValueTypeKind.LITERAL:
        return False

    return all(
        isinstance(value, str)
        for value in value_type.literals
    )


def _numeric_result_type(
    first: ValueType,
    second: ValueType,
    operator: ast.operator,
) -> ValueType:
    if isinstance(operator, ast.Div):
        return FLOAT_TYPE

    if (
        _contains_float(first)
        or _contains_float(second)
    ):
        return FLOAT_TYPE

    if (
        _is_integer(first)
        and _is_integer(second)
    ):
        return INT_TYPE

    return NUMBER_TYPE


def _contains_float(value_type: ValueType) -> bool:
    if value_type.kind is ValueTypeKind.FLOAT:
        return True

    if value_type.kind is not ValueTypeKind.LITERAL:
        return False

    return any(
        isinstance(value, float)
        for value in value_type.literals
    )

