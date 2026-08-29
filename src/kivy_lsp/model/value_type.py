# src/kivy_lsp/model/value_type.py

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum

type LiteralValue = str | int | float | bool | None


class ValueTypeKind(StrEnum):
    """A statically inferred value category."""

    UNKNOWN = "unknown"
    ANY = "any"
    NONE = "none"
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    NUMBER = "number"
    STRING = "string"
    LIST = "list"
    TUPLE = "tuple"
    SEQUENCE = "sequence"
    DICT = "dict"
    SET = "set"
    CALLABLE = "callable"
    OBJECT = "object"
    LITERAL = "literal"
    UNION = "union"


@dataclass(frozen=True, slots=True)
class ValueType:
    """An editor-neutral static value type."""

    kind: ValueTypeKind
    name: str | None = None
    arguments: tuple[ValueType, ...] = ()
    literals: tuple[LiteralValue, ...] = ()

    def __post_init__(self) -> None:
        if self.kind is ValueTypeKind.OBJECT and not self.name:
            raise ValueError(
                "Object value types require a name"
            )

        if self.kind is not ValueTypeKind.OBJECT and self.name is not None:
            raise ValueError(
                "Only object value types may have a name"
            )

        if self.kind is ValueTypeKind.LITERAL and not self.literals:
            raise ValueError(
                "Literal value types require at least one value"
            )

        if self.kind is not ValueTypeKind.LITERAL and self.literals:
            raise ValueError(
                "Only literal value types may contain literal values"
            )

        if (
            self.kind is ValueTypeKind.UNION
            and len(self.arguments) < 2
        ):
            raise ValueError(
                "Union value types require at least two members"
    )

    @property
    def is_unknown(self) -> bool:
        return self.kind is ValueTypeKind.UNKNOWN

    @property
    def is_any(self) -> bool:
        return self.kind is ValueTypeKind.ANY

    @property
    def is_numeric(self) -> bool:
        if self.kind in {
            ValueTypeKind.INT,
            ValueTypeKind.FLOAT,
            ValueTypeKind.NUMBER,
        }:
            return True

        if self.kind is not ValueTypeKind.LITERAL:
            return False

        return all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            for value in self.literals
        )

    @property
    def accepts_none(self) -> bool:
        if self.kind in {
            ValueTypeKind.UNKNOWN,
            ValueTypeKind.ANY,
            ValueTypeKind.NONE,
        }:
            return True

        if self.kind is ValueTypeKind.LITERAL:
            return None in self.literals

        if self.kind is ValueTypeKind.UNION:
            return any(
                argument.accepts_none
                for argument in self.arguments
            )

        return False

    @property
    def display(self) -> str:
        if self.kind is ValueTypeKind.UNKNOWN:
            return "unknown"

        if self.kind is ValueTypeKind.ANY:
            return "Any"

        if self.kind is ValueTypeKind.NONE:
            return "None"

        if self.kind is ValueTypeKind.BOOL:
            return "bool"

        if self.kind is ValueTypeKind.INT:
            return "int"

        if self.kind is ValueTypeKind.FLOAT:
            return "float"

        if self.kind is ValueTypeKind.NUMBER:
            return "number"

        if self.kind is ValueTypeKind.STRING:
            return "str"

        if self.kind is ValueTypeKind.CALLABLE:
            return "Callable"

        if self.kind is ValueTypeKind.OBJECT:
            name = self.name or "object"

            if not self.arguments:
                return name

            arguments = ", ".join(
                argument.display
                for argument in self.arguments
            )
            return f"{name}[{arguments}]"

        if self.kind is ValueTypeKind.LITERAL:
            values = ", ".join(
                repr(value)
                for value in self.literals
            )
            return f"Literal[{values}]"

        if self.kind is ValueTypeKind.UNION:
            return " | ".join(
                argument.display
                for argument in self.arguments
            )

        container_name = {
            ValueTypeKind.LIST: "list",
            ValueTypeKind.TUPLE: "tuple",
            ValueTypeKind.SEQUENCE: "Sequence",
            ValueTypeKind.DICT: "dict",
            ValueTypeKind.SET: "set",
        }.get(self.kind)

        if container_name is None:
            return self.kind.value

        if not self.arguments:
            return container_name

        arguments = ", ".join(
            argument.display
            for argument in self.arguments
        )
        return f"{container_name}[{arguments}]"


UNKNOWN_TYPE = ValueType(
    ValueTypeKind.UNKNOWN,
)
ANY_TYPE = ValueType(
    ValueTypeKind.ANY,
)
NONE_TYPE = ValueType(
    ValueTypeKind.NONE,
)
BOOL_TYPE = ValueType(
    ValueTypeKind.BOOL,
)
INT_TYPE = ValueType(
    ValueTypeKind.INT,
)
FLOAT_TYPE = ValueType(
    ValueTypeKind.FLOAT,
)
NUMBER_TYPE = ValueType(
    ValueTypeKind.NUMBER,
)
STRING_TYPE = ValueType(
    ValueTypeKind.STRING,
)
CALLABLE_TYPE = ValueType(
    ValueTypeKind.CALLABLE,
)


def object_type(
    name: str,
    *arguments: ValueType,
) -> ValueType:
    """Create a named object type."""

    if not name:
        return UNKNOWN_TYPE

    return ValueType(
        kind=ValueTypeKind.OBJECT,
        name=name,
        arguments=arguments,
    )


