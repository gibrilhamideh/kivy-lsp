# src/kivy_lsp/analysis/property_value_completion.py

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from kivy_lsp.model.property import KivyPropertyInfo
from kivy_lsp.model.value_type import (
    LiteralValue,
    ValueType,
    ValueTypeKind,
)


class KvPropertyValueKind(StrEnum):
    """The semantic kind of a property-value suggestion."""

    OPTION = "option"
    BOOLEAN = "boolean"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class KvPropertyValueSuggestion:
    """A completion choice for a Kivy property value."""

    label: str
    insert_text: str
    kind: KvPropertyValueKind
    sort_text: str
    detail: str


class KvPropertyValueCompleter:
    """Produce values accepted by a Kivy property."""

    def complete(
        self,
        property_info: KivyPropertyInfo,
        prefix: str,
    ) -> tuple[KvPropertyValueSuggestion, ...]:
        suggestions: list[KvPropertyValueSuggestion] = []

        suggestions.extend(
            self._option_suggestions(
                property_info,
                prefix,
            )
        )

        if _accepts_kind(
            property_info.accepted_type,
            ValueTypeKind.BOOL,
        ):
            suggestions.extend(
                self._boolean_suggestions(prefix)
            )

        if _accepts_none(property_info):
            none_suggestion = KvPropertyValueSuggestion(
                label="None",
                insert_text="None",
                kind=KvPropertyValueKind.NONE,
                sort_text="20:none",
                detail="This property allows None",
            )

            if _matches_prefix(
                none_suggestion,
                prefix,
            ):
                suggestions.append(none_suggestion)

        return _deduplicate_and_sort(suggestions)

    def _option_suggestions(
        self,
        property_info: KivyPropertyInfo,
        prefix: str,
    ) -> list[KvPropertyValueSuggestion]:
        suggestions: list[KvPropertyValueSuggestion] = []

        for index, option in enumerate(property_info.options):
            source = _literal_source(option)
            suggestion = KvPropertyValueSuggestion(
                label=source,
                insert_text=source,
                kind=KvPropertyValueKind.OPTION,
                sort_text=f"00:{index:04d}:{source.casefold()}",
                detail=_option_detail(option),
            )

            if _matches_prefix(suggestion, prefix):
                suggestions.append(suggestion)

        return suggestions

    def _boolean_suggestions(
        self,
        prefix: str,
    ) -> list[KvPropertyValueSuggestion]:
        suggestions: list[KvPropertyValueSuggestion] = []

        for index, value in enumerate((True, False)):
            source = str(value)
            suggestion = KvPropertyValueSuggestion(
                label=source,
                insert_text=source,
                kind=KvPropertyValueKind.BOOLEAN,
                sort_text=f"10:{index:04d}:{source.casefold()}",
                detail="Boolean property value",
            )

            if _matches_prefix(suggestion, prefix):
                suggestions.append(suggestion)

        return suggestions


def _accepts_none(
    property_info: KivyPropertyInfo,
) -> bool:
    if property_info.allow_none:
        return True

    if _accepts_kind(
        property_info.accepted_type,
        ValueTypeKind.NONE,
    ):
        return True

    return any(
        option is None
        for option in property_info.options
    )


def _accepts_kind(
    value_type: ValueType,
    kind: ValueTypeKind,
) -> bool:
    if value_type.kind is kind:
        return True

    if value_type.kind is ValueTypeKind.UNION:
        return any(
            _accepts_kind(argument, kind)
            for argument in value_type.arguments
        )

    if value_type.kind is ValueTypeKind.LITERAL:
        return any(
            _literal_kind(literal) is kind
            for literal in value_type.literals
        )

    return False


def _literal_kind(
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


def _literal_source(
    literal: LiteralValue,
) -> str:
    if literal is None:
        return "None"

    if isinstance(literal, bool):
        return str(literal)

    if isinstance(literal, str):
        return json.dumps(
            literal,
            ensure_ascii=False,
        )

    return repr(literal)


def _option_detail(
    option: LiteralValue,
) -> str:
    if option is None:
        type_name = "None"
    elif isinstance(option, bool):
        type_name = "bool"
    elif isinstance(option, int):
        type_name = "int"
    elif isinstance(option, float):
        type_name = "float"
    else:
        type_name = "str"

    return f"Allowed OptionProperty value ({type_name})"


def _matches_prefix(
    suggestion: KvPropertyValueSuggestion,
    prefix: str,
) -> bool:
    normalized_prefix = prefix.strip().casefold()

    if not normalized_prefix:
        return True

    source = suggestion.insert_text.casefold()

    if source.startswith(normalized_prefix):
        return True

    if suggestion.kind is not KvPropertyValueKind.OPTION:
        return False

    unquoted_prefix = normalized_prefix.lstrip("\"'")
    unquoted_source = source.lstrip("\"'")

    return unquoted_source.startswith(unquoted_prefix)


def _deduplicate_and_sort(
    suggestions: list[KvPropertyValueSuggestion],
) -> tuple[KvPropertyValueSuggestion, ...]:
    unique: dict[str, KvPropertyValueSuggestion] = {}

    for suggestion in suggestions:
        unique.setdefault(
            suggestion.insert_text,
            suggestion,
        )

    return tuple(
        sorted(
            unique.values(),
            key=lambda suggestion: (
                suggestion.sort_text,
                suggestion.label.casefold(),
            ),
        )
    )
