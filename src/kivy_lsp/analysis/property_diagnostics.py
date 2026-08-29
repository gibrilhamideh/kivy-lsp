# src/kivy_lsp/analysis/property_diagnostics.py

from __future__ import annotations

from kivy_lsp.analysis.property_resolution import (
    KivyPropertyResolver,
)
from kivy_lsp.analysis.scope import KvValue
from kivy_lsp.analysis.type_compatibility import (
    KivyPropertyTypeChecker,
    TypeCompatibility,
)
from kivy_lsp.analysis.value_inference import KvInferredValue
from kivy_lsp.model.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
)
from kivy_lsp.model.span import Span
from kivy_lsp.python.index import PythonIndex


class KvPropertyDiagnosticAnalyzer:
    """Validate values assigned to known Kivy properties."""

    def __init__(
        self,
        python_index: PythonIndex,
    ) -> None:
        self._property_resolver = KivyPropertyResolver(
            python_index,
        )
        self._type_checker = KivyPropertyTypeChecker()

    def analyze(
        self,
        *,
        widget_value: KvValue,
        property_name: str,
        value: KvInferredValue,
        value_span: Span,
        sequence_length: int | None = None,
    ) -> tuple[Diagnostic, ...]:
        resolved = self._property_resolver.resolve(
            widget_value,
            property_name,
        )

        if resolved is None:
            return ()

        property_info = resolved.info

        if property_info is None:
            return ()

        result = self._type_checker.check(
            property_info,
            value,
            sequence_length=sequence_length,
        )

        if result.compatibility in {
            TypeCompatibility.COMPATIBLE,
            TypeCompatibility.UNKNOWN,
        }:
            return ()

        if result.compatibility is TypeCompatibility.INCOMPATIBLE:
            severity = DiagnosticSeverity.ERROR
            code = "kv-incompatible-property-value"
        else:
            severity = DiagnosticSeverity.WARNING
            code = "kv-possibly-incompatible-property-value"

        reason = result.reason

        if reason is None:
            reason = (
                f"Expected {result.expected}, but received "
                f"{result.actual}."
            )

        message = (
            f'Invalid value for property "{property_name}": '
            f"{reason}"
        )

        return (
            Diagnostic(
                message=message,
                span=value_span,
                severity=severity,
                code=code,
            ),
        )
