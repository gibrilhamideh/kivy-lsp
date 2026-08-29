# src/kivy_lsp/model/diagnosticostic.py

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto

from kivy_lsp.model.span import Span


class DiagnosticSeverity(StrEnum):
    """The importance level of an analysis diagnostic."""

    ERROR = auto()
    WARNING = auto()
    INFORMATION = auto()
    HINT = auto()


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """An editor-independent problem found in source code."""

    message: str
    span: Span
    severity: DiagnosticSeverity
    code: str

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("diagnostic message cannot be empty")

        if not self.code:
            raise ValueError("diagnostic code cannot be empty")
