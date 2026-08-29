# src/kivy_lsp/model/property.py

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kivy_lsp.model.value_type import (
    ANY_TYPE,
    BOOL_TYPE,
    NUMBER_TYPE,
    STRING_TYPE,
    UNKNOWN_TYPE,
    LiteralValue,
    ValueType,
    ValueTypeKind,
    union_type,
)


class KivyPropertyKind(StrEnum):
    """A supported Kivy Property implementation."""

    UNKNOWN = "unknown"
    ALIAS = "alias"
    BOOLEAN = "boolean"
    BOUNDED_NUMERIC = "bounded_numeric"
    COLOR = "color"
    CONFIG_PARSER = "config_parser"
    DICT = "dict"
    LIST = "list"
    NUMERIC = "numeric"
    OBJECT = "object"
    OPTION = "option"
    REFERENCE_LIST = "reference_list"
    STRING = "string"
    VARIABLE_LIST = "variable_list"


@dataclass(frozen=True, slots=True)
class KivyPropertyInfo:
    """Static metadata extracted from a Kivy Property declaration."""

    kind: KivyPropertyKind
    accepted_type: ValueType
    default_type: ValueType = UNKNOWN_TYPE
    item_type: ValueType = UNKNOWN_TYPE
    options: tuple[LiteralValue, ...] = ()
    options_reference: str | None = None
    allow_none: bool = False
    minimum: float | None = None
    maximum: float | None = None
    sequence_min_length: int | None = None
    sequence_max_length: int | None = None
    accepts_numeric_units: bool = False

    def __post_init__(self) -> None:
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError(
                "Property minimum cannot be greater than maximum."
            )

        if (
            self.sequence_min_length is not None
            and self.sequence_min_length < 0
        ):
            raise ValueError(
                "Property minimum sequence length cannot be negative."
            )

        if (
            self.sequence_max_length is not None
            and self.sequence_max_length < 0
        ):
            raise ValueError(
                "Property maximum sequence length cannot be negative."
            )

        if (
            self.sequence_min_length is not None
            and self.sequence_max_length is not None
            and self.sequence_min_length
            > self.sequence_max_length
        ):
            raise ValueError(
                "Property minimum sequence length cannot exceed maximum."
            )

    @property
    def has_options(self) -> bool:
        return bool(self.options)

    @property
    def has_numeric_bounds(self) -> bool:
        return (
            self.minimum is not None
            or self.maximum is not None
        )

    @property
    def has_sequence_bounds(self) -> bool:
        return (
            self.sequence_min_length is not None
            or self.sequence_max_length is not None
        )

    @property
    def accepts_none(self) -> bool:
        return (
            self.allow_none
            or self.accepted_type.accepts_none
        )

    @property
    def expected_display(self) -> str:
        display = self.accepted_type.display

        if (
            self.allow_none
            and not self.accepted_type.accepts_none
        ):
            return f"{display} | None"

        return display


_PROPERTY_KINDS: dict[str, KivyPropertyKind] = {
    "AliasProperty": KivyPropertyKind.ALIAS,
    "BooleanProperty": KivyPropertyKind.BOOLEAN,
    "BoundedNumericProperty": KivyPropertyKind.BOUNDED_NUMERIC,
    "ColorProperty": KivyPropertyKind.COLOR,
    "ConfigParserProperty": KivyPropertyKind.CONFIG_PARSER,
    "DictProperty": KivyPropertyKind.DICT,
    "ListProperty": KivyPropertyKind.LIST,
    "NumericProperty": KivyPropertyKind.NUMERIC,
    "ObjectProperty": KivyPropertyKind.OBJECT,
    "OptionProperty": KivyPropertyKind.OPTION,
    "ReferenceListProperty": KivyPropertyKind.REFERENCE_LIST,
    "StringProperty": KivyPropertyKind.STRING,
    "VariableListProperty": KivyPropertyKind.VARIABLE_LIST,
}


def property_kind_from_class_name(
    class_name: str,
) -> KivyPropertyKind:
    """Return the property kind represented by a Python class name."""
    short_name = class_name.rsplit(".", maxsplit=1)[-1]

    return _PROPERTY_KINDS.get(
        short_name,
        KivyPropertyKind.UNKNOWN,
    )


def default_property_info(
    kind: KivyPropertyKind,
) -> KivyPropertyInfo:
    """Create baseline metadata for a Kivy Property kind."""
    sequence_type = ValueType(
        ValueTypeKind.SEQUENCE,
    )
    dictionary_type = ValueType(
        ValueTypeKind.DICT,
    )
    config_parser_type = ValueType(
        ValueTypeKind.OBJECT,
        name="ConfigParser",
    )

    accepted_types = {
        KivyPropertyKind.UNKNOWN: UNKNOWN_TYPE,
        KivyPropertyKind.ALIAS: ANY_TYPE,
        KivyPropertyKind.BOOLEAN: BOOL_TYPE,
        KivyPropertyKind.BOUNDED_NUMERIC: NUMBER_TYPE,
        KivyPropertyKind.COLOR: union_type(
            sequence_type,
            STRING_TYPE,
        ),
        KivyPropertyKind.CONFIG_PARSER: config_parser_type,
        KivyPropertyKind.DICT: dictionary_type,
        KivyPropertyKind.LIST: sequence_type,
        KivyPropertyKind.NUMERIC: NUMBER_TYPE,
        KivyPropertyKind.OBJECT: ANY_TYPE,
        KivyPropertyKind.OPTION: UNKNOWN_TYPE,
        KivyPropertyKind.REFERENCE_LIST: sequence_type,
        KivyPropertyKind.STRING: STRING_TYPE,
        KivyPropertyKind.VARIABLE_LIST: union_type(NUMBER_TYPE, sequence_type),
    }

    return KivyPropertyInfo(
        kind=kind,
        accepted_type=accepted_types[kind],
        item_type=(
            NUMBER_TYPE
            if kind in {
                KivyPropertyKind.COLOR,
                KivyPropertyKind.VARIABLE_LIST,
            }
            else UNKNOWN_TYPE
        ),
        sequence_min_length=(
            3
            if kind is KivyPropertyKind.COLOR
            else None
        ),
        sequence_max_length=(
            4
            if kind is KivyPropertyKind.COLOR
            else None
        ),
        accepts_numeric_units=kind in {
            KivyPropertyKind.NUMERIC,
            KivyPropertyKind.BOUNDED_NUMERIC,
        },
    )


def property_info_from_class_name(
    class_name: str,
) -> KivyPropertyInfo:
    """Create baseline metadata from a Property class name."""
    kind = property_kind_from_class_name(class_name)
    return default_property_info(kind)
