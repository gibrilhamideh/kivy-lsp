from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from kivy_lsp.analysis.expression import (
    KvExpressionResolution,
    KvExpressionResolver,
    KvResolutionKind,
)
from kivy_lsp.analysis.i18n import (
    translation_key_target_at,
    translation_parameter_target_at,
)
from kivy_lsp.analysis.python_ids_completion import (
    enclosing_class_name,
)
from kivy_lsp.analysis.scope import (
    KvBinding,
    KvScope,
    KvSemanticModel,
    KvValue,
)
from kivy_lsp.analysis.widget_resolution import resolve_widget_class
from kivy_lsp.config import ServerConfig
from kivy_lsp.i18n.index import TranslationIndex
from kivy_lsp.kv.context import context_at
from kivy_lsp.kv.index import KvIdSymbol, KvIndex
from kivy_lsp.kv.nodes import (
    BodyNode,
    DocumentNode,
    PropertyNode,
    RuleNode,
    WidgetNode,
)
from kivy_lsp.kv.parser import ParseResult
from kivy_lsp.model.span import Span
from kivy_lsp.model.symbol import (
    ClassSymbol,
    SymbolLocation,
)
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.workspace.document import TextDocument

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"

_PYTHON_DOT_IDS_PATTERN = re.compile(
    rf"\bself\s*\.\s*ids\s*\.\s*"
    rf"(?P<id>{_IDENTIFIER})"
    rf"(?:\s*\.\s*(?P<member>{_IDENTIFIER}))?"
)

_PYTHON_SUBSCRIPT_IDS_PATTERN = re.compile(
    rf"\bself\s*\.\s*ids\s*\[\s*"
    rf"(?P<quote>['\"])(?P<id>{_IDENTIFIER})(?P=quote)"
    rf"\s*\]"
    rf"(?:\s*\.\s*(?P<member>{_IDENTIFIER}))?"
)


@dataclass(frozen=True, slots=True)
class _ExpressionTarget:
    name: str
    span: Span
    node: ast.expr
    owner: ast.expr | None = None
    is_id_key: bool = False


