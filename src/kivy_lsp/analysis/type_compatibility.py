# src/kivy_lsp/analysis/type_compatibility.py

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from kivy_lsp.analysis.value_inference import (
    KvInferredValue,
    KvTypeConfidence,
)
from kivy_lsp.model.property import KivyPropertyInfo
from kivy_lsp.model.value_type import (
    NONE_TYPE,
    LiteralValue,
    ValueType,
    ValueTypeKind,
    union_type,
)

_NUMERIC_UNIT_PATTERN = re.compile(
    r"""
    ^[+-]?
    (?:
        \d+(?:\.\d*)?
        |
        \.\d+
    )
    (?:px|in|cm|mm|pt|dp|sp)$
    """,
    re.VERBOSE,
)


class TypeCompatibility(StrEnum):
    """The result of comparing an expression with a property type."""

    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    """A type comparison result independent of LSP diagnostics."""

    compatibility: TypeCompatibility
    expected: str
    actual: str
    reason: str | None = None

    @property
    def is_compatible(self) -> bool:
        return self.compatibility is TypeCompatibility.COMPATIBLE

    @property
    def is_incompatible(self) -> bool:
        return self.compatibility is TypeCompatibility.INCOMPATIBLE

    @property
    def is_possible(self) -> bool:
        return self.compatibility is TypeCompatibility.POSSIBLE

    @property
    def is_unknown(self) -> bool:
        return self.compatibility is TypeCompatibility.UNKNOWN


