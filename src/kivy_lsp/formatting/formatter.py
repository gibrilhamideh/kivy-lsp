"""Safe whole-document formatting using the shared KV syntax tree."""

from __future__ import annotations

import ast
import re
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass

from kivy_lsp.formatting.expressions import (
    ExpressionLayout,
    UnsupportedExpression,
    check_source_tokens,
    layout_expression,
    layout_statement,
)
from kivy_lsp.formatting.options import FormatOptions
from kivy_lsp.kv.nodes import (
    DocumentNode,
    PropertyNode,
    RuleNode,
    WidgetNode,
)
from kivy_lsp.kv.parser import parse

_CONTROL = re.compile(r"^\s*#\s*fmt:\s*(off|on|skip)\s*$")
_COMMENT = re.compile(r"^\s*#")
_NEWLINE = re.compile(r"\r\n|\r|\n")
_COMPOUND = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith,
    ast.Try, ast.TryStar, ast.FunctionDef, ast.AsyncFunctionDef,
    ast.ClassDef, ast.Match,
)

type _Construct = RuleNode | WidgetNode | PropertyNode


@dataclass(frozen=True, slots=True)
class FormatIssue:
    """A nonfatal formatter limitation at a one-based line number."""

    line: int
    message: str
    code: str = "format-skipped"