class KvDefinitionEngine:
    """Resolve token-sensitive definitions from a KV document."""

    def __init__(
        self,
        python_index: PythonIndex,
        kv_index: KvIndex,
        config: ServerConfig,
        translation_index: TranslationIndex,
    ) -> None:
        self._python_index = python_index
        self._kv_index = kv_index
        self._config = config
        self._translation_index = translation_index
        self._resolver = KvExpressionResolver(
            python_index,
            config,
        )

    def definition_at(
        self,
        document: TextDocument,
        parse_result: ParseResult,
        semantic_model: KvSemanticModel,
        offset: int,
    ) -> tuple[SymbolLocation, ...]:
        translation = self._translation_definition(
            document,
            parse_result,
            offset,
        )

        if translation:
            return translation

        context = context_at(parse_result, offset)
        selector = context.selector

        if selector is not None:
            if selector.name.span.contains(offset):
                return self._class_locations(selector.name.text)

            for base in selector.base_names:
                if base.span.contains(offset):
                    return self._class_locations(base.text)

        widget = _widget_name_at(
            parse_result.document,
            offset,
        )

        if widget is not None:
            return self._class_locations(widget.name.text)

        property_node = context.property_node

        if (
            property_node is not None
            and _property_name_contains(property_node, offset)
        ):
            return self._property_locations(
                document,
                semantic_model,
                parse_result,
                property_node,
                offset,
            )

        expression = context.expression

        if expression is None:
            return ()

        scope = semantic_model.scope_at(offset)

        if scope is None:
            return ()

        self_value = self._resolver.self_value(
            document,
            scope,
            context.current_widget,
        )

        return self._expression_locations(
            document,
            expression.span,
            scope,
            self_value,
            offset,
        )

    def _translation_definition(
        self,
        document: TextDocument,
        parse_result: ParseResult,
        offset: int,
    ) -> tuple[SymbolLocation, ...]:
        i18n = self._config.i18n

        if i18n is None:
            return ()

        key_target = translation_key_target_at(
            document,
            parse_result,
            offset,
            i18n,
            self._translation_index,
        )

        if key_target is not None and key_target.entry is not None:
            entry = key_target.entry
            return (
                _location(
                    entry.uri,
                    entry.key_span,
                ),
            )

        parameter_target = translation_parameter_target_at(
            document,
            parse_result,
            offset,
            i18n,
            self._translation_index,
        )

        if (
            parameter_target is None
            or parameter_target.placeholder is None
        ):
            return ()

        placeholder = parameter_target.placeholder
        return (
            _location(
                parameter_target.entry.uri,
                placeholder.span,
            ),
        )

    def _property_locations(
        self,
        document: TextDocument,
        semantic_model: KvSemanticModel,
        parse_result: ParseResult,
        property_node: PropertyNode,
        offset: int,
    ) -> tuple[SymbolLocation, ...]:
        scope = semantic_model.scope_at(offset)

        if scope is None:
            return ()

        context = context_at(parse_result, offset)
        owner = self._resolver.self_value(
            document,
            scope,
            context.property_owner,
        )
        resolution = KvExpressionResolution.resolved(owner)
        definitions = self._resolver.member_definitions(
            resolution,
            property_node.name,
        )

        return _deduplicate_locations(
            tuple(
                symbol.location
                for symbol in definitions
            )
        )

    def _expression_locations(
        self,
        document: TextDocument,
        expression_span: Span,
        scope: KvScope,
        self_value: KvValue,
        offset: int,
    ) -> tuple[SymbolLocation, ...]:
        source = document.text[
            expression_span.start:expression_span.end
        ]
        target = _expression_target_at(
            source,
            expression_span.start,
            offset,
        )

        if target is None:
            return ()

        if target.is_id_key:
            binding = scope.id_named(target.name)
            return _binding_locations(binding, scope.uri)

        if isinstance(target.node, ast.Name):
            binding = scope.id_named(target.name)

            if binding is not None:
                return _binding_locations(binding, scope.uri)

            resolution = self._resolver.resolve(
                target.name,
                scope,
                self_value=self_value,
            )
            return self._resolution_locations(resolution)

        if target.owner is None:
            return ()

        owner_source = ast.get_source_segment(
            source,
            target.owner,
        )

        if owner_source is None:
            return ()

        owner = self._resolver.resolve(
            owner_source,
            scope,
            self_value=self_value,
        )

        if owner.kind is KvResolutionKind.ID_NAMESPACE:
            binding = scope.id_named(target.name)
            return _binding_locations(binding, scope.uri)

        definitions = self._resolver.member_definitions(
            owner,
            target.name,
        )

        return _deduplicate_locations(
            tuple(
                symbol.location
                for symbol in definitions
            )
        )

    def _resolution_locations(
        self,
        resolution: KvExpressionResolution,
    ) -> tuple[SymbolLocation, ...]:
        value = resolution.value

        if value is None:
            return ()

        return self._value_locations(value)

    def _value_locations(
        self,
        value: KvValue,
    ) -> tuple[SymbolLocation, ...]:
        if value.type_name is not None:
            dynamic = tuple(
                _location(symbol.uri, symbol.span)
                for symbol in self._kv_index.find(value.type_name)
                if symbol.is_dynamic
            )

            if dynamic:
                return _deduplicate_locations(dynamic)

        if value.symbol is not None:
            return (value.symbol.location,)

        if value.class_symbol is not None:
            return (value.class_symbol.location,)

        return ()

    def _class_locations(
        self,
        name: str,
    ) -> tuple[SymbolLocation, ...]:
        dynamic = tuple(
            _location(symbol.uri, symbol.span)
            for symbol in self._kv_index.find(name)
            if symbol.is_dynamic
        )

        if dynamic:
            return _deduplicate_locations(dynamic)

        candidates: list[ClassSymbol] = []
        resolved = self._python_index.resolve_class(name)

        if resolved is not None:
            candidates.append(resolved)

        candidates.extend(self._python_index.classes_named(name))

        for registration in (
            self._python_index.factory_registrations_named(name)
        ):
            class_symbol = self._python_index.resolve_factory_class(
                registration,
            )

            if class_symbol is not None:
                candidates.append(class_symbol)

        return _deduplicate_locations(
            tuple(
                candidate.location
                for candidate in candidates
            )
        )


