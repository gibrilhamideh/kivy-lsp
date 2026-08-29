# src/kivy_lsp/model/span.py

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class Span:
    """
    Half-open range of character offsets in source text.

    The start offset is included and the end offset is excluded. An empty
    span has equal start and end offsets.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("span start cannot be negative")

        if self.end < self.start:
            raise ValueError("span end cannot be before its start")

    @classmethod
    def empty(cls, offset: int) -> Span:
        return cls(
            start=offset,
            end=offset,
        )

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def is_empty(self) -> bool:
        return self.start == self.end

    def contains(self, offset: int) -> bool:
        return self.start <= offset < self.end

    def contains_cursor(self, offset: int) -> bool:
        return self.start <= offset <= self.end

    def encloses(self, other: Span) -> bool:
        return self.start <= other.start and other.end <= self.end

    def overlaps(self, other: Span) -> bool:
        return self.start < other.end and other.start < self.end

    def cover(self, other: Span) -> Span:
        return Span(
            start=min(self.start, other.start),
            end=max(self.end, other.end),
        )
