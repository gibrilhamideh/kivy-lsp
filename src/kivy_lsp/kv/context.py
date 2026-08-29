# src/kivy_lsp/kv/context.py

from __future__ import annotations

from dataclasses import dataclass

from kivy_lsp.kv.nodes import (
    BodyNode,
    DirectiveNode,
    DocumentItem,
    DocumentNode,
    ExpressionNode,
    PropertyNode,
    RuleNode,
    RuleSelectorNode,
    WidgetNode,
)
from kivy_lsp.kv.parser import ParseResult

_CANVAS_NAMES = frozenset(
    {
        "canvas",
        "canvas.before",
        "canvas.after",
    }
)


@dataclass(frozen=True, slots=True)
class KvContext:
    """KV syntax surrounding one cursor offset."""

    document: DocumentNode
    offset: int
    directive: DirectiveNode | None
    rule: RuleNode | None
    selector: RuleSelectorNode | None
    root_widget: WidgetNode | None
    widget_path: tuple[WidgetNode, ...]
    instruction_path: tuple[WidgetNode, ...]
    property_node: PropertyNode | None
    expression: ExpressionNode | None

    @property
    def root_declaration(self) -> RuleNode | WidgetNode | None:
        if self.rule is not None:
            return self.rule

        return self.root_widget

    @property
    def current_widget(self) -> WidgetNode | None:
        if not self.widget_path:
            return None

        return self.widget_path[-1]

    @property
    def current_instruction(self) -> WidgetNode | None:
        if not self.instruction_path:
            return None

        return self.instruction_path[-1]

    @property
    def property_owner(self) -> WidgetNode | None:
        return self.current_instruction or self.current_widget

    @property
    def inside_expression(self) -> bool:
        return self.expression is not None


def context_at(result: ParseResult, offset: int) -> KvContext:
    """Find the deepest KV syntax context at an absolute offset."""

    document = result.document

    if offset < 0 or offset > document.span.end:
        raise ValueError("context offset is outside the document")

    return _ContextFinder(
        document=document,
        offset=offset,
    ).find()


class _ContextFinder:
    """Mutable state used while descending one syntax tree."""

    def __init__(
        self,
        document: DocumentNode,
        offset: int,
    ) -> None:
        self._document = document
        self._offset = offset
        self._directive: DirectiveNode | None = None
        self._rule: RuleNode | None = None
        self._selector: RuleSelectorNode | None = None
        self._root_widget: WidgetNode | None = None
        self._widget_path: list[WidgetNode] = []
        self._instruction_path: list[WidgetNode] = []
        self._property: PropertyNode | None = None
        self._expression: ExpressionNode | None = None

    def find(self) -> KvContext:
        candidate = self._document_item_at()

        if candidate is not None:
            item, limit = candidate
            self._visit_document_item(item, limit)

        return KvContext(
            document=self._document,
            offset=self._offset,
            directive=self._directive,
            rule=self._rule,
            selector=self._selector,
            root_widget=self._root_widget,
            widget_path=tuple(self._widget_path),
            instruction_path=tuple(self._instruction_path),
            property_node=self._property,
            expression=self._expression,
        )

    def _visit_document_item(
        self,
        item: DocumentItem,
        limit: int,
    ) -> None:
        if isinstance(item, DirectiveNode):
            if item.span.contains_cursor(self._offset):
                self._directive = item

            return

        if isinstance(item, RuleNode):
            self._rule = item
            self._visit_selector(item)
            self._visit_body(item.body, limit)
            return

        self._root_widget = item
        self._widget_path.append(item)
        self._visit_body(item.body, limit)

    def _visit_selector(self, rule: RuleNode) -> None:
        for selector in rule.selectors:
            if selector.span.contains_cursor(self._offset):
                self._selector = selector
                return

    def _visit_body(
        self,
        body: tuple[BodyNode, ...],
        limit: int,
        *,
        inside_canvas: bool = False,
    ) -> None:
        candidate = self._body_node_at(body, limit)

        if candidate is None:
            return

        node, node_limit = candidate

        if isinstance(node, WidgetNode):
            if inside_canvas:
                self._instruction_path.append(node)
            else:
                self._widget_path.append(node)

            self._visit_body(
                node.body,
                node_limit,
                inside_canvas=inside_canvas,
            )
            return

        self._property = node

        if (
            node.value is not None
            and node.value.span.contains_cursor(self._offset)
        ):
            self._expression = node.value

        if node.body:
            self._visit_body(
                node.body,
                node_limit,
                inside_canvas=(
                    inside_canvas
                    or node.name in _CANVAS_NAMES
                ),
            )

    def _document_item_at(
        self,
    ) -> tuple[DocumentItem, int] | None:
        items = self._document.items
        limit = self._document.span.end

        for index, item in enumerate(items):
            item_limit = self._next_document_item_start(
                items=items,
                index=index,
                fallback=limit,
            )

            if self._inside_effective_range(
                start=item.span.start,
                end=item_limit,
            ):
                return item, item_limit

        return None

    def _body_node_at(
        self,
        body: tuple[BodyNode, ...],
        limit: int,
    ) -> tuple[BodyNode, int] | None:
        for index, node in enumerate(body):
            node_limit = self._next_body_node_start(
                body=body,
                index=index,
                fallback=limit,
            )

            if self._inside_effective_range(
                start=node.span.start,
                end=node_limit,
            ):
                return node, node_limit

        return None

    def _inside_effective_range(
        self,
        start: int,
        end: int,
    ) -> bool:
        if self._offset == self._document.span.end:
            return start <= self._offset <= end

        return start <= self._offset < end

    @staticmethod
    def _next_document_item_start(
        items: tuple[DocumentItem, ...],
        index: int,
        fallback: int,
    ) -> int:
        next_index = index + 1

        if next_index >= len(items):
            return fallback

        return items[next_index].span.start

    @staticmethod
    def _next_body_node_start(
        body: tuple[BodyNode, ...],
        index: int,
        fallback: int,
    ) -> int:
        next_index = index + 1

        if next_index >= len(body):
            return fallback

        return body[next_index].span.start
