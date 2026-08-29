# src/kivy_lsp/kv/lexer.py

from __future__ import annotations

import re
from dataclasses import dataclass

from kivy_lsp.kv.tokens import Token, TokenKind
from kivy_lsp.model.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
)
from kivy_lsp.model.span import Span

_NUMBER_PATTERN = re.compile(
    r"""
    (?:
        0[xX](?:_?[0-9a-fA-F])+
        |
        0[oO](?:_?[0-7])+
        |
        0[bB](?:_?[01])+
        |
        (?:
            (?:\d(?:_?\d)*)?\.\d(?:_?\d)*
            |
            \d(?:_?\d)*\.
        )
        (?:[eE][+-]?\d(?:_?\d)*)?
        |
        \d(?:_?\d)*[eE][+-]?\d(?:_?\d)*
        |
        \d(?:_?\d)*
    )
    [jJ]?
    """,
    re.VERBOSE,
)

_PUNCTUATION: dict[str, TokenKind] = {
    ":": TokenKind.COLON,
    ",": TokenKind.COMMA,
    ".": TokenKind.DOT,
    ";": TokenKind.SEMICOLON,
    "(": TokenKind.LEFT_PAREN,
    ")": TokenKind.RIGHT_PAREN,
    "[": TokenKind.LEFT_BRACKET,
    "]": TokenKind.RIGHT_BRACKET,
    "{": TokenKind.LEFT_BRACE,
    "}": TokenKind.RIGHT_BRACE,
    "<": TokenKind.LESS_THAN,
    ">": TokenKind.GREATER_THAN,
    "@": TokenKind.AT,
    "=": TokenKind.ASSIGN,
}

_OPENING_DELIMITERS: dict[str, str] = {
    "(": ")",
    "[": "]",
    "{": "}",
}

_CLOSING_DELIMITERS: frozenset[str] = frozenset(
    _OPENING_DELIMITERS.values()
)

_OPERATORS: tuple[str, ...] = (
    "**=",
    "//=",
    "<<=",
    ">>=",
    "...",
    ":=",
    "==",
    "!=",
    "<=",
    ">=",
    "->",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "@=",
    "&=",
    "|=",
    "^=",
    "**",
    "//",
    "<<",
    ">>",
    "+",
    "-",
    "*",
    "/",
    "%",
    "&",
    "|",
    "^",
    "~",
)


@dataclass(frozen=True, slots=True)
class LexResult:
    """Tokens and diagnostics produced from one KV document."""

    tokens: tuple[Token, ...]
    diagnostics: tuple[Diagnostic, ...]


def lex(source: str) -> LexResult:
    """Convert KV source text into a lossless token sequence."""

    return _Lexer(source).run()