class PythonIdsDefinitionEngine:
    """Resolve Python self.ids references into KV and Python sources."""

    def __init__(
        self,
        python_index: PythonIndex,
        kv_index: KvIndex,
    ) -> None:
        self._python_index = python_index
        self._kv_index = kv_index

    def definition_at(
        self,
        document: TextDocument,
        offset: int,
    ) -> tuple[SymbolLocation, ...]:
        if offset < 0 or offset > len(document.text):
            return ()

        class_name = enclosing_class_name(
            document.text,
            offset,
        )

        if class_name is None:
            return ()

        line_start = document.text.rfind("\n", 0, offset) + 1
        line_end = document.text.find("\n", offset)

        if line_end < 0:
            line_end = len(document.text)

        line = document.text[line_start:line_end]
        relative_offset = offset - line_start

        for pattern in (
            _PYTHON_SUBSCRIPT_IDS_PATTERN,
            _PYTHON_DOT_IDS_PATTERN,
        ):
            for match in pattern.finditer(line):
                result = self._definition_for_match(
                    class_name,
                    match,
                    relative_offset,
                )

                if result:
                    return result

        return ()

    def _definition_for_match(
        self,
        class_name: str,
        match: re.Match[str],
        offset: int,
    ) -> tuple[SymbolLocation, ...]:
        id_name = match.group("id")
        id_start, id_end = match.span("id")

        if id_start <= offset < id_end:
            return tuple(
                _id_location(id_symbol)
                for id_symbol in (
                    self._kv_index.id_definitions_for_class(
                        class_name,
                        id_name,
                    )
                )
            )

        member = match.group("member")

        if member is None:
            return ()

        member_start, member_end = match.span("member")

        if not member_start <= offset < member_end:
            return ()

        id_symbol = next(
            (
                item
                for item in self._kv_index.ids_for_class(class_name)
                if item.name == id_name
            ),
            None,
        )

        if id_symbol is None:
            return ()

        class_symbol = resolve_widget_class(
            id_symbol.widget_class,
            self._python_index,
            self._kv_index,
        )

        if class_symbol is None:
            return ()

        symbol = self._python_index.member_named(
            class_symbol,
            member,
        )

        if symbol is None:
            return ()

        return (symbol.location,)


def _expression_target_at(
    source: str,
    base_offset: int,
    offset: int,
) -> _ExpressionTarget | None:
    try:
        expression = ast.parse(source, mode="eval")
    except SyntaxError:
        return _dotted_expression_target(
            source,
            base_offset,
            offset,
        )

    parents = {
        child: parent
        for parent in ast.walk(expression)
        for child in ast.iter_child_nodes(parent)
    }
    candidates: list[_ExpressionTarget] = []

    for node in ast.walk(expression):
        if isinstance(node, ast.Name):
            span = _ast_span(source, node, base_offset)
            candidates.append(
                _ExpressionTarget(
                    name=node.id,
                    span=span,
                    node=node,
                )
            )
            continue

        if isinstance(node, ast.Attribute):
            span = _attribute_span(
                source,
                node,
                base_offset,
            )
            candidates.append(
                _ExpressionTarget(
                    name=node.attr,
                    span=span,
                    node=node,
                    owner=node.value,
                )
            )
            continue

        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _is_ids_key_node(node, parents)
        ):
            span = _string_content_span(
                source,
                node,
                base_offset,
            )
            candidates.append(
                _ExpressionTarget(
                    name=node.value,
                    span=span,
                    node=node,
                    is_id_key=True,
                )
            )

    matches = [
        candidate
        for candidate in candidates
        if candidate.span.contains(offset)
    ]

    if not matches:
        return None

    return min(matches, key=lambda candidate: candidate.span.length)