def literal_type(
    *values: LiteralValue,
) -> ValueType:
    """Create a type containing one or more literal values."""

    unique: list[LiteralValue] = []
    seen: set[tuple[type[object], LiteralValue]] = set()

    for value in values:
        key = (
            type(value),
            value,
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(value)

    if not unique:
        return UNKNOWN_TYPE

    return ValueType(
        kind=ValueTypeKind.LITERAL,
        literals=tuple(unique),
    )


def union_type(
    *value_types: ValueType,
) -> ValueType:
    """Create a flattened union without duplicate members."""

    flattened: list[ValueType] = []

    for value_type in value_types:
        if value_type.kind is ValueTypeKind.ANY:
            return ANY_TYPE

        if value_type.kind is ValueTypeKind.UNION:
            candidates = value_type.arguments
        else:
            candidates = (value_type,)

        for candidate in candidates:
            if candidate not in flattened:
                flattened.append(candidate)

    if not flattened:
        return UNKNOWN_TYPE

    if len(flattened) == 1:
        return flattened[0]

    return ValueType(
        kind=ValueTypeKind.UNION,
        arguments=tuple(flattened),
    )


def value_type_from_annotation(
    annotation: str | None,
) -> ValueType:
    """Parse a Python annotation without importing its module."""

    if annotation is None or not annotation.strip():
        return UNKNOWN_TYPE

    try:
        expression = ast.parse(
            annotation,
            mode="eval",
        )
    except SyntaxError:
        return UNKNOWN_TYPE

    return _annotation_node_type(
        expression.body,
    )


def _annotation_node_type(
    node: ast.expr,
) -> ValueType:
    if isinstance(node, ast.Constant):
        if node.value is None:
            return NONE_TYPE

        if isinstance(node.value, str):
            return value_type_from_annotation(
                node.value,
            )

        return UNKNOWN_TYPE

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return union_type(
            _annotation_node_type(node.left),
            _annotation_node_type(node.right),
        )

    if isinstance(node, ast.Subscript):
        return _subscript_type(node)

    name = _qualified_expression(node)

    if name is None:
        return UNKNOWN_TYPE

    return _named_type(name)


def _subscript_type(
    node: ast.Subscript,
) -> ValueType:
    name = _qualified_expression(node.value)

    if name is None:
        return UNKNOWN_TYPE

    short_name = name.rsplit(".", 1)[-1]
    arguments = _subscript_arguments(node)

    if short_name == "Annotated":
        if not arguments:
            return UNKNOWN_TYPE

        return _annotation_node_type(arguments[0])

    if short_name == "Optional":
        if not arguments:
            return UNKNOWN_TYPE

        return union_type(
            _annotation_node_type(arguments[0]),
            NONE_TYPE,
        )

    if short_name == "Union":
        return union_type(
            *(
                _annotation_node_type(argument)
                for argument in arguments
            ),
        )

    if short_name == "Literal":
        values: list[LiteralValue] = []

        for argument in arguments:
            success, value = _literal_value(argument)

            if success:
                values.append(value)

        return literal_type(*values)

    argument_types = tuple(
        _annotation_node_type(argument)
        for argument in arguments
        if not _is_ellipsis(argument)
    )

    container_kind = {
        "list": ValueTypeKind.LIST,
        "List": ValueTypeKind.LIST,
        "tuple": ValueTypeKind.TUPLE,
        "Tuple": ValueTypeKind.TUPLE,
        "Sequence": ValueTypeKind.SEQUENCE,
        "MutableSequence": ValueTypeKind.SEQUENCE,
        "dict": ValueTypeKind.DICT,
        "Dict": ValueTypeKind.DICT,
        "Mapping": ValueTypeKind.DICT,
        "MutableMapping": ValueTypeKind.DICT,
        "set": ValueTypeKind.SET,
        "Set": ValueTypeKind.SET,
        "frozenset": ValueTypeKind.SET,
        "FrozenSet": ValueTypeKind.SET,
    }.get(short_name)

    if container_kind is not None:
        return ValueType(
            kind=container_kind,
            arguments=argument_types,
        )

    if short_name in {
        "Callable",
        "Protocol",
    }:
        return CALLABLE_TYPE

    return object_type(name, *argument_types)

def _named_type(name: str) -> ValueType:
    short_name = name.rsplit(".", 1)[-1]

    if short_name == "Any":
        return ANY_TYPE

    if short_name in {
        "None",
        "NoneType",
    }:
        return NONE_TYPE

    if short_name == "bool":
        return BOOL_TYPE

    if short_name == "int":
        return INT_TYPE

    if short_name == "float":
        return FLOAT_TYPE

    if short_name in {
        "Number",
        "Real",
    }:
        return NUMBER_TYPE

    if short_name == "str":
        return STRING_TYPE

    if short_name in {
        "Callable",
        "function",
    }:
        return CALLABLE_TYPE

    return object_type(name)


def _subscript_arguments(
    node: ast.Subscript,
) -> tuple[ast.expr, ...]:
    if isinstance(node.slice, ast.Tuple):
        return tuple(node.slice.elts)

    return (node.slice,)


def _qualified_expression(
    node: ast.AST,
) -> str | None:
    if isinstance(node, ast.Name):
        return node.id

    if isinstance(node, ast.Attribute):
        parent = _qualified_expression(
            node.value,
        )

        if parent is None:
            return None

        return f"{parent}.{node.attr}"

    return None


def _literal_value(
    node: ast.expr,
) -> tuple[bool, LiteralValue]:
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError):
        return False, None

    if value is None:
        return True, None

    if isinstance(value, (str, int, float, bool)):
        return True, value

    return False, None


def _is_ellipsis(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and node.value is Ellipsis
    )
