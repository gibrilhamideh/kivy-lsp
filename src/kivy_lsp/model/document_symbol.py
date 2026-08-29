from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kivy_lsp.model.span import Span


class KvDocumentSymbolKind(StrEnum):
    """The structural category of a KV document symbol."""

    CLASS = "class"
    CONSTRUCTOR = "constructor"
    EVENT = "event"
    NAMESPACE = "namespace"
    PROPERTY = "property"


@dataclass(frozen=True, slots=True)
class KvDocumentSymbol:
    """One hierarchical symbol shown in an editor outline."""

    name: str
    kind: KvDocumentSymbolKind
    span: Span
    selection_span: Span
    children: tuple[KvDocumentSymbol, ...] = ()
    detail: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Document symbol name cannot be empty.")

        if not self.span.encloses(self.selection_span):
            raise ValueError(
                "Document symbol range must enclose its selection."
            )

        for child in self.children:
            if not self.span.encloses(child.span):
                raise ValueError(
                    "Document symbol range must enclose its children."
                )
