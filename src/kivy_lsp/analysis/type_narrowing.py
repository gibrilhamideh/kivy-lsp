from __future__ import annotations

import ast
from collections.abc import Mapping
from enum import StrEnum

from kivy_lsp.model.value_type import (
    NONE_TYPE,
    UNKNOWN_TYPE,
    ValueType,
    ValueTypeKind,
    literal_type,
    union_type,
)


class KvNoneNarrowing(StrEnum):
    """A fact learned about one expression from a branch condition."""

    NONE = "none"
    NON_NONE = "non_none"


type KvTypeNarrowings = Mapping[str, KvNoneNarrowing]


def branch_narrowings(
    condition: ast.expr,
    *,
    truthy: bool,
) -> dict[str, KvNoneNarrowing]:
    """Return safe None facts established by one condition branch."""
    if (
        isinstance(condition, ast.UnaryOp)
        and isinstance(condition.op, ast.Not)
    ):
        return branch_narrowings(
            condition.operand,
            truthy=not truthy,
        )

    if isinstance(condition, ast.BoolOp):
        if isinstance(condition.op, ast.And) and truthy:
            return merge_narrowings(
                *(
                    branch_narrowings(value, truthy=True)
                    for value in condition.values
                )
            )

        if isinstance(condition.op, ast.Or) and not truthy:
            return merge_narrowings(
                *(
                    branch_narrowings(value, truthy=False)
                    for value in condition.values
                )
            )

        return {}

    comparison = _none_comparison(condition)

    if comparison is not None:
        target, true_narrowing = comparison
        key = _narrowing_key(target)

        if key is None:
            return {}

        narrowing = (
            true_narrowing
            if truthy
            else _opposite(true_narrowing)
        )
        return {key: narrowing}

    if truthy:
        key = _narrowing_key(condition)

        if key is not None:
            return {key: KvNoneNarrowing.NON_NONE}

    return {}


def merge_narrowings(
    *narrowings: KvTypeNarrowings,
) -> dict[str, KvNoneNarrowing]:
    """Combine nested branch facts, preferring the nearest branch."""
    merged: dict[str, KvNoneNarrowing] = {}

    for narrowing in narrowings:
        merged.update(narrowing)

    return merged


def narrow_value_type(
    node: ast.expr,
    value_type: ValueType,
    narrowings: KvTypeNarrowings,
) -> ValueType:
    """Apply the active None fact for an inferred expression type."""
    key = _narrowing_key(node)

    if key is None:
        return value_type

    narrowing = narrowings.get(key)

    if narrowing is None:
        return value_type

    if narrowing is KvNoneNarrowing.NONE:
        return NONE_TYPE

    narrowed = _without_none(value_type)
    return narrowed or UNKNOWN_TYPE


def _none_comparison(
    condition: ast.expr,
) -> tuple[ast.expr, KvNoneNarrowing] | None:
    if not isinstance(condition, ast.Compare):
        return None

    if len(condition.ops) != 1 or len(condition.comparators) != 1:
        return None

    operator = condition.ops[0]

    if not isinstance(operator, (ast.Is, ast.IsNot)):
        return None

    right = condition.comparators[0]

    if _is_none(condition.left):
        target = right
    elif _is_none(right):
        target = condition.left
    else:
        return None

    if isinstance(operator, ast.Is):
        narrowing = KvNoneNarrowing.NONE
    else:
        narrowing = KvNoneNarrowing.NON_NONE

    return target, narrowing


def _narrowing_key(
    node: ast.expr,
) -> str | None:
    if not isinstance(
        node,
        (
            ast.Name,
            ast.Attribute,
            ast.Subscript,
        ),
    ):
        return None

    return ast.dump(
        node,
        annotate_fields=True,
        include_attributes=False,
    )


def _without_none(
    value_type: ValueType,
) -> ValueType | None:
    if value_type.kind is ValueTypeKind.NONE:
        return None

    if value_type.kind is ValueTypeKind.LITERAL:
        literals = tuple(
            literal
            for literal in value_type.literals
            if literal is not None
        )

        if not literals:
            return None

        return literal_type(*literals)

    if value_type.kind is not ValueTypeKind.UNION:
        return value_type

    members = tuple(
        narrowed
        for member in value_type.arguments
        if (narrowed := _without_none(member)) is not None
    )

    if not members:
        return None

    return union_type(*members)


def _opposite(
    narrowing: KvNoneNarrowing,
) -> KvNoneNarrowing:
    if narrowing is KvNoneNarrowing.NONE:
        return KvNoneNarrowing.NON_NONE

    return KvNoneNarrowing.NONE


def _is_none(
    node: ast.expr,
) -> bool:
    return isinstance(node, ast.Constant) and node.value is None

