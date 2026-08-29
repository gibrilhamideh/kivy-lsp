# src/kivy_lsp/kv/tokens.py

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto

from kivy_lsp.model.span import Span


class TokenKind(StrEnum):
    """Kinds of tokens that can appear in KV source code."""

    EOF = auto()
    ERROR = auto()

    NEWLINE = auto()
    INDENT = auto()
    DEDENT = auto()
    WHITESPACE = auto()
    COMMENT = auto()
    LINE_CONTINUATION = auto()

    IDENTIFIER = auto()
    NUMBER = auto()
    STRING = auto()
    DIRECTIVE = auto()

    COLON = auto()
    COMMA = auto()
    DOT = auto()
    SEMICOLON = auto()

    LEFT_PAREN = auto()
    RIGHT_PAREN = auto()
    LEFT_BRACKET = auto()
    RIGHT_BRACKET = auto()
    LEFT_BRACE = auto()
    RIGHT_BRACE = auto()

    LESS_THAN = auto()
    GREATER_THAN = auto()
    AT = auto()

    ASSIGN = auto()
    OPERATOR = auto()


TRIVIA_TOKEN_KINDS: frozenset[TokenKind] = frozenset(
    {
        TokenKind.WHITESPACE,
        TokenKind.COMMENT,
        TokenKind.LINE_CONTINUATION,
    }
)

STRUCTURAL_TOKEN_KINDS: frozenset[TokenKind] = frozenset(
    {
        TokenKind.NEWLINE,
        TokenKind.INDENT,
        TokenKind.DEDENT,
        TokenKind.EOF,
    }
)


@dataclass(frozen=True, slots=True)
class Token:
    """A single lexical unit from KV source code."""

    kind: TokenKind
    text: str
    span: Span
    synthetic: bool = False

    def __post_init__(self) -> None:
        if not self.synthetic and len(self.text) != self.span.length:
            raise ValueError(
                "token text length must match its source span"
            )

    @classmethod
    def missing(
        cls,
        kind: TokenKind,
        offset: int,
    ) -> Token:
        """Create a missing token for parser error recovery."""

        return cls(
            kind=kind,
            text="",
            span=Span.empty(offset),
            synthetic=True,
        )

    @property
    def is_trivia(self) -> bool:
        return self.kind in TRIVIA_TOKEN_KINDS

    @property
    def is_structural(self) -> bool:
        return self.kind in STRUCTURAL_TOKEN_KINDS

    @property
    def is_error(self) -> bool:
        return self.kind is TokenKind.ERROR

    @property
    def is_missing(self) -> bool:
        return self.synthetic

    def contains(self, offset: int) -> bool:
        return self.span.contains(offset)

    def contains_cursor(self, offset: int) -> bool:
        return self.span.contains_cursor(offset)
