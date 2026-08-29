# src/kivy_lsp/kv/parser.py

from __future__ import annotations

from dataclasses import dataclass

from kivy_lsp.kv.lexer import LexResult, lex
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
from kivy_lsp.kv.token_stream import TokenStream
from kivy_lsp.kv.tokens import Token, TokenKind
from kivy_lsp.model.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
)
from kivy_lsp.model.span import Span

_INLINE_SPACE_KINDS: frozenset[TokenKind] = frozenset(
    {
        TokenKind.WHITESPACE,
    }
)

_LINE_TRIVIA_KINDS: frozenset[TokenKind] = frozenset(
    {
        TokenKind.WHITESPACE,
        TokenKind.COMMENT,
    }
)

_EXPRESSION_OPENERS: frozenset[TokenKind] = frozenset(
    {
        TokenKind.LEFT_PAREN,
        TokenKind.LEFT_BRACKET,
        TokenKind.LEFT_BRACE,
    }
)

_EXPRESSION_CLOSERS: frozenset[TokenKind] = frozenset(
    {
        TokenKind.RIGHT_PAREN,
        TokenKind.RIGHT_BRACKET,
        TokenKind.RIGHT_BRACE,
    }
)


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Syntax tree, tokens, and diagnostics for one KV document."""

    document: DocumentNode
    tokens: tuple[Token, ...]
    diagnostics: tuple[Diagnostic, ...]


def parse(source: str) -> ParseResult:
    """Lex and parse a complete KV document."""

    return _Parser(lex(source)).run()


class _Parser:
    """Error-tolerant recursive-descent parser for KV syntax."""

    def __init__(self, lex_result: LexResult) -> None:
        self._tokens = lex_result.tokens
        self._stream = TokenStream(lex_result.tokens)
        self._diagnostics = list(lex_result.diagnostics)

    def run(self) -> ParseResult:
        items: list[DocumentItem] = []

        while not self._stream.at_end:
            self._skip_blank_lines()

            if self._stream.at_end:
                break

            if self._stream.check(TokenKind.DIRECTIVE):
                items.append(self._parse_directive())
                continue

            if self._stream.check(TokenKind.LESS_THAN):
                items.append(self._parse_rule())
                continue

            if self._stream.check(TokenKind.IDENTIFIER):
                items.append(self._parse_widget())
                continue

            if self._stream.check(TokenKind.DEDENT):
                self._stream.advance()
                continue

            self._report_current(
                message="expected a rule, directive, or root widget",
                code="kv-expected-document-item",
            )
            self._synchronize_line()

        eof = self._stream.current
        document = DocumentNode(
            span=Span(start=0, end=eof.span.end),
            items=tuple(items),
            eof=eof,
        )

        return ParseResult(
            document=document,
            tokens=self._tokens,
            diagnostics=tuple(self._diagnostics),
        )

    def _parse_directive(self) -> DirectiveNode:
        token = self._stream.advance()
        content = token.text[2:].strip()
        name, separator, arguments = content.partition(" ")

        if not name:
            self._report(
                token=token,
                message="expected a directive name after '#:'",
                code="kv-expected-directive-name",
            )

        self._consume_line_end()

        return DirectiveNode(
            span=token.span,
            token=token,
            name=name,
            arguments=arguments.strip() if separator else "",
        )

    def _parse_rule(self) -> RuleNode:
        opening = self._stream.advance()
        selectors: list[RuleSelectorNode] = []
        self._skip_inline_spaces()

        while not self._at_rule_header_end:
            if not self._stream.check(TokenKind.IDENTIFIER):
                self._report_current(
                    message="expected a class name in rule selector",
                    code="kv-expected-rule-name",
                )
                self._stream.advance()
                self._skip_inline_spaces()
                continue

            selectors.append(self._parse_rule_selector())
            self._skip_inline_spaces()

            if self._stream.consume(TokenKind.COMMA) is None:
                break

            self._skip_inline_spaces()

        closing = self._expect(
            kind=TokenKind.GREATER_THAN,
            message="expected '>' after rule selector",
            code="kv-expected-rule-close",
        )
        self._skip_inline_spaces()
        colon = self._expect(
            kind=TokenKind.COLON,
            message="expected ':' after rule declaration",
            code="kv-expected-colon",
        )
        body = self._parse_declaration_body()
        end = self._body_end(body, colon.span.end)

        return RuleNode(
            span=Span(start=opening.span.start, end=end),
            opening=opening,
            selectors=tuple(selectors),
            closing=closing,
            colon=colon,
            body=body,
        )

    @property
    def _at_rule_header_end(self) -> bool:
        return self._stream.current.kind in {
            TokenKind.GREATER_THAN,
            TokenKind.NEWLINE,
            TokenKind.EOF,
        }

    def _parse_rule_selector(self) -> RuleSelectorNode:
        name = self._expect(
            kind=TokenKind.IDENTIFIER,
            message="expected a class name",
            code="kv-expected-rule-name",
        )
        self._skip_inline_spaces()
        dynamic_marker = self._stream.consume(TokenKind.AT)
        base_names: list[Token] = []

        if dynamic_marker is not None:
            self._skip_inline_spaces()
            base_names.append(
                self._expect(
                    kind=TokenKind.IDENTIFIER,
                    message="expected a base class after '@'",
                    code="kv-expected-base-class",
                )
            )
            self._skip_inline_spaces()

            while self._is_operator("+"):
                self._stream.advance()
                self._skip_inline_spaces()
                base_names.append(
                    self._expect(
                        kind=TokenKind.IDENTIFIER,
                        message="expected a base class after '+'",
                        code="kv-expected-base-class",
                    )
                )
                self._skip_inline_spaces()

        end = name.span.end

        if base_names:
            end = base_names[-1].span.end
        elif dynamic_marker is not None:
            end = dynamic_marker.span.end

        return RuleSelectorNode(
            span=Span(start=name.span.start, end=end),
            name=name,
            dynamic_marker=dynamic_marker,
            base_names=tuple(base_names),
        )

    def _parse_widget(self) -> WidgetNode:
        name = self._expect(
            kind=TokenKind.IDENTIFIER,
            message="expected a widget class name",
            code="kv-expected-widget-name",
        )
        self._skip_inline_spaces()
        colon = self._expect(
            kind=TokenKind.COLON,
            message="expected ':' after widget declaration",
            code="kv-expected-colon",
        )
        body = self._parse_declaration_body()
        end = self._body_end(body, colon.span.end)

        return WidgetNode(
            span=Span(start=name.span.start, end=end),
            name=name,
            colon=colon,
            body=body,
        )

    def _parse_declaration_body(self) -> tuple[BodyNode, ...]:
        self._skip_inline_spaces()

        if self._stream.check(TokenKind.COMMENT):
            self._stream.advance()

        if self._stream.consume(TokenKind.NEWLINE) is not None:
            return self._parse_optional_indented_body()

        if self._stream.at_end:
            return ()

        self._report_current(
            message="unexpected content after declaration",
            code="kv-unexpected-declaration-content",
        )
        self._synchronize_line()
        return self._parse_optional_indented_body()

    def _parse_optional_indented_body(self) -> tuple[BodyNode, ...]:
        if not self._consume_indentation():
            return ()

        return self._parse_body()

    def _parse_body(self) -> tuple[BodyNode, ...]:
        body: list[BodyNode] = []

        while not self._stream.at_end:
            self._skip_blank_lines()

            if self._stream.consume(TokenKind.DEDENT) is not None:
                break

            if self._stream.check(TokenKind.INDENT):
                self._report_current(
                    message="unexpected indentation",
                    code="kv-unexpected-indentation",
                )
                self._stream.advance()
                continue

            if self._is_clear_previous():
                body.append(self._parse_property())
                continue

            if self._stream.check(TokenKind.IDENTIFIER):
                if self._is_widget_name(self._stream.current.text):
                    body.append(self._parse_widget())
                else:
                    body.append(self._parse_property())

                continue

            self._report_current(
                message="expected a widget or property declaration",
                code="kv-expected-body-item",
            )
            self._synchronize_line()

        return tuple(body)

    def _parse_property(self) -> PropertyNode:
        clear_previous: Token | None = None

        if self._is_clear_previous():
            clear_previous = self._stream.advance()

        start = (
            clear_previous.span.start
            if clear_previous is not None
            else self._stream.current.span.start
        )
        name_tokens = self._parse_property_name()
        self._skip_inline_spaces()
        colon = self._expect(
            kind=TokenKind.COLON,
            message="expected ':' after property name",
            code="kv-expected-colon",
        )
        self._skip_inline_spaces()

        if self._has_inline_expression:
            value = self._parse_inline_expression()
            self._consume_line_end()
            end = value.span.end if value is not None else colon.span.end

            return PropertyNode(
                span=Span(start=start, end=end),
                clear_previous=clear_previous,
                name_tokens=name_tokens,
                colon=colon,
                value=value,
                body=(),
            )

        self._consume_line_end()
        value, body = self._parse_property_content()
        fallback = colon.span.end

        if value is not None:
            fallback = value.span.end

        end = self._body_end(body, fallback)

        return PropertyNode(
            span=Span(start=start, end=end),
            clear_previous=clear_previous,
            name_tokens=name_tokens,
            colon=colon,
            value=value,
            body=body,
        )

    def _parse_property_name(self) -> tuple[Token, ...]:
        tokens: list[Token] = [
            self._expect(
                kind=TokenKind.IDENTIFIER,
                message="expected a property name",
                code="kv-expected-property-name",
            )
        ]

        while self._stream.check(TokenKind.DOT):
            tokens.append(self._stream.advance())
            tokens.append(
                self._expect(
                    kind=TokenKind.IDENTIFIER,
                    message="expected a name after '.'",
                    code="kv-expected-property-name",
                )
            )

        return tuple(tokens)

    @property
    def _has_inline_expression(self) -> bool:
        return self._stream.current.kind not in {
            TokenKind.COMMENT,
            TokenKind.NEWLINE,
            TokenKind.DEDENT,
            TokenKind.EOF,
        }

    def _parse_inline_expression(self) -> ExpressionNode | None:
        tokens: list[Token] = []
        depth = 0

        while not self._stream.at_end:
            token = self._stream.current

            if token.kind is TokenKind.COMMENT and depth == 0:
                break

            if token.kind is TokenKind.NEWLINE and depth == 0:
                break

            if token.kind in {TokenKind.INDENT, TokenKind.DEDENT}:
                break

            token = self._stream.advance()
            tokens.append(token)

            if token.kind in _EXPRESSION_OPENERS:
                depth += 1
            elif token.kind in _EXPRESSION_CLOSERS:
                depth = max(0, depth - 1)

        self._trim_expression_end(tokens)
        return self._make_expression(tokens)

    def _parse_property_content(
        self,
    ) -> tuple[ExpressionNode | None, tuple[BodyNode, ...]]:
        if not self._consume_indentation():
            return None, ()

        self._skip_blank_lines()

        if self._stream.consume(TokenKind.DEDENT) is not None:
            return None, ()

        if self._looks_like_body_declaration():
            return None, self._parse_body()

        return self._parse_block_expression(), ()

    def _parse_block_expression(self) -> ExpressionNode | None:
        tokens: list[Token] = []
        nested_indentation = 0

        while not self._stream.at_end:
            token = self._stream.current

            if token.kind is TokenKind.INDENT:
                nested_indentation += 1
                self._stream.advance()
                continue

            if token.kind is TokenKind.DEDENT:
                if nested_indentation == 0:
                    self._stream.advance()
                    break

                nested_indentation -= 1
                self._stream.advance()
                continue

            tokens.append(self._stream.advance())

        self._trim_expression_end(tokens)
        return self._make_expression(tokens)

    def _looks_like_body_declaration(self) -> bool:
        checkpoint = self._stream.mark()

        if self._is_clear_previous():
            self._stream.advance()

        if self._stream.consume(TokenKind.IDENTIFIER) is None:
            self._stream.restore(checkpoint)
            return False

        while self._stream.consume(TokenKind.DOT) is not None:
            if self._stream.consume(TokenKind.IDENTIFIER) is None:
                self._stream.restore(checkpoint)
                return False

        self._skip_inline_spaces()
        result = self._stream.check(TokenKind.COLON)
        self._stream.restore(checkpoint)
        return result

    def _consume_indentation(self) -> bool:
        while True:
            self._stream.skip(_LINE_TRIVIA_KINDS)

            if self._stream.consume(TokenKind.NEWLINE) is None:
                break

        return self._stream.consume(TokenKind.INDENT) is not None

    def _skip_blank_lines(self) -> None:
        while True:
            self._stream.skip(_LINE_TRIVIA_KINDS)

            if self._stream.consume(TokenKind.NEWLINE) is None:
                break

    def _skip_inline_spaces(self) -> None:
        self._stream.skip(_INLINE_SPACE_KINDS)

    def _consume_line_end(self) -> None:
        self._stream.skip(_LINE_TRIVIA_KINDS)

        if self._stream.consume(TokenKind.NEWLINE) is not None:
            return

        if self._stream.at_end:
            return

        if self._stream.check(TokenKind.DEDENT):
            return

        self._synchronize_line()

    def _synchronize_line(self) -> None:
        while self._stream.current.kind not in {
            TokenKind.NEWLINE,
            TokenKind.DEDENT,
            TokenKind.EOF,
        }:
            self._stream.advance()

        self._stream.consume(TokenKind.NEWLINE)

    def _expect(
        self,
        kind: TokenKind,
        message: str,
        code: str,
    ) -> Token:
        token = self._stream.consume(kind)

        if token is not None:
            return token

        current = self._stream.current
        self._report(
            token=current,
            message=message,
            code=code,
        )
        return Token.missing(
            kind=kind,
            offset=current.span.start,
        )

    def _report_current(self, message: str, code: str) -> None:
        self._report(
            token=self._stream.current,
            message=message,
            code=code,
        )

    def _report(
        self,
        token: Token,
        message: str,
        code: str,
    ) -> None:
        self._diagnostics.append(
            Diagnostic(
                message=message,
                span=token.span,
                severity=DiagnosticSeverity.ERROR,
                code=code,
            )
        )

    def _is_clear_previous(self) -> bool:
        return self._is_operator("-")

    def _is_operator(self, value: str) -> bool:
        token = self._stream.current
        return (
            token.kind is TokenKind.OPERATOR
            and token.text == value
        )

    @staticmethod
    def _is_widget_name(name: str) -> bool:
        return bool(name) and name[0].isupper()

    @staticmethod
    def _trim_expression_end(tokens: list[Token]) -> None:
        while tokens and tokens[-1].kind in {
            TokenKind.WHITESPACE,
            TokenKind.COMMENT,
            TokenKind.NEWLINE,
        }:
            tokens.pop()

    @staticmethod
    def _make_expression(
        tokens: list[Token],
    ) -> ExpressionNode | None:
        if not tokens:
            return None

        return ExpressionNode(
            span=Span(
                start=tokens[0].span.start,
                end=tokens[-1].span.end,
            ),
            tokens=tuple(tokens),
        )

    @staticmethod
    def _body_end(
        body: tuple[BodyNode, ...],
        fallback: int,
    ) -> int:
        if not body:
            return fallback

        return body[-1].span.end
