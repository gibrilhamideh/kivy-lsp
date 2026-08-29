# src/kivy_lsp/model/semantic_token.py

from dataclasses import dataclass
from enum import StrEnum

from kivy_lsp.model.span import Span


class SemanticTokenKind(StrEnum):
    NAMESPACE = "namespace"
    CLASS = "class"
    PARAMETER = "parameter"
    VARIABLE = "variable"
    PROPERTY = "property"
    EVENT = "event"
    FUNCTION = "function"
    METHOD = "method"
    KEYWORD = "keyword"
    OPERATOR = "operator"


class SemanticTokenModifier(StrEnum):
    DECLARATION = "declaration"
    DEFINITION = "definition"
    READONLY = "readonly"
    STATIC = "static"
    DEPRECATED = "deprecated"
    DEFAULT_LIBRARY = "defaultLibrary"


@dataclass(frozen=True, slots=True)
class SemanticToken:
    span: Span
    kind: SemanticTokenKind
    modifiers: tuple[SemanticTokenModifier, ...] = ()

    def __post_init__(self) -> None:
        if self.span.start == self.span.end:
            raise ValueError(
                "Semantic token span cannot be empty",
            )
