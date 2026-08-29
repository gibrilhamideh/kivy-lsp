# src/kivy_lsp/workspace/document.py

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from enum import StrEnum

from kivy_lsp.model.span import Span

_NEWLINE_PATTERN = re.compile(r"\r\n|\r|\n")


class PositionEncoding(StrEnum):
    """Character encoding used for LSP position calculations."""

    UTF8 = "utf-8"
    UTF16 = "utf-16"
    UTF32 = "utf-32"


@dataclass(frozen=True, slots=True, order=True)
class TextPosition:
    """A zero-based line and character position."""

    line: int
    character: int

    def __post_init__(self) -> None:
        if self.line < 0:
            raise ValueError("position line cannot be negative")

        if self.character < 0:
            raise ValueError("position character cannot be negative")


@dataclass(frozen=True, slots=True)
class TextRange:
    """A range between two text positions."""

    start: TextPosition
    end: TextPosition

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("range end cannot be before its start")


@dataclass(frozen=True, slots=True)
class TextDocument:
    """An immutable snapshot of an open text document."""

    uri: str
    text: str
    version: int | None = None
    _line_starts: tuple[int, ...] = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not self.uri:
            raise ValueError("document URI cannot be empty")

        starts = [0]

        for match in _NEWLINE_PATTERN.finditer(self.text):
            starts.append(match.end())

        object.__setattr__(self, "_line_starts", tuple(starts))

    @property
    def line_count(self) -> int:
        return len(self._line_starts)

    def updated(
        self,
        text: str,
        version: int | None,
    ) -> TextDocument:
        """Create a new immutable snapshot with updated contents."""

        return TextDocument(
            uri=self.uri,
            text=text,
            version=version,
        )

    def line_text(self, line: int) -> str:
        """Return one line without its newline characters."""

        start, end = self._line_bounds(line)
        return self.text[start:end]

    def offset_at(
        self,
        position: TextPosition,
        encoding: PositionEncoding = PositionEncoding.UTF16,
    ) -> int:
        """Convert a line and character position into a source offset."""

        if position.line >= self.line_count:
            return len(self.text)

        line_start, line_end = self._line_bounds(position.line)
        line_text = self.text[line_start:line_end]
        relative = self._offset_for_units(
            text=line_text,
            units=position.character,
            encoding=encoding,
        )
        return line_start + relative

    def position_at(
        self,
        offset: int,
        encoding: PositionEncoding = PositionEncoding.UTF16,
    ) -> TextPosition:
        """Convert an absolute source offset into a text position."""

        if offset < 0 or offset > len(self.text):
            raise ValueError("document offset is out of range")

        line = bisect_right(self._line_starts, offset) - 1
        line_start, line_end = self._line_bounds(line)
        content_offset = min(offset, line_end)
        content = self.text[line_start:content_offset]

        return TextPosition(
            line=line,
            character=self._unit_length(content, encoding),
        )

    def range_at(
        self,
        span: Span,
        encoding: PositionEncoding = PositionEncoding.UTF16,
    ) -> TextRange:
        """Convert an internal source span into a text range."""

        if span.end > len(self.text):
            raise ValueError("span extends beyond the document")

        return TextRange(
            start=self.position_at(span.start, encoding),
            end=self.position_at(span.end, encoding),
        )

    def span_at(
        self,
        text_range: TextRange,
        encoding: PositionEncoding = PositionEncoding.UTF16,
    ) -> Span:
        """Convert a text range into an internal source span."""

        return Span(
            start=self.offset_at(text_range.start, encoding),
            end=self.offset_at(text_range.end, encoding),
        )

    def _line_bounds(self, line: int) -> tuple[int, int]:
        if line < 0 or line >= self.line_count:
            raise ValueError("document line is out of range")

        start = self._line_starts[line]

        if line + 1 >= self.line_count:
            return start, len(self.text)

        end = self._line_starts[line + 1]

        if self.text[end - 2:end] == "\r\n":
            end -= 2
        elif end > start and self.text[end - 1] in ("\r", "\n"):
            end -= 1

        return start, end

    @staticmethod
    def _offset_for_units(
        text: str,
        units: int,
        encoding: PositionEncoding,
    ) -> int:
        if encoding is PositionEncoding.UTF32:
            return min(units, len(text))

        consumed = 0

        for offset, character in enumerate(text):
            width = TextDocument._unit_length(
                character,
                encoding,
            )

            if consumed + width > units:
                return offset

            consumed += width

        return len(text)

    @staticmethod
    def _unit_length(
        text: str,
        encoding: PositionEncoding,
    ) -> int:
        if encoding is PositionEncoding.UTF8:
            return len(text.encode("utf-8"))

        if encoding is PositionEncoding.UTF16:
            return len(text.encode("utf-16-le")) // 2

        return len(text)