class KivyPropertyTypeChecker:
    """Compare inferred KV values with Kivy property constraints."""

    def check(
        self,
        property_info: KivyPropertyInfo,
        value: KvInferredValue,
        *,
        sequence_length: int | None = None,
    ) -> CompatibilityResult:
        expected = _expected_display(property_info)
        actual = _actual_display(value)

        if value.confidence is KvTypeConfidence.UNKNOWN:
            return CompatibilityResult(
                compatibility=TypeCompatibility.UNKNOWN,
                expected=expected,
                actual=actual,
                reason="The expression type could not be determined.",
            )

        none_result = self._check_none(
            property_info,
            value,
            expected,
            actual,
        )

        if none_result is not None:
            return none_result

        numeric_unit_result = self._check_numeric_unit(
            property_info,
            value,
            expected,
            actual,
        )

        if numeric_unit_result is not None:
            return numeric_unit_result

        compatibility = _type_compatibility(
            _accepted_type(property_info),
            value.value_type,
        )

        if compatibility is TypeCompatibility.UNKNOWN:
            return CompatibilityResult(
                compatibility=TypeCompatibility.UNKNOWN,
                expected=expected,
                actual=actual,
                reason="The property or expression type is unknown.",
            )

        if compatibility is TypeCompatibility.INCOMPATIBLE:
            if value.confidence is KvTypeConfidence.POSSIBLE:
                return CompatibilityResult(
                    compatibility=TypeCompatibility.POSSIBLE,
                    expected=expected,
                    actual=actual,
                    reason=(
                        "The expression may not produce a compatible "
                        "value."
                    ),
                )

            return CompatibilityResult(
                compatibility=TypeCompatibility.INCOMPATIBLE,
                expected=expected,
                actual=actual,
                reason=(
                    f"Expected {expected}, but received {actual}."
                ),
            )

        option_result = self._check_options(
            property_info,
            value,
            expected,
            actual,
        )

        if option_result is not None:
            return option_result

        bounds_result = self._check_numeric_bounds(
            property_info,
            value,
            expected,
            actual,
        )

        if bounds_result is not None:
            return bounds_result

        length_result = self._check_sequence_length(
            property_info,
            sequence_length,
            expected,
            actual,
        )

        if length_result is not None:
            return length_result

        if compatibility is TypeCompatibility.POSSIBLE:
            return CompatibilityResult(
                compatibility=TypeCompatibility.POSSIBLE,
                expected=expected,
                actual=actual,
                reason=(
                    "The expression may produce an incompatible value."
                ),
            )

        return CompatibilityResult(
            compatibility=TypeCompatibility.COMPATIBLE,
            expected=expected,
            actual=actual,
        )

    def _check_none(
        self,
        property_info: KivyPropertyInfo,
        value: KvInferredValue,
        expected: str,
        actual: str,
    ) -> CompatibilityResult | None:
        is_none_literal = (
            value.literal_known
            and value.literal is None
        )
        is_none_type = (
            value.value_type.kind is ValueTypeKind.NONE
        )

        if not is_none_literal and not is_none_type:
            return None

        if _accepts_none(property_info):
            return CompatibilityResult(
                compatibility=TypeCompatibility.COMPATIBLE,
                expected=expected,
                actual=actual,
            )

        return CompatibilityResult(
            compatibility=TypeCompatibility.INCOMPATIBLE,
            expected=expected,
            actual=actual,
            reason="This property does not allow None.",
        )

    def _check_numeric_unit(
        self,
        property_info: KivyPropertyInfo,
        value: KvInferredValue,
        expected: str,
        actual: str,
    ) -> CompatibilityResult | None:
        if not property_info.accepts_numeric_units:
            return None

        if value.value_type.kind is not ValueTypeKind.STRING:
            return None

        if not value.literal_known:
            return CompatibilityResult(
                compatibility=TypeCompatibility.POSSIBLE,
                expected=expected,
                actual=actual,
                reason=(
                    "The string must contain a valid Kivy numeric unit."
                ),
            )

        literal = value.literal

        if not isinstance(literal, str):
            return None

        if _NUMERIC_UNIT_PATTERN.fullmatch(literal):
            return CompatibilityResult(
                compatibility=TypeCompatibility.COMPATIBLE,
                expected=expected,
                actual=actual,
            )

        return CompatibilityResult(
            compatibility=TypeCompatibility.INCOMPATIBLE,
            expected=expected,
            actual=actual,
            reason=(
                f"{literal!r} is not a valid Kivy numeric unit value."
            ),
        )

    def _check_options(
        self,
        property_info: KivyPropertyInfo,
        value: KvInferredValue,
        expected: str,
        actual: str,
    ) -> CompatibilityResult | None:
        options = property_info.options

        if not options:
            return None

        if value.literal_known:
            if _literal_in_options(
                value.literal,
                options,
            ):
                return CompatibilityResult(
                    compatibility=TypeCompatibility.COMPATIBLE,
                    expected=expected,
                    actual=actual,
                )

            return CompatibilityResult(
                compatibility=TypeCompatibility.INCOMPATIBLE,
                expected=expected,
                actual=actual,
                reason=(
                    f"{value.literal!r} is not one of the allowed "
                    f"values: {_options_display(options)}."
                ),
            )

        literals = value.value_type.literals

        if literals:
            allowed_count = sum(
                _literal_in_options(literal, options)
                for literal in literals
            )

            if allowed_count == len(literals):
                return CompatibilityResult(
                    compatibility=TypeCompatibility.COMPATIBLE,
                    expected=expected,
                    actual=actual,
                )

            if allowed_count == 0:
                return CompatibilityResult(
                    compatibility=TypeCompatibility.INCOMPATIBLE,
                    expected=expected,
                    actual=actual,
                    reason=(
                        "None of the expression's possible values are "
                        "allowed by this property."
                    ),
                )

            return CompatibilityResult(
                compatibility=TypeCompatibility.POSSIBLE,
                expected=expected,
                actual=actual,
                reason=(
                    "Some possible expression values are not allowed "
                    "by this property."
                ),
            )

        return CompatibilityResult(
            compatibility=TypeCompatibility.POSSIBLE,
            expected=expected,
            actual=actual,
            reason=(
                "The value cannot be proven to be one of the allowed "
                f"options: {_options_display(options)}."
            ),
        )

    def _check_numeric_bounds(
        self,
        property_info: KivyPropertyInfo,
        value: KvInferredValue,
        expected: str,
        actual: str,
    ) -> CompatibilityResult | None:
        if not value.literal_known:
            return None

        literal = value.literal

        if isinstance(literal, bool):
            return None

        if not isinstance(literal, (int, float)):
            return None

        minimum = property_info.minimum
        maximum = property_info.maximum

        if minimum is not None and literal < minimum:
            return CompatibilityResult(
                compatibility=TypeCompatibility.INCOMPATIBLE,
                expected=expected,
                actual=actual,
                reason=(
                    f"{literal!r} is below the minimum value "
                    f"{minimum!r}."
                ),
            )

        if maximum is not None and literal > maximum:
            return CompatibilityResult(
                compatibility=TypeCompatibility.INCOMPATIBLE,
                expected=expected,
                actual=actual,
                reason=(
                    f"{literal!r} is above the maximum value "
                    f"{maximum!r}."
                ),
            )

        return None

    def _check_sequence_length(
        self,
        property_info: KivyPropertyInfo,
        sequence_length: int | None,
        expected: str,
        actual: str,
    ) -> CompatibilityResult | None:
        if sequence_length is None:
            return None

        minimum = property_info.sequence_min_length
        maximum = property_info.sequence_max_length

        if minimum is not None and sequence_length < minimum:
            return CompatibilityResult(
                compatibility=TypeCompatibility.INCOMPATIBLE,
                expected=expected,
                actual=actual,
                reason=(
                    f"The sequence requires at least {minimum} items, "
                    f"but received {sequence_length}."
                ),
            )

        if maximum is not None and sequence_length > maximum:
            return CompatibilityResult(
                compatibility=TypeCompatibility.INCOMPATIBLE,
                expected=expected,
                actual=actual,
                reason=(
                    f"The sequence allows at most {maximum} items, "
                    f"but received {sequence_length}."
                ),
            )

        return None