def _is_ids_key_node(
    node: ast.Constant,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    parent = parents.get(node)

    if isinstance(parent, ast.Subscript) and parent.slice is node:
        return _is_ids_expression(parent.value)

    if not isinstance(parent, ast.Call) or not parent.args:
        return False

    if parent.args[0] is not node:
        return False

    function = parent.func

    return (
        isinstance(function, ast.Attribute)
        and function.attr == "get"
        and _is_ids_expression(function.value)
    )


def _is_ids_expression(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "ids"
        and isinstance(node.value, ast.Name)
        and node.value.id in {"root", "self"}
    )


def _dotted_expression_target(
    source: str,
    base_offset: int,
    offset: int,
) -> _ExpressionTarget | None:
    relative = offset - base_offset

    for match in re.finditer(_IDENTIFIER, source):
        if not match.start() <= relative < match.end():
            continue

        node = ast.Name(
            id=match.group(0),
            ctx=ast.Load(),
        )
        return _ExpressionTarget(
            name=match.group(0),
            span=Span(
                start=base_offset + match.start(),
                end=base_offset + match.end(),
            ),
            node=node,
        )

    return None


def _widget_name_at(
    document: DocumentNode,
    offset: int,
) -> WidgetNode | None:
    def visit(body: tuple[BodyNode, ...]) -> WidgetNode | None:
        for item in body:
            if isinstance(item, WidgetNode):
                if item.name.span.contains(offset):
                    return item

                nested = visit(item.body)

                if nested is not None:
                    return nested
            elif item.body:
                nested = visit(item.body)

                if nested is not None:
                    return nested

        return None

    for item in document.items:
        if isinstance(item, WidgetNode):
            if item.name.span.contains(offset):
                return item

            nested = visit(item.body)

            if nested is not None:
                return nested
        elif isinstance(item, RuleNode):
            nested = visit(item.body)

            if nested is not None:
                return nested

    return None


def _property_name_contains(
    property_node: PropertyNode,
    offset: int,
) -> bool:
    return any(
        token.span.contains(offset)
        for token in property_node.name_tokens
        if token.text.isidentifier()
    )


def _binding_locations(
    binding: KvBinding | None,
    uri: str,
) -> tuple[SymbolLocation, ...]:
    if binding is None or binding.declaration_span is None:
        return ()

    return (
        _location(
            uri,
            binding.declaration_span,
        ),
    )


def _id_location(id_symbol: KvIdSymbol) -> SymbolLocation:
    return _location(id_symbol.uri, id_symbol.span)


def _location(uri: str, span: Span) -> SymbolLocation:
    return SymbolLocation(
        uri=uri,
        span=span,
        selection_span=span,
    )


def _deduplicate_locations(
    locations: tuple[SymbolLocation, ...],
) -> tuple[SymbolLocation, ...]:
    unique: dict[tuple[str, int, int], SymbolLocation] = {}

    for location in locations:
        key = (
            location.uri,
            location.selection_span.start,
            location.selection_span.end,
        )
        unique.setdefault(key, location)

    return tuple(unique.values())


def _ast_span(
    source: str,
    node: ast.expr,
    base_offset: int,
) -> Span:
    start = _line_byte_offset(
        source,
        node.lineno,
        node.col_offset,
    )
    end = _line_byte_offset(
        source,
        node.end_lineno or node.lineno,
        node.end_col_offset or node.col_offset,
    )
    return Span(
        start=base_offset + start,
        end=base_offset + end,
    )


def _attribute_span(
    source: str,
    node: ast.Attribute,
    base_offset: int,
) -> Span:
    end = _line_byte_offset(
        source,
        node.end_lineno or node.lineno,
        node.end_col_offset or node.col_offset,
    )
    return Span(
        start=base_offset + end - len(node.attr),
        end=base_offset + end,
    )


def _string_content_span(
    source: str,
    node: ast.Constant,
    base_offset: int,
) -> Span:
    span = _ast_span(source, node, base_offset)
    segment = source[
        span.start - base_offset:
        span.end - base_offset
    ]
    quote_length = 3 if segment[:3] in {"'''", '\"\"\"'} else 1
    return Span(
        start=span.start + quote_length,
        end=span.end - quote_length,
    )


def _line_byte_offset(
    source: str,
    line_number: int,
    byte_column: int,
) -> int:
    lines = source.splitlines(keepends=True)
    start = sum(
        len(line)
        for line in lines[:line_number - 1]
    )
    line = lines[line_number - 1]
    column = len(
        line.encode("utf-8")[:byte_column].decode("utf-8")
    )
    return start + column

