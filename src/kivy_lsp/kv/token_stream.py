# src/kivy_lsp/kv/token_stream.py

from __future__ import annotations

from collections.abc import Iterable

from kivy_lsp.kv.tokens import (
    TRIVIA_TOKEN_KINDS,
    Token,
    TokenKind,
)


class TokenStream:
    """A movable, non-destructive cursor over lexer tokens."""

    def __init__(self, tokens: tuple[Token, ...]) -> None:
        if not tokens:
            raise ValueError("token stream cannot be empty")

        if tokens[-1].kind is not TokenKind.EOF:
            raise ValueError("token stream must end with an EOF token")

        self._tokens = tokens
        self._position = 0

    @property
    def position(self) -> int:
        return self._position

    @property
    def current(self) -> Token:
        return self._tokens[self._position]

    @property
    def previous(self) -> Token | None:
        if self._position == 0:
            return None

        return self._tokens[self._position - 1]

    @property
    def at_end(self) -> bool:
        return self.current.kind is TokenKind.EOF

    def check(self, kind: TokenKind) -> bool:
        return self.current.kind is kind

    def advance(self) -> Token:
        token = self.current

        if not self.at_end:
            self._position += 1

        return token

    def consume(self, kind: TokenKind) -> Token | None:
        if not self.check(kind):
            return None

        return self.advance()

    def peek(self, distance: int = 0) -> Token:
        if distance < 0:
            raise ValueError("peek distance cannot be negative")

        position = min(
            self._position + distance,
            len(self._tokens) - 1,
        )
        return self._tokens[position]

    def peek_significant(self, distance: int = 0) -> Token:
        """Look ahead while ignoring whitespace and comments."""

        if distance < 0:
            raise ValueError("peek distance cannot be negative")

        position = self._position
        remaining = distance

        while position < len(self._tokens):
            token = self._tokens[position]

            if token.kind not in TRIVIA_TOKEN_KINDS:
                if remaining == 0:
                    return token

                remaining -= 1

            position += 1

        return self._tokens[-1]

    def skip_trivia(self) -> None:
        while self.current.kind in TRIVIA_TOKEN_KINDS:
            self.advance()

    def skip(self, kinds: Iterable[TokenKind]) -> None:
        accepted = frozenset(kinds)

        while self.current.kind in accepted:
            self.advance()

    def mark(self) -> int:
        """Return a checkpoint that can later be restored."""

        return self._position

    def restore(self, position: int) -> None:
        if position < 0 or position >= len(self._tokens):
            raise ValueError("token stream position is out of range")

        self._position = position

    def tokens_from(self, position: int) -> tuple[Token, ...]:
        if position < 0 or position > self._position:
            raise ValueError("token stream position is out of range")

        return self._tokens[position:self._position]
