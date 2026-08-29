from __future__ import annotations

import re
from dataclasses import dataclass

from kivy_lsp.kv.nodes import (
    BodyNode,
    DocumentItem,
    PropertyNode,
    RuleNode,
    RuleSelectorNode,
    WidgetNode,
)
from kivy_lsp.kv.parser import ParseResult
from kivy_lsp.model.document_symbol import (
    KvDocumentSymbol,
    KvDocumentSymbolKind,
)
from kivy_lsp.model.span import Span
from kivy_lsp.workspace.document import TextDocument

_SECTION_PATTERN = re.compile(
    r"^(?P<indent>[ \t]*)#[ \t]*section:[ \t]*"
    r"(?P<name>.*?\S)[ \t]*$",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class _SectionMarker:
    name: str
    span: Span
    selection_span: Span
    indent: int


class KvDocumentSymbolAnalyzer:
    """Build an editor outline from an error-tolerant KV parse tree."""

    def analyze(
        self,
        document: TextDocument,
        parse_result: ParseResult,
    ) -> tuple[KvDocumentSymbol, ...]:
        builder = _DocumentSymbolBuilder(
            document,
            _section_markers(document.text),
        )

        return builder.document_symbols(
            parse_result.document.items,
            parse_result.document.span,
        )


class _DocumentSymbolBuilder:
    def __init__(
        self,
        document: TextDocument,
        section_markers: tuple[_SectionMarker, ...],
    ) -> None:
        self._document = document
        self._section_markers = section_markers

    def document_symbols(
        self,
        items: tuple[DocumentItem, ...],
        container_span: Span,
    ) -> tuple[KvDocumentSymbol, ...]:
        structural_items = tuple(
            item
            for item in items
            if isinstance(item, (RuleNode, WidgetNode))
        )

        return self._symbols_for_items(
            structural_items,
            container_span,
        )

    def body_symbols(
        self,
        body: tuple[BodyNode, ...],
        container_span: Span,
    ) -> tuple[KvDocumentSymbol, ...]:
        return self._symbols_for_items(
            body,
            container_span,
        )

    def _symbols_for_items(
        self,
        items: tuple[RuleNode | WidgetNode | PropertyNode, ...],
        container_span: Span,
    ) -> tuple[KvDocumentSymbol, ...]:
        symbols = tuple(
            self._symbol_for_node(item)
            for item in items
        )

        if not symbols:
            return ()

        markers = self._direct_section_markers(
            items,
            container_span,
        )

        if not markers:
            return symbols

        return _group_sections(symbols, markers)

    def _symbol_for_node(
        self,
        node: RuleNode | WidgetNode | PropertyNode,
    ) -> KvDocumentSymbol:
        if isinstance(node, RuleNode):
            return self._rule_symbol(node)

        if isinstance(node, WidgetNode):
            return self._widget_symbol(node)

        return self._property_symbol(node)

    def _rule_symbol(
        self,
        node: RuleNode,
    ) -> KvDocumentSymbol:
        selection_span = _selector_selection_span(node)

        return KvDocumentSymbol(
            name=_rule_name(node),
            kind=KvDocumentSymbolKind.CLASS,
            span=node.span,
            selection_span=selection_span,
            children=self.body_symbols(
                node.body,
                Span(
                    start=node.colon.span.end,
                    end=node.span.end,
                ),
            ),
        )

    def _widget_symbol(
        self,
        node: WidgetNode,
    ) -> KvDocumentSymbol:
        is_canvas = node.class_name in {
            "canvas",
            "canvas.after",
            "canvas.before",
        }

        return KvDocumentSymbol(
            name=node.class_name,
            kind=(
                KvDocumentSymbolKind.NAMESPACE
                if is_canvas
                else KvDocumentSymbolKind.CONSTRUCTOR
            ),
            span=node.span,
            selection_span=node.name.span,
            children=self.body_symbols(
                node.body,
                Span(
                    start=node.colon.span.end,
                    end=node.span.end,
                ),
            ),
        )

    def _property_symbol(
        self,
        node: PropertyNode,
    ) -> KvDocumentSymbol:
        is_canvas = node.name in {
            "canvas",
            "canvas.after",
            "canvas.before",
        }
        selection_span = Span(
            start=node.name_tokens[0].span.start,
            end=node.name_tokens[-1].span.end,
        )

        return KvDocumentSymbol(
            name=node.name,
            kind=(
                KvDocumentSymbolKind.NAMESPACE
                if is_canvas
                else (
                    KvDocumentSymbolKind.EVENT
                    if node.is_event_handler
                    else KvDocumentSymbolKind.PROPERTY
                )
            ),
            span=node.span,
            selection_span=selection_span,
            children=self.body_symbols(
                node.body,
                Span(
                    start=node.colon.span.end,
                    end=node.span.end,
                ),
            ),
        )

    def _direct_section_markers(
        self,
        items: tuple[RuleNode | WidgetNode | PropertyNode, ...],
        container_span: Span,
    ) -> tuple[_SectionMarker, ...]:
        direct_indent = min(
            self._indent_at(item.span.start)
            for item in items
        )
        markers: list[_SectionMarker] = []

        for marker in self._section_markers:
            if marker.span.start < container_span.start:
                continue

            if marker.span.end > container_span.end:
                break

            if marker.indent != direct_indent:
                continue

            if any(
                item.span.contains(marker.span.start)
                for item in items
            ):
                continue

            markers.append(marker)

        return tuple(markers)

    def _indent_at(self, offset: int) -> int:
        line = self._document.position_at(offset).line
        source_line = self._document.line_text(line)
        return _indent_width(source_line)


def _section_markers(source: str) -> tuple[_SectionMarker, ...]:
    markers: list[_SectionMarker] = []

    for match in _SECTION_PATTERN.finditer(source):
        name = match.group("name")
        markers.append(
            _SectionMarker(
                name=name,
                span=Span(
                    start=match.start(),
                    end=match.end(),
                ),
                selection_span=Span(
                    start=match.start("name"),
                    end=match.end("name"),
                ),
                indent=_indent_width(match.group("indent")),
            )
        )

    return tuple(markers)


def _group_sections(
    symbols: tuple[KvDocumentSymbol, ...],
    markers: tuple[_SectionMarker, ...],
) -> tuple[KvDocumentSymbol, ...]:
    result: list[KvDocumentSymbol] = []
    marker_index = 0
    active_marker: _SectionMarker | None = None
    active_children: list[KvDocumentSymbol] = []

    def finish_section() -> None:
        nonlocal active_marker, active_children

        if active_marker is None:
            return

        end = (
            active_children[-1].span.end
            if active_children
            else active_marker.span.end
        )
        result.append(
            KvDocumentSymbol(
                name=active_marker.name,
                kind=KvDocumentSymbolKind.NAMESPACE,
                span=Span(
                    start=active_marker.span.start,
                    end=end,
                ),
                selection_span=active_marker.selection_span,
                children=tuple(active_children),
            )
        )
        active_marker = None
        active_children = []

    for symbol in symbols:
        while (
            marker_index < len(markers)
            and markers[marker_index].span.start < symbol.span.start
        ):
            finish_section()
            active_marker = markers[marker_index]
            marker_index += 1

        if active_marker is None:
            result.append(symbol)
        else:
            active_children.append(symbol)

    while marker_index < len(markers):
        finish_section()
        active_marker = markers[marker_index]
        marker_index += 1

    finish_section()
    return tuple(result)


def _rule_name(node: RuleNode) -> str:
    return ", ".join(
        _selector_name(selector)
        for selector in node.selectors
    )


def _selector_name(selector: RuleSelectorNode) -> str:
    if not selector.is_dynamic:
        return selector.name.text

    bases = "+".join(
        base_name.text
        for base_name in selector.base_names
    )
    return f"{selector.name.text}@{bases}"


def _selector_selection_span(node: RuleNode) -> Span:
    if not node.selectors:
        return Span.empty(node.opening.span.end)

    return Span(
        start=node.selectors[0].span.start,
        end=node.selectors[-1].span.end,
    )


def _indent_width(source_line: str) -> int:
    width = 0

    for character in source_line:
        if character == " ":
            width += 1
            continue

        if character == "\t":
            width += 4
            continue

        break

    return width