def _type_compatibility(
    expected: ValueType,
    actual: ValueType,
) -> TypeCompatibility:
    if expected.kind in {
        ValueTypeKind.ANY,
        ValueTypeKind.UNKNOWN,
    }:
        return TypeCompatibility.UNKNOWN

    if actual.kind in {
        ValueTypeKind.ANY,
        ValueTypeKind.UNKNOWN,
    }:
        return TypeCompatibility.UNKNOWN

    if actual.kind is ValueTypeKind.UNION:
        results = tuple(
            _type_compatibility(expected, argument)
            for argument in actual.arguments
        )
        return _combine_actual_union(results)

    if expected.kind is ValueTypeKind.UNION:
        results = tuple(
            _type_compatibility(argument, actual)
            for argument in expected.arguments
        )
        return _combine_expected_union(results)

    if expected.kind is ValueTypeKind.LITERAL:
        return _literal_type_compatibility(
            expected,
            actual,
        )

    if actual.kind is ValueTypeKind.LITERAL:
        return _actual_literal_compatibility(
            expected,
            actual,
        )

    if expected.kind is actual.kind:
        if expected.kind is ValueTypeKind.OBJECT:
            return _object_compatibility(
                expected,
                actual,
            )

        return TypeCompatibility.COMPATIBLE

    if expected.kind is ValueTypeKind.NUMBER and actual.kind in {
        ValueTypeKind.INT,
        ValueTypeKind.FLOAT,
        ValueTypeKind.NUMBER,
    }:
        return TypeCompatibility.COMPATIBLE

    if expected.kind is ValueTypeKind.FLOAT and actual.kind in {
        ValueTypeKind.INT,
        ValueTypeKind.FLOAT,
    }:
        return TypeCompatibility.COMPATIBLE

    if expected.kind is ValueTypeKind.SEQUENCE and actual.kind in {
        ValueTypeKind.LIST,
        ValueTypeKind.TUPLE,
        ValueTypeKind.SEQUENCE,
    }:
        return TypeCompatibility.COMPATIBLE

    return TypeCompatibility.INCOMPATIBLE


