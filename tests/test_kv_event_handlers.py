from pathlib import Path

from kivy_lsp.analysis.diagnostics import KvDiagnosticAnalyzer
from kivy_lsp.analysis.scope_builder import build_kv_semantic_model
from kivy_lsp.config import ServerConfig
from kivy_lsp.kv.parser import parse
from kivy_lsp.model.diagnostic import Diagnostic
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.workspace.document import TextDocument

_PYTHON_SOURCE = (
    "class RootView:\n"
    "    def commit(\n"
    "        self,\n"
    "        key: str,\n"
    "        value: int,\n"
    "    ) -> None:\n"
    "        pass\n"
    "\n"
    "class UINumericField:\n"
    "    value: int | None\n"
)


def _diagnostics(source: str) -> tuple[Diagnostic, ...]:
    root = Path.cwd()
    config = ServerConfig(
        project_root=root,
        source_roots=(root,),
        kv_paths=(root,),
    )
    index = PythonIndex()
    indexed = index_python_module(
        TextDocument(
            uri="file:///widgets.py",
            text=_PYTHON_SOURCE,
        ),
        "widgets",
    )
    assert indexed.module is not None
    index.replace(indexed.module)

    document = TextDocument(
        uri="file:///editor.kv",
        text=source,
    )
    parse_result = parse(source)
    semantic_model = build_kv_semantic_model(
        document,
        parse_result,
        index,
        config,
    )

    return KvDiagnosticAnalyzer(
        index,
        config,
    ).analyze(
        document,
        parse_result,
        semantic_model,
    )


def test_event_handler_accepts_multiple_statements() -> None:
    source = (
        "<RootView>:\n"
        "    UINumericField:\n"
        "        on_commit:\n"
        '            root.commit("sold", self.value)\n'
        "            self.value = None\n"
    )

    diagnostics = _diagnostics(source)

    assert not any(
        diagnostic.code == "kv-invalid-expression"
        for diagnostic in diagnostics
    )


def test_event_handler_preserves_nested_python_indentation() -> None:
    source = (
        "<RootView>:\n"
        "    UINumericField:\n"
        "        on_commit:\n"
        "            if self.value is not None:\n"
        '                root.commit("sold", self.value)\n'
        "            else:\n"
        "                self.value = 0\n"
    )

    diagnostics = _diagnostics(source)

    assert diagnostics == ()


def test_event_handler_diagnostic_maps_to_later_statement() -> None:
    source = (
        "<RootView>:\n"
        "    UINumericField:\n"
        "        on_commit:\n"
        '            root.commit("sold", 1)\n'
        "            self.missing = None\n"
    )

    diagnostics = _diagnostics(source)
    missing = next(
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "kv-unknown-member"
    )

    assert source[missing.span.start:missing.span.end] == "missing"


def test_invalid_event_handler_reports_statement_syntax() -> None:
    source = (
        "<RootView>:\n"
        "    UINumericField:\n"
        "        on_commit:\n"
        "            if self.value is not None:\n"
    )

    diagnostics = _diagnostics(source)
    invalid = next(
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "kv-invalid-expression"
    )

    assert invalid.message.startswith(
        "Invalid Python event handler:"
    )
