# src/kivy_lsp/python/property_indexer.py

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass, replace

from kivy_lsp.model.property import (
    KivyPropertyInfo,
    KivyPropertyKind,
    default_property_info,
    property_kind_from_class_name,
)
from kivy_lsp.model.value_type import (
    UNKNOWN_TYPE,
    LiteralValue,
    ValueType,
    value_type_from_annotation,
)
from kivy_lsp.python.module import ImportBinding

_PROPERTY_ANNOTATIONS: dict[KivyPropertyKind, str] = {
    KivyPropertyKind.UNKNOWN: "Any",
    KivyPropertyKind.ALIAS: "Any",
    KivyPropertyKind.BOOLEAN: "bool",
    KivyPropertyKind.BOUNDED_NUMERIC: "int | float",
    KivyPropertyKind.COLOR: (
        "list[float] | tuple[float, ...] | str"
    ),
    KivyPropertyKind.CONFIG_PARSER: "Any",
    KivyPropertyKind.DICT: "dict[Any, Any]",
    KivyPropertyKind.LIST: "list[Any] | tuple[Any, ...]",
    KivyPropertyKind.NUMERIC: "int | float",
    KivyPropertyKind.OBJECT: "Any",
    KivyPropertyKind.OPTION: "Any",
    KivyPropertyKind.REFERENCE_LIST: (
        "list[Any] | tuple[Any, ...]"
    ),
    KivyPropertyKind.STRING: "str",
    KivyPropertyKind.VARIABLE_LIST: (
        "list[Any] | tuple[Any, ...]"
    ),
}


_PROPERTY_ANNOTATIONS_BY_CLASS = {
    "AliasProperty",
    "BooleanProperty",
    "BoundedNumericProperty",
    "ColorProperty",
    "ConfigParserProperty",
    "DictProperty",
    "ListProperty",
    "NumericProperty",
    "ObjectProperty",
    "OptionProperty",
    "ReferenceListProperty",
    "StringProperty",
    "VariableListProperty",
}


@dataclass(frozen=True, slots=True)
class IndexedKivyProperty:
    """A Kivy Property declaration extracted from Python source."""

    class_name: str
    annotation: str
    info: KivyPropertyInfo