def _object_compatibility(
    expected: ValueType,
    actual: ValueType,
) -> TypeCompatibility:
    if expected.name is None or actual.name is None:
        return TypeCompatibility.POSSIBLE

    if expected.name == actual.name:
        return TypeCompatibility.COMPATIBLE

    expected_short_name = expected.name.rsplit(".", 1)[-1]
    actual_short_name = actual.name.rsplit(".", 1)[-1]

    if expected_short_name == actual_short_name:
        return TypeCompatibility.COMPATIBLE

    return TypeCompatibility.POSSIBLE


def _literal_type_compatibility(
    expected: ValueType,
    actual: ValueType,
) -> TypeCompatibility:
    expected_literals = expected.literals

    if not expected_literals:
        return TypeCompatibility.UNKNOWN

    if actual.kind is ValueTypeKind.LITERAL:
        actual_literals = actual.literals

        if not actual_literals:
            return TypeCompatibility.UNKNOWN

        matches = sum(
            _literal_in_options(literal, expected_literals)
            for literal in actual_literals
        )

        if matches == len(actual_literals):
            return TypeCompatibility.COMPATIBLE

        if matches == 0:
            return TypeCompatibility.INCOMPATIBLE

        return TypeCompatibility.POSSIBLE

    literal_types = tuple(
        _literal_value_kind(literal)
        for literal in expected_literals
    )

    if actual.kind in literal_types:
        return TypeCompatibility.POSSIBLE

    return TypeCompatibility.INCOMPATIBLE


def _actual_literal_compatibility(
    expected: ValueType,
    actual: ValueType,
) -> TypeCompatibility:
    literals = actual.literals

    if not literals:
        return TypeCompatibility.UNKNOWN

    results = tuple(
        _kind_compatibility(
            expected.kind,
            _literal_value_kind(literal),
        )
        for literal in literals
    )

    return _combine_actual_union(results)


def _kind_compatibility(
    expected: ValueTypeKind,
    actual: ValueTypeKind,
) -> TypeCompatibility:
    if expected is actual:
        return TypeCompatibility.COMPATIBLE

    if expected is ValueTypeKind.NUMBER and actual in {
        ValueTypeKind.INT,
        ValueTypeKind.FLOAT,
        ValueTypeKind.NUMBER,
    }:
        return TypeCompatibility.COMPATIBLE

    if expected is ValueTypeKind.FLOAT and actual in {
        ValueTypeKind.INT,
        ValueTypeKind.FLOAT,
    }:
        return TypeCompatibility.COMPATIBLE

    if expected is ValueTypeKind.SEQUENCE and actual in {
        ValueTypeKind.LIST,
        ValueTypeKind.TUPLE,
        ValueTypeKind.SEQUENCE,
    }:
        return TypeCompatibility.COMPATIBLE

    return TypeCompatibility.INCOMPATIBLE


def _combine_actual_union(
    results: tuple[TypeCompatibility, ...],
) -> TypeCompatibility:
    if not results:
        return TypeCompatibility.UNKNOWN

    if all(
        result is TypeCompatibility.COMPATIBLE
        for result in results
    ):
        return TypeCompatibility.COMPATIBLE

    if all(
        result is TypeCompatibility.INCOMPATIBLE
        for result in results
    ):
        return TypeCompatibility.INCOMPATIBLE

    if all(
        result is TypeCompatibility.UNKNOWN
        for result in results
    ):
        return TypeCompatibility.UNKNOWN

    return TypeCompatibility.POSSIBLE


def _combine_expected_union(
    results: tuple[TypeCompatibility, ...],
) -> TypeCompatibility:
    if not results:
        return TypeCompatibility.UNKNOWN

    if TypeCompatibility.COMPATIBLE in results:
        return TypeCompatibility.COMPATIBLE

    if TypeCompatibility.POSSIBLE in results:
        return TypeCompatibility.POSSIBLE

    if TypeCompatibility.UNKNOWN in results:
        return TypeCompatibility.UNKNOWN

    return TypeCompatibility.INCOMPATIBLE