@dataclass(frozen=True, slots=True)
class FormatResult:
    text: str
    issues: tuple[FormatIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class _Edit:
    start: int
    end: int
    lines: tuple[str, ...]


def format_source(
    source: str, options: FormatOptions | None = None,
) -> FormatResult:
    """Format independent safe regions; preserve unsupported source exactly."""

    return _Formatter(source, options or FormatOptions()).run()


class _Formatter:
    def __init__(self, source: str, options: FormatOptions) -> None:
        self.source = source
        self.options = options
        self.lines = _NEWLINE.split(source)
        self.starts = [0]
        for match in _NEWLINE.finditer(source):
            self.starts.append(match.end())
        match = _NEWLINE.search(source)
        self.newline = match.group() if match else "\n"
        self.parsed = parse(source)
        self.constructs = list(self._walk(self.parsed.document))
        self.protected = self._protected_lines()
        self.issues: list[FormatIssue] = []
        self.edits: list[_Edit] = []
        self.normalize_indentation = True

    def run(self) -> FormatResult:
        self._visit(self.parsed.document, 0)
        # A preserved region must remain at its existing structural depth.
        # Do not partially normalize a document using a different indent unit.
        if self.issues or self.protected:
            self.normalize_indentation = False
            self.edits.clear()
            self.issues.clear()
            self._visit(self.parsed.document, 0)

        lines = list(self.lines)
        edits = sorted(self.edits, key=lambda item: item.start, reverse=True)
        for edit in edits:
            lines[edit.start:edit.end] = edit.lines
        text = self.newline.join(lines)
        for number, line in enumerate(lines, 1):
            if len(line.lstrip(" \t")) > self.options.line_length:
                self.issues.append(FormatIssue(
                    number,
                    "content exceeds line-length; preserved token, chain, "
                    "comment, directive, or unsupported region",
                    "format-overflow",
                ))
        return FormatResult(text, tuple(self.issues))

    def _walk(self, node: DocumentNode | _Construct) -> Iterator[_Construct]:
        children = node.items if isinstance(node, DocumentNode) else node.body
        for child in children:
            if isinstance(child, (RuleNode, WidgetNode, PropertyNode)):
                yield child
                yield from self._walk(child)

    def _line(self, offset: int) -> int:
        return bisect_right(self.starts, offset) - 1

    def _bounds(self, node: _Construct) -> tuple[int, int]:
        return self._line(node.span.start), self._line(node.span.end) + 1

    def _protected_lines(self) -> set[int]:
        protected: set[int] = set()
        off = False
        for index, line in enumerate(self.lines):
            match = _CONTROL.match(line)
            action = match.group(1) if match else None
            if off or action:
                protected.add(index)
            if action == "off":
                off = True
            elif action == "on":
                off = False
            elif action == "skip":
                next_line = index + 1
                while next_line < len(self.lines):
                    content = self.lines[next_line].lstrip()
                    if content.startswith("#:"):
                        protected.add(next_line)
                        break
                    if content and not content.startswith("#"):
                        break
                    next_line += 1
                if next_line in protected:
                    continue
                for node in self.constructs:
                    start, end = self._bounds(node)
                    if start >= next_line:
                        protected.update(range(start, end))
                        break
        return protected

    def _visit(self, node: DocumentNode | _Construct, depth: int) -> None:
        if isinstance(node, DocumentNode):
            for item in node.items:
                if isinstance(item, (RuleNode, WidgetNode, PropertyNode)):
                    self._visit(item, depth)
            return

        start, end = self._bounds(node)
        if isinstance(node, PropertyNode) and node.value is not None:
            if not any(line in self.protected for line in range(start, end)):
                self._property(node, depth, start, end)
            return

        if start not in self.protected and self.normalize_indentation:
            line = self.lines[start]
            self.edits.append(_Edit(
                start, start + 1, ("    " * depth + line.lstrip(" \t"),),
            ))
        for child in node.body:
            self._visit(child, depth + 1)

    def _property(
        self, node: PropertyNode, depth: int, start: int, end: int,
    ) -> None:
        assert node.value is not None
        original = self.lines[start:end]
        first = self.lines[start]
        indent = "    " * depth if self.normalize_indentation else (
            first[:len(first) - len(first.lstrip(" \t"))]
        )
        for diagnostic in self.parsed.diagnostics:
            if node.span.start <= diagnostic.span.start <= node.span.end:
                self._skip(start, "region has KV syntax errors")
                return

        value_line = self._line(node.value.span.start)
        value_end = self._line(node.value.span.end)
        column = node.value.span.start - self.starts[value_line]
        raw = [self.lines[value_line][column:]]
        baseline = len(self.lines[value_line]) - len(
            self.lines[value_line].lstrip(" \t")
        )
        for line in self.lines[value_line + 1:value_end + 1]:
            raw.append(line[baseline:] if value_line > start else line)
        value = "\n".join(raw)
        # The raw physical region includes comments outside ExpressionNode's
        # trimmed span. Token-aware expression layout preserves them by skip.
        if any(_COMMENT.match(line) for line in original):
            self._skip(start, "comments retain their original association")
            return
        header_end = node.colon.span.end - self.starts[start]
        if value_line > start and first[header_end:].strip():
            self._skip(start, "commented property header retains its layout")
            return

        name = ("-" if node.clear_previous else "") + node.name
        header = name + ":"
        try:
            layouts = self._layouts(value, node.is_event_handler)
        except UnsupportedExpression as error:
            self._skip(start, str(error))
            return

        compact = layouts[0].compact if len(layouts) == 1 else None
        width = self.options.line_length
        if compact is not None and len(header + " " + compact) <= width:
            result = (indent + header + " " + compact,)
        else:
            continuation = indent + self._indent_unit()
            output = [indent + header]
            for layout in layouts:
                for index, line in enumerate(layout.lines):
                    suffix = " \\" if index < len(layout.lines) - 1 else ""
                    output.append(continuation + line + suffix)
            result = tuple(output)
        self.edits.append(_Edit(start, end, result))

    def _layouts(self, value: str, handler: bool) -> list[ExpressionLayout]:
        width = self.options.line_length
        if not handler:
            return [layout_expression(value, width)]
        try:
            tree = ast.parse(value, mode="exec")
        except (SyntaxError, ValueError) as error:
            raise UnsupportedExpression("incomplete event handler") from error
        compound = any(isinstance(node, _COMPOUND) for node in tree.body)
        if not tree.body or compound:
            raise UnsupportedExpression(
                "complex event suites retain their original layout"
            )
        # Any comments between statements must also be preserved. A layout
        # pass on the whole source inspects tokens without changing the file.
        check_source_tokens(value)
        layouts = []
        for statement in tree.body:
            segment = ast.get_source_segment(value, statement)
            if segment is None:
                raise UnsupportedExpression("event statement has no source")
            layouts.append(layout_statement(segment, width))
        return layouts

    def _indent_unit(self) -> str:
        if self.normalize_indentation:
            return "    "
        for node in self.constructs:
            start, _ = self._bounds(node)
            line = self.lines[start]
            indent = line[:len(line) - len(line.lstrip(" \t"))]
            if indent:
                return "\t" if "\t" in indent else " " * len(indent)
        return "    "

    def _skip(self, line: int, message: str) -> None:
        self.issues.append(FormatIssue(line + 1, message))
