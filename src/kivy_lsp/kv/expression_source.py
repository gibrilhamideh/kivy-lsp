"""Embedded Python source and its positions in the original KV document."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

from kivy_lsp.model.span import Span


@dataclass(frozen=True, slots=True)
class EmbeddedPythonSource:
    """Kivy-normalized Python text with absolute source-position mapping.

    Kivy removes blank/comment lines and strips expression-line whitespace
    before compiling a property. Mapping the remaining lines lets features
    use Python AST byte columns without losing KV character offsets.
    """

    text: str
    span: Span
    statement_block: bool
    _lines: tuple[str, ...]
    _line_starts: tuple[int, ...]

    @classmethod
    def from_source(
        cls,
        source: str,
        span: Span,
        *,
        statement_block: bool = False,
    ) -> EmbeddedPythonSource:
        lines: list[str] = []
        starts: list[int] = []
        offset = span.start

        for line in source[span.start:span.end].splitlines(keepends=True):
            content = line.strip()

            if content and not content.startswith("#"):
                prefix_length = len(line) - len(line.lstrip())
                lines.append(content)
                starts.append(offset + prefix_length)

            offset += len(line)

        return cls(
            text="\n".join(lines),
            span=span,
            statement_block=statement_block,
            _lines=tuple(lines),
            _line_starts=tuple(starts),
        )

    @property
    def mode(self) -> Literal["exec", "eval"]:
        return "exec" if self.statement_block else "eval"

    def offset(self, line: int, byte_column: int) -> int:
        """Convert a one-based AST line and UTF-8 column to a KV offset."""
        index = max(0, line - 1)

        if index >= len(self._lines):
            return self.span.end

        encoded = self._lines[index].encode("utf-8")
        prefix = encoded[:max(0, byte_column)].decode("utf-8", "ignore")
        return self._line_starts[index] + len(prefix)

    def node_span(self, node: ast.AST) -> Span:
        """Return an AST node's absolute character span in the KV file."""
        line = getattr(node, "lineno", None)
        end_line = getattr(node, "end_lineno", None)

        if line is None or end_line is None:
            return self.span

        return Span(
            start=self.offset(line, getattr(node, "col_offset", 0)),
            end=self.offset(end_line, getattr(node, "end_col_offset", 0)),
        )
