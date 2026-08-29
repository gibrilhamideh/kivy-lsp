# src/kivy_lsp/analysis/diagnostics.py

from __future__ import annotations

import ast

from kivy_lsp.analysis.expression import KvExpressionResolver
from kivy_lsp.analysis.expression_diagnostics import (
    KvExpressionDiagnosticAnalyzer,
)
from kivy_lsp.analysis.property_diagnostics import (
    KvPropertyDiagnosticAnalyzer,
)
from kivy_lsp.analysis.scope import KvSemanticModel
from kivy_lsp.analysis.value_inference import KvValueInferer
from kivy_lsp.config import ServerConfig
from kivy_lsp.kv.nodes import (
    BodyNode,
    DocumentNode,
    PropertyNode,
    RuleNode,
    WidgetNode,
)
from kivy_lsp.kv.parser import ParseResult
from kivy_lsp.model.diagnostic import Diagnostic
from kivy_lsp.model.span import Span
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.workspace.document import PositionEncoding, TextDocument

_NON_EXPRESSION_PROPERTIES = frozenset(
    {
        "id",
    }
)

_CANVAS_NAMES = frozenset(
    {
        "canvas",
        "canvas.before",
        "canvas.after",
    }
)


class KvDiagnosticAnalyzer:
    """Produce semantic diagnostics for one complete KV document."""

    def __init__(
        self,
        python_index: PythonIndex,
        config: ServerConfig | None = None,
    ) -> None:
        self._resolver = KvExpressionResolver(
            python_index,
            config,
        )
        self._expression_analyzer = (
            KvExpressionDiagnosticAnalyzer(
                self._resolver,
            )
        )
        self._property_analyzer = (
            KvPropertyDiagnosticAnalyzer(
                python_index,
            )
        )
        self._value_inferer = KvValueInferer(
            self._resolver,
        )

    def analyze(
        self,
        document: TextDocument,
        parse_result: ParseResult,
        semantic_model: KvSemanticModel,
    ) -> tuple[Diagnostic, ...]:
        diagnostics: list[Diagnostic] = []

        self._walk_document(
            document,
            parse_result.document,
            semantic_model,
            diagnostics,
        )

        return _deduplicate_and_sort(diagnostics)

    def _walk_document(
        self,
        document: TextDocument,
        root: DocumentNode,
        semantic_model: KvSemanticModel,
        diagnostics: list[Diagnostic],
    ) -> None:
        for item in root.items:
            if isinstance(item, RuleNode):
                self._walk_body(
                    document,
                    item.body,
                    semantic_model,
                    diagnostics,
                    current_widget=None,
                )
                continue

            if isinstance(item, WidgetNode):
                self._walk_widget(
                    document,
                    item,
                    semantic_model,
                    diagnostics,
                )

    def _walk_body(
        self,
        document: TextDocument,
        body: tuple[BodyNode, ...],
        semantic_model: KvSemanticModel,
        diagnostics: list[Diagnostic],
        *,
        current_widget: WidgetNode | None,
    ) -> None:
        for item in body:
            if isinstance(item, WidgetNode):
                self._walk_widget(
                    document,
                    item,
                    semantic_model,
                    diagnostics,
                )
                continue

            self._walk_property(
                document,
                item,
                semantic_model,
                diagnostics,
                current_widget=current_widget,
            )

    def _walk_widget(
        self,
        document: TextDocument,
        widget: WidgetNode,
        semantic_model: KvSemanticModel,
        diagnostics: list[Diagnostic],
    ) -> None:
        self._walk_body(
            document,
            widget.body,
            semantic_model,
            diagnostics,
            current_widget=widget,
        )

    def _walk_property(
        self,
        document: TextDocument,
        property_node: PropertyNode,
        semantic_model: KvSemanticModel,
        diagnostics: list[Diagnostic],
        *,
        current_widget: WidgetNode | None,
        property_owner: WidgetNode | None = None,
    ) -> None:
        expression = property_node.value

        if expression is not None:
            self._analyze_expression(
                document,
                property_node,
                expression.text,
                expression.span,
                semantic_model,
                diagnostics,
                current_widget=current_widget,
                property_owner=property_owner,
            )

        if property_node.body:
            if property_node.name in _CANVAS_NAMES:
                self._walk_canvas_body(
                    document,
                    property_node.body,
                    semantic_model,
                    diagnostics,
                    current_widget=current_widget,
                )
            elif property_owner is not None:
                self._walk_instruction_body(
                    document,
                    property_node.body,
                    semantic_model,
                    diagnostics,
                    current_widget=current_widget,
                    property_owner=property_owner,
                )
            else:
                self._walk_body(
                    document,
                    property_node.body,
                    semantic_model,
                    diagnostics,
                    current_widget=current_widget,
                )

    def _walk_canvas_body(
        self,
        document: TextDocument,
        body: tuple[BodyNode, ...],
        semantic_model: KvSemanticModel,
        diagnostics: list[Diagnostic],
        *,
        current_widget: WidgetNode | None,
    ) -> None:
        for item in body:
            if isinstance(item, WidgetNode):
                self._walk_instruction_body(
                    document,
                    item.body,
                    semantic_model,
                    diagnostics,
                    current_widget=current_widget,
                    property_owner=item,
                )

    def _walk_instruction_body(
        self,
        document: TextDocument,
        body: tuple[BodyNode, ...],
        semantic_model: KvSemanticModel,
        diagnostics: list[Diagnostic],
        *,
        current_widget: WidgetNode | None,
        property_owner: WidgetNode,
    ) -> None:
        for item in body:
            if isinstance(item, WidgetNode):
                self._walk_instruction_body(
                    document,
                    item.body,
                    semantic_model,
                    diagnostics,
                    current_widget=current_widget,
                    property_owner=item,
                )
                continue

            self._walk_property(
                document,
                item,
                semantic_model,
                diagnostics,
                current_widget=current_widget,
                property_owner=property_owner,
            )

    def _analyze_expression(
        self,
        document: TextDocument,
        property_node: PropertyNode,
        source: str,
        value_span: Span,
        semantic_model: KvSemanticModel,
        diagnostics: list[Diagnostic],
        *,
        current_widget: WidgetNode | None,
        property_owner: WidgetNode | None,
    ) -> None:
        if property_node.name in _NON_EXPRESSION_PROPERTIES:
            return

        if not source.strip():
            return

        scope = semantic_model.scope_at(
            value_span.start,
        )

        if scope is None:
            return

        self_value = self._resolver.self_value(
            document,
            scope,
            current_widget,
        )
        property_value = (
            self._resolver.self_value(
                document,
                scope,
                property_owner,
            )
            if property_owner is not None
            else self_value
        )

        diagnostics.extend(
            self._expression_analyzer.analyze(
                source,
                value_span,
                scope,
                self_value=self_value,
                statement_block=property_node.is_event_handler,
                statement_indent=(
                    _statement_indent(
                        document,
                        value_span.start,
                    )
                    if property_node.is_event_handler
                    else ""
                ),
            )
        )

        if property_node.is_event_handler:
            return

        inferred_value = self._value_inferer.infer(
            source,
            scope,
            self_value=self_value,
        )
        sequence_length = _literal_sequence_length(source)

        diagnostics.extend(
            self._property_analyzer.analyze(
                widget_value=property_value,
                property_name=property_node.name,
                value=inferred_value,
                value_span=value_span,
                sequence_length=sequence_length,
            )
        )


