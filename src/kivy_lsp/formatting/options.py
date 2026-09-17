"""Options shared by the independent KV formatter and its adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FormatOptions:
    """Content width excludes leading indentation, includes continuations."""

    line_length: int = 79

    def __post_init__(self) -> None:
        if type(self.line_length) is not int or self.line_length < 4:
            raise ValueError("format.line-length must be an integer >= 4")