def _accepts_none(
    property_info: KivyPropertyInfo,
) -> bool:
    if property_info.allow_none:
        return True

    if _value_type_accepts_none(
        property_info.accepted_type,
    ):
        return True

    return _literal_in_options(
        None,
        property_info.options,
    )


def _accepted_type(
    property_info: KivyPropertyInfo,
) -> ValueType:
    accepted_type = property_info.accepted_type

    if not _accepts_none(property_info):
        return accepted_type

    if _value_type_accepts_none(accepted_type):
        return accepted_type

    return union_type(
        accepted_type,
        NONE_TYPE,
    )


def _value_type_accepts_none(
    value_type: ValueType,
) -> bool:
    if value_type.kind is ValueTypeKind.NONE:
        return True

    if value_type.kind is ValueTypeKind.UNION:
        return any(
            _value_type_accepts_none(argument)
            for argument in value_type.arguments
        )

    if value_type.kind is ValueTypeKind.LITERAL:
        return any(
            literal is None
            for literal in value_type.literals
        )

    return False


def _literal_in_options(
    literal: LiteralValue,
    options: tuple[LiteralValue, ...],
) -> bool:
    return any(
        _same_literal(literal, option)
        for option in options
    )


def _same_literal(
    left: LiteralValue,
    right: LiteralValue,
) -> bool:
    return type(left) is type(right) and left == right


def _literal_value_kind(
    literal: LiteralValue,
) -> ValueTypeKind:
    if literal is None:
        return ValueTypeKind.NONE

    if isinstance(literal, bool):
        return ValueTypeKind.BOOL

    if isinstance(literal, int):
        return ValueTypeKind.INT

    if isinstance(literal, float):
        return ValueTypeKind.FLOAT

    return ValueTypeKind.STRING


def _expected_display(
    property_info: KivyPropertyInfo,
) -> str:
    value = _value_type_display(
        property_info.accepted_type,
    )

    if property_info.allow_none and "None" not in value:
        value = f"{value} | None"

    if property_info.options:
        return _options_display(
            property_info.options,
        )

    return value


def _actual_display(
    value: KvInferredValue,
) -> str:
    if value.literal_known:
        return repr(value.literal)

    return _value_type_display(
        value.value_type,
    )


def _value_type_display(
    value_type: ValueType,
) -> str:
    kind = value_type.kind

    if kind is ValueTypeKind.UNKNOWN:
        return "unknown"

    if kind is ValueTypeKind.ANY:
        return "Any"

    if kind is ValueTypeKind.NONE:
        return "None"

    if kind is ValueTypeKind.BOOL:
        return "bool"

    if kind is ValueTypeKind.INT:
        return "int"

    if kind is ValueTypeKind.FLOAT:
        return "float"

    if kind is ValueTypeKind.NUMBER:
        return "int | float"

    if kind is ValueTypeKind.STRING:
        return "str"

    if kind is ValueTypeKind.LIST:
        return "list"

    if kind is ValueTypeKind.TUPLE:
        return "tuple"

    if kind is ValueTypeKind.SEQUENCE:
        return "list | tuple"

    if kind is ValueTypeKind.DICT:
        return "dict"

    if kind is ValueTypeKind.SET:
        return "set"

    if kind is ValueTypeKind.CALLABLE:
        return "callable"

    if kind is ValueTypeKind.OBJECT:
        return value_type.name or "object"

    if kind is ValueTypeKind.LITERAL:
        return _options_display(
            value_type.literals,
        )

    if kind is ValueTypeKind.UNION:
        return " | ".join(
            _value_type_display(argument)
            for argument in value_type.arguments
        )

    return kind.value


def _options_display(
    options: tuple[LiteralValue, ...],
) -> str:
    return " | ".join(
        repr(option)
        for option in options
    )
