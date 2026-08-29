# src/kivy_lsp/kv/nodes.py

from __future__ import annotations

from dataclasses import dataclass

from kivy_lsp.kv.tokens import Token
from kivy_lsp.model.span import Span


@dataclass(frozen=True, slots=True)
class KvNode:
    """Base class for every node in a parsed KV document."""

    span: Span


@dataclass(frozen=True, slots=True)
class ExpressionNode(KvNode):
    """A Python expression used as a KV property value."""

    tokens: tuple[Token, ...]

    @property
    def text(self) -> str:
        return "".join(token.text for token in self.tokens)


@dataclass(frozen=True, slots=True)
class DirectiveNode(KvNode):
    """A KV directive such as #:import, #:include, or #:set."""

    token: Token
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class RuleSelectorNode(KvNode):
    """One class selector from a KV rule declaration."""

    name: Token
    dynamic_marker: Token | None
    base_names: tuple[Token, ...]

    @property
    def is_dynamic(self) -> bool:
        return self.dynamic_marker is not None


@dataclass(frozen=True, slots=True)
class RuleNode(KvNode):
    """A rule such as <MyWidget>: or <Name@BaseWidget>:."""

    opening: Token
    selectors: tuple[RuleSelectorNode, ...]
    closing: Token
    colon: Token
    body: tuple[BodyNode, ...]


@dataclass(frozen=True, slots=True)
class WidgetNode(KvNode):
    """A root widget, child widget, or canvas instruction."""

    name: Token
    colon: Token
    body: tuple[BodyNode, ...]

    @property
    def class_name(self) -> str:
        return self.name.text


@dataclass(frozen=True, slots=True)
class PropertyNode(KvNode):
    """A property assignment, event handler, or property block."""

    clear_previous: Token | None
    name_tokens: tuple[Token, ...]
    colon: Token
    value: ExpressionNode | None
    body: tuple[BodyNode, ...]

    def __post_init__(self) -> None:
        if self.value is not None and self.body:
            raise ValueError(
                "a property cannot have both a value and a body"
            )

    @property
    def name(self) -> str:
        return "".join(token.text for token in self.name_tokens)

    @property
    def has_value(self) -> bool:
        return self.value is not None

    @property
    def is_block(self) -> bool:
        return bool(self.body)

    @property
    def is_event_handler(self) -> bool:
        return self.name.startswith("on_")


type BodyNode = WidgetNode | PropertyNode
type DocumentItem = DirectiveNode | RuleNode | WidgetNode


@dataclass(frozen=True, slots=True)
class DocumentNode(KvNode):
    """The parsed syntax tree for one complete KV document."""

    items: tuple[DocumentItem, ...]
    eof: Token