class KivyPropertyIndexer:
    """Extract static metadata from Kivy Property constructor calls."""

    def __init__(
        self,
        imports: Iterable[ImportBinding],
    ) -> None:
        self._aliases = self._property_aliases(imports)

    def index(
        self,
        value: ast.expr | None,
        annotation: ast.expr | None,
    ) -> IndexedKivyProperty | None:
        if not isinstance(value, ast.Call):
            return None

        class_name = self._property_class_name(value.func)

        if class_name is None:
            return None

        kind = property_kind_from_class_name(class_name)

        if kind is KivyPropertyKind.UNKNOWN:
            return None

        info = self._property_info(
            value,
            kind,
        )
        annotation_text = self._annotation_text(
            annotation,
            value,
            kind,
            info,
        )

        return IndexedKivyProperty(
            class_name=class_name,
            annotation=annotation_text,
            info=info,
        )

    def _property_info(
        self,
        call: ast.Call,
        kind: KivyPropertyKind,
    ) -> KivyPropertyInfo:
        base = default_property_info(kind)
        default_node = self._default_argument(
            call,
            kind,
        )
        default_type = self._default_type(
            default_node,
        )
        options = self._property_options(
            call,
            kind,
        )
        options_reference = self._property_options_reference(
            call,
            kind,
            options,
        )
        allow_none = self._allow_none(
            call,
            default_node,
            base,
        )
        minimum = self._property_bound(
            call,
            kind,
            "min",
        )
        maximum = self._property_bound(
            call,
            kind,
            "max",
        )
        minimum_length = base.sequence_min_length
        maximum_length = base.sequence_max_length

        if kind is KivyPropertyKind.VARIABLE_LIST:
            length = self._integer_value(
                self._keyword_argument(
                    call,
                    "length",
                )
            )

            if length is not None and length >= 0:
                minimum_length = length
                maximum_length = length

        return replace(
            base,
            default_type=default_type,
            options=options,
            options_reference=options_reference,
            allow_none=allow_none,
            minimum=minimum,
            maximum=maximum,
            sequence_min_length=minimum_length,
            sequence_max_length=maximum_length,
        )

    def _annotation_text(
        self,
        annotation: ast.expr | None,
        call: ast.Call,
        kind: KivyPropertyKind,
        info: KivyPropertyInfo,
    ) -> str:
        if annotation is not None:
            return ast.unparse(annotation)

        if kind is KivyPropertyKind.OPTION:
            literal_annotation = self._literal_annotation(
                info.options,
            )

            if literal_annotation is not None:
                return literal_annotation

            default_annotation = self._infer_annotation(
                self._default_argument(
                    call,
                    kind,
                )
            )

            if (
                default_annotation is not None
                and default_annotation != "None"
            ):
                return default_annotation

        if kind is KivyPropertyKind.OBJECT:
            default_annotation = self._infer_annotation(
                self._default_argument(
                    call,
                    kind,
                )
            )

            if (
                default_annotation is not None
                and default_annotation != "None"
            ):
                return default_annotation

        return _PROPERTY_ANNOTATIONS[kind]

    def _property_class_name(
        self,
        function: ast.expr,
    ) -> str | None:
        qualified_name = self._qualified_expression(function)

        if qualified_name is None:
            return None

        alias_target = self._aliases.get(qualified_name)

        if alias_target is not None:
            return alias_target

        short_name = qualified_name.rsplit(
            ".",
            maxsplit=1,
        )[-1]

        if short_name in _PROPERTY_ANNOTATIONS_BY_CLASS:
            return short_name

        return None

    @staticmethod
    def _default_argument(
        call: ast.Call,
        kind: KivyPropertyKind,
    ) -> ast.expr | None:
        if kind in {
            KivyPropertyKind.ALIAS,
            KivyPropertyKind.REFERENCE_LIST,
        }:
            return None

        return KivyPropertyIndexer._call_argument(
            call,
            position=0,
            keyword_name="defaultvalue",
        )

    @staticmethod
    def _property_options(
        call: ast.Call,
        kind: KivyPropertyKind,
    ) -> tuple[LiteralValue, ...]:
        if kind is not KivyPropertyKind.OPTION:
            return ()

        options_node = KivyPropertyIndexer._call_argument(
            call,
            position=1,
            keyword_name="options",
        )

        return KivyPropertyIndexer._literal_values(
            options_node,
        )

    @staticmethod
    def _property_options_reference(
        call: ast.Call,
        kind: KivyPropertyKind,
        options: tuple[LiteralValue, ...],
    ) -> str | None:
        if kind is not KivyPropertyKind.OPTION or options:
            return None

        options_node = KivyPropertyIndexer._call_argument(
            call,
            position=1,
            keyword_name="options",
        )

        return KivyPropertyIndexer._qualified_expression(
            options_node,
        )

    @staticmethod
    def _allow_none(
        call: ast.Call,
        default_node: ast.expr | None,
        base: KivyPropertyInfo,
    ) -> bool:
        allow_none_node = KivyPropertyIndexer._keyword_argument(
            call,
            "allownone",
        )

        if allow_none_node is not None:
            allow_none = KivyPropertyIndexer._bool_value(
                allow_none_node,
            )

            if allow_none is None:
                return True

            return allow_none

        if KivyPropertyIndexer._is_none(default_node):
            return True

        return base.allow_none

    @staticmethod
    def _property_bound(
        call: ast.Call,
        kind: KivyPropertyKind,
        name: str,
    ) -> float | None:
        if kind is not KivyPropertyKind.BOUNDED_NUMERIC:
            return None

        value = KivyPropertyIndexer._number_value(
            KivyPropertyIndexer._keyword_argument(
                call,
                name,
            )
        )

        if value is None:
            return None

        return float(value)

    @staticmethod
    def _default_type(
        node: ast.expr | None,
    ) -> ValueType:
        annotation = KivyPropertyIndexer._infer_annotation(node)

        if annotation is None:
            return UNKNOWN_TYPE

        return value_type_from_annotation(annotation)

    @staticmethod
    def _literal_annotation(
        values: tuple[LiteralValue, ...],
    ) -> str | None:
        if not values:
            return None

        arguments = ", ".join(
            repr(value)
            for value in values
        )
        return f"Literal[{arguments}]"

    @staticmethod
    def _literal_values(
        node: ast.expr | None,
    ) -> tuple[LiteralValue, ...]:
        if not isinstance(
            node,
            (ast.List, ast.Tuple, ast.Set),
        ):
            return ()

        values: list[LiteralValue] = []

        for element in node.elts:
            is_literal, value = (
                KivyPropertyIndexer._literal_value(element)
            )

            if not is_literal:
                continue

            if any(
                type(existing) is type(value)
                and existing == value
                for existing in values
            ):
                continue

            values.append(value)

        return tuple(values)

    @staticmethod
    def _literal_value(
        node: ast.expr,
    ) -> tuple[bool, LiteralValue]:
        if isinstance(node, ast.Constant):
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

        number = KivyPropertyIndexer._number_value(node)

        if number is not None:
            return True, number

        return False, None

    @staticmethod
    def _number_value(
        node: ast.expr | None,
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

        operand = KivyPropertyIndexer._number_value(
            node.operand,
        )

        if operand is None:
            return None

        if isinstance(node.op, ast.USub):
            return -operand

        if isinstance(node.op, ast.UAdd):
            return operand

        return None

    @staticmethod
    def _integer_value(
        node: ast.expr | None,
    ) -> int | None:
        value = KivyPropertyIndexer._number_value(node)

        if not isinstance(value, int):
            return None

        return value

    @staticmethod
    def _bool_value(
        node: ast.expr | None,
    ) -> bool | None:
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, bool)
        ):
            return node.value

        return None

    @staticmethod
    def _is_none(
        node: ast.expr | None,
    ) -> bool:
        return (
            isinstance(node, ast.Constant)
            and node.value is None
        )

    @staticmethod
    def _infer_annotation(
        value: ast.expr | None,
    ) -> str | None:
        if value is None:
            return None

        if isinstance(value, ast.Constant):
            if value.value is None:
                return "None"

            if isinstance(value.value, bool):
                return "bool"

            if isinstance(value.value, int):
                return "int"

            if isinstance(value.value, float):
                return "float"

            if isinstance(value.value, str):
                return "str"

            return None

        if isinstance(value, ast.List):
            return "list[Any]"

        if isinstance(value, ast.Tuple):
            return "tuple[Any, ...]"

        if isinstance(value, ast.Set):
            return "set[Any]"

        if isinstance(value, ast.Dict):
            return "dict[Any, Any]"

        if isinstance(value, ast.Lambda):
            return "Callable[..., Any]"

        if isinstance(value, ast.Call):
            return KivyPropertyIndexer._qualified_expression(
                value.func,
            )

        return None

    @staticmethod
    def _call_argument(
        call: ast.Call,
        position: int,
        keyword_name: str,
    ) -> ast.expr | None:
        keyword_value = KivyPropertyIndexer._keyword_argument(
            call,
            keyword_name,
        )

        if keyword_value is not None:
            return keyword_value

        if position < len(call.args):
            return call.args[position]

        return None

    @staticmethod
    def _keyword_argument(
        call: ast.Call,
        name: str,
    ) -> ast.expr | None:
        for keyword in call.keywords:
            if keyword.arg == name:
                return keyword.value

        return None

    @staticmethod
    def _qualified_expression(
        node: ast.AST | None,
    ) -> str | None:
        if isinstance(node, ast.Name):
            return node.id

        if isinstance(node, ast.Attribute):
            parent = KivyPropertyIndexer._qualified_expression(
                node.value,
            )

            if parent is None:
                return None

            return f"{parent}.{node.attr}"

        return None

    @staticmethod
    def _property_aliases(
        imports: Iterable[ImportBinding],
    ) -> dict[str, str]:
        aliases = {
            class_name: class_name
            for class_name in _PROPERTY_ANNOTATIONS_BY_CLASS
        }

        for binding in imports:
            if binding.relative_level != 0:
                continue

            if binding.target_module != "kivy.properties":
                continue

            target_name = binding.target_name

            if target_name not in _PROPERTY_ANNOTATIONS_BY_CLASS:
                continue

            aliases[binding.local_name] = target_name

        return aliases