def _literal_sequence_length(
    source: str,
) -> int | None:
    try:
        tree = ast.parse(
            source,
            mode="eval",
        )
    except SyntaxError:
        return None

    expression = tree.body

    if isinstance(
        expression,
        (
            ast.List,
            ast.Tuple,
        ),
    ):
        return len(expression.elts)

    return None


def _statement_indent(
    document: TextDocument,
    offset: int,
) -> str:
    position = document.position_at(
        offset,
        PositionEncoding.UTF32,
    )
    line = document.line_text(position.line)
    prefix = line[:position.character]

    if prefix and not prefix.isspace():
        return ""

    return prefix


def _deduplicate_and_sort(
    diagnostics: list[Diagnostic],
) -> tuple[Diagnostic, ...]:
    unique: dict[
        tuple[str, int, int, str | None],
        Diagnostic,
    ] = {}

    for diagnostic in diagnostics:
        key = (
            diagnostic.message,
            diagnostic.span.start,
            diagnostic.span.end,
            diagnostic.code,
        )
        unique.setdefault(
            key,
            diagnostic,
        )

    return tuple(
        sorted(
            unique.values(),
            key=lambda diagnostic: (
                diagnostic.span.start,
                diagnostic.span.end,
                diagnostic.code or "",
                diagnostic.message,
            ),
        )
    )