class _Lexer:
    """Mutable state used during a single lexing operation."""

    def __init__(self, source: str) -> None:
        self._source = source
        self._offset = 0
        self._tokens: list[Token] = []
        self._diagnostics: list[Diagnostic] = []
        self._indent_levels = [0]
        self._delimiters: list[tuple[str, int]] = []
        self._at_line_start = True
        self._line_has_code = False
        self._continued_line = False

    def run(self) -> LexResult:
        while not self._at_end:
            if self._at_line_start:
                self._scan_line_start()

                if self._at_end:
                    break

            self._scan_token()

        self._finish_document()

        return LexResult(
            tokens=tuple(self._tokens),
            diagnostics=tuple(self._diagnostics),
        )

    @property
    def _at_end(self) -> bool:
        return self._offset >= len(self._source)

    @property
    def _current(self) -> str:
        if self._at_end:
            return ""

        return self._source[self._offset]

    def _peek(self, distance: int = 1) -> str:
        offset = self._offset + distance

        if offset >= len(self._source):
            return ""

        return self._source[offset]

    def _scan_line_start(self) -> None:
        start = self._offset

        while self._current in (" ", "\t"):
            self._offset += 1

        if self._offset > start:
            self._emit(TokenKind.WHITESPACE, start, self._offset)

        indentation = self._offset - start
        suppress = bool(self._delimiters) or self._continued_line
        self._continued_line = False
        self._at_line_start = False
        self._line_has_code = False

        if suppress or self._is_blank_or_comment_line():
            return

        current = self._indent_levels[-1]

        if indentation > current:
            self._indent_levels.append(indentation)
            self._emit_structural(TokenKind.INDENT)
            return

        if indentation == current:
            return

        while indentation < self._indent_levels[-1]:
            self._indent_levels.pop()
            self._emit_structural(TokenKind.DEDENT)

        if indentation != self._indent_levels[-1]:
            self._report(
                message="indentation does not match an outer level",
                start=start,
                end=self._offset,
                code="kv-inconsistent-dedent",
            )
            self._indent_levels.append(indentation)
            self._emit_structural(TokenKind.INDENT)

    def _is_blank_or_comment_line(self) -> bool:
        return self._current in ("", "\r", "\n", "#")

    def _scan_token(self) -> None:
        character = self._current

        if character in (" ", "\t"):
            self._scan_whitespace()
        elif character in ("\r", "\n"):
            self._scan_newline()
        elif character == "\\" and self._is_newline_at(self._offset + 1):
            self._scan_line_continuation()
        elif character == "#":
            self._scan_hash_line()
        elif self._starts_string():
            self._scan_string()
        elif self._is_identifier_start(character):
            self._scan_identifier()
        elif character.isdigit() or character == "." and self._peek().isdigit():
            self._scan_number()
        elif self._scan_operator():
            self._line_has_code = True
        elif character in _PUNCTUATION:
            self._scan_punctuation()
        else:
            self._scan_unexpected_character()

    def _scan_whitespace(self) -> None:
        start = self._offset

        while self._current in (" ", "\t"):
            self._offset += 1

        self._emit(TokenKind.WHITESPACE, start, self._offset)

    def _scan_newline(self) -> None:
        start = self._offset
        self._consume_newline()
        self._emit(TokenKind.NEWLINE, start, self._offset)
        self._at_line_start = True
        self._line_has_code = False

    def _scan_line_continuation(self) -> None:
        start = self._offset
        self._offset += 1
        self._consume_newline()
        self._emit(TokenKind.LINE_CONTINUATION, start, self._offset)
        self._at_line_start = True
        self._line_has_code = False
        self._continued_line = True

    def _scan_hash_line(self) -> None:
        start = self._offset

        while not self._at_end and self._current not in ("\r", "\n"):
            self._offset += 1

        kind = TokenKind.COMMENT

        if not self._line_has_code and self._source.startswith("#:", start):
            kind = TokenKind.DIRECTIVE

        self._emit(kind, start, self._offset)

    def _scan_identifier(self) -> None:
        start = self._offset
        self._offset += 1

        while self._is_identifier_part(self._current):
            self._offset += 1

        self._emit(TokenKind.IDENTIFIER, start, self._offset)
        self._line_has_code = True

    def _scan_number(self) -> None:
        start = self._offset
        match = _NUMBER_PATTERN.match(self._source, self._offset)

        if match is None:
            self._scan_unexpected_character()
            return

        self._offset = match.end()
        self._emit(TokenKind.NUMBER, start, self._offset)
        self._line_has_code = True

    def _starts_string(self) -> bool:
        if self._current in ("'", '"'):
            return True

        for prefix_length in (2, 1):
            end = self._offset + prefix_length
            prefix = self._source[self._offset:end]

            if end >= len(self._source):
                continue

            if not self._is_string_prefix(prefix):
                continue

            if self._source[end] in ("'", '"'):
                return True

        return False

    def _scan_string(self) -> None:
        start = self._offset
        quote_offset = self._string_quote_offset()
        quote = self._source[quote_offset]
        triple = self._source.startswith(quote * 3, quote_offset)
        self._offset = quote_offset + (3 if triple else 1)
        terminated = False

        while not self._at_end:
            if self._current == "\\":
                self._offset += 1

                if not self._at_end:
                    self._offset += 1

                continue

            if triple and self._source.startswith(
                quote * 3,
                self._offset,
            ):
                self._offset += 3
                terminated = True
                break

            if not triple and self._current == quote:
                self._offset += 1
                terminated = True
                break

            if not triple and self._current in ("\r", "\n"):
                break

            self._offset += 1

        self._emit(TokenKind.STRING, start, self._offset)
        self._line_has_code = True

        if not terminated:
            self._report(
                message="unterminated string literal",
                start=start,
                end=self._offset,
                code="kv-unterminated-string",
            )

    def _string_quote_offset(self) -> int:
        if self._current in ("'", '"'):
            return self._offset

        for prefix_length in (2, 1):
            end = self._offset + prefix_length
            prefix = self._source[self._offset:end]

            if self._is_string_prefix(prefix):
                return end

        return self._offset

    def _scan_operator(self) -> bool:
        for operator in _OPERATORS:
            if not self._source.startswith(operator, self._offset):
                continue

            start = self._offset
            self._offset += len(operator)
            self._emit(TokenKind.OPERATOR, start, self._offset)
            return True

        if self._source.startswith("<=", self._offset):
            self._emit_operator(2)
            return True

        if self._source.startswith(">=", self._offset):
            self._emit_operator(2)
            return True

        return False

    def _emit_operator(self, length: int) -> None:
        start = self._offset
        self._offset += length
        self._emit(TokenKind.OPERATOR, start, self._offset)

    def _scan_punctuation(self) -> None:
        start = self._offset
        character = self._current
        kind = _PUNCTUATION[character]
        self._offset += 1
        self._emit(kind, start, self._offset)
        self._line_has_code = True

        if character in _OPENING_DELIMITERS:
            self._delimiters.append((character, start))
        elif character in _CLOSING_DELIMITERS:
            self._close_delimiter(character, start)

    def _close_delimiter(self, character: str, offset: int) -> None:
        if not self._delimiters:
            self._report(
                message=f"unexpected closing delimiter '{character}'",
                start=offset,
                end=offset + 1,
                code="kv-unexpected-delimiter",
            )
            return

        opening, _ = self._delimiters[-1]
        expected = _OPENING_DELIMITERS[opening]

        if character == expected:
            self._delimiters.pop()
            return

        self._report(
            message=(
                f"expected '{expected}' before closing "
                f"delimiter '{character}'"
            ),
            start=offset,
            end=offset + 1,
            code="kv-mismatched-delimiter",
        )

    def _scan_unexpected_character(self) -> None:
        start = self._offset
        self._offset += 1
        self._emit(TokenKind.ERROR, start, self._offset)
        self._line_has_code = True
        self._report(
            message=f"unexpected character {self._source[start]!r}",
            start=start,
            end=self._offset,
            code="kv-unexpected-character",
        )

    def _finish_document(self) -> None:
        while len(self._indent_levels) > 1:
            self._indent_levels.pop()
            self._emit_structural(TokenKind.DEDENT)

        for opening, offset in self._delimiters:
            expected = _OPENING_DELIMITERS[opening]
            self._report(
                message=f"expected closing delimiter '{expected}'",
                start=offset,
                end=offset + 1,
                code="kv-unclosed-delimiter",
            )

        self._emit_structural(TokenKind.EOF)

    def _consume_newline(self) -> None:
        if self._source.startswith("\r\n", self._offset):
            self._offset += 2
        elif self._current in ("\r", "\n"):
            self._offset += 1

    def _is_newline_at(self, offset: int) -> bool:
        if offset >= len(self._source):
            return False

        return self._source[offset] in ("\r", "\n")

    def _emit(
        self,
        kind: TokenKind,
        start: int,
        end: int,
    ) -> None:
        self._tokens.append(
            Token(
                kind=kind,
                text=self._source[start:end],
                span=Span(start=start, end=end),
            )
        )

    def _emit_structural(self, kind: TokenKind) -> None:
        self._tokens.append(
            Token(
                kind=kind,
                text="",
                span=Span.empty(self._offset),
                synthetic=True,
            )
        )

    def _report(
        self,
        message: str,
        start: int,
        end: int,
        code: str,
    ) -> None:
        self._diagnostics.append(
            Diagnostic(
                message=message,
                span=Span(start=start, end=end),
                severity=DiagnosticSeverity.ERROR,
                code=code,
            )
        )

    @staticmethod
    def _is_identifier_start(character: str) -> bool:
        return bool(character) and (
            character == "_" or character.isalpha()
        )

    @staticmethod
    def _is_identifier_part(character: str) -> bool:
        if not character:
            return False

        return character == "_" or character.isalnum()

    @staticmethod
    def _is_string_prefix(value: str) -> bool:
        if not value:
            return False

        lowered = value.lower()
        return lowered in {"r", "u", "b", "f", "br", "rb", "fr", "rf"}
