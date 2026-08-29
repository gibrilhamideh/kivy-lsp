from pathlib import Path

from kivy_lsp.analysis.python_ids_completion import (
    PythonIdsCompletionEngine,
)
from kivy_lsp.kv.index import KvIndex
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.workspace.document import TextDocument
from kivy_lsp.workspace.kv_scanner import KvScanner


def _python_index() -> PythonIndex:
    index = PythonIndex()
    modules = (
        (
            "widgets",
            "file:///widgets.py",
            (
                "class Button:\n"
                "    text: str\n"
                "    disabled: bool\n"
                "    def trigger(self) -> None:\n"
                "        pass\n"
            ),
        ),
        (
            "view",
            "file:///view.py",
            "class RootView:\n    pass\n",
        ),
    )

    for module_name, uri, source in modules:
        result = index_python_module(
            TextDocument(
                uri=uri,
                text=source,
            ),
            module_name,
        )
        assert result.module is not None
        index.replace(result.module)

    return index


def _kv_index() -> KvIndex:
    source = (
        "<RootView>:\n"
        "    Button:\n"
        "        id: submit_button\n"
        "    Label:\n"
        "        id: title_label\n"
    )
    uri = "file:///view.kv"
    scanner = KvScanner((Path.cwd(),))
    index = KvIndex()
    index.replace(
        uri,
        scanner.scan_text(uri, source),
    )
    return index


def _complete(expression: str) -> list[tuple[str, str]]:
    source = (
        "class RootView:\n"
        "    def test(self):\n"
        f"        {expression}"
    )
    document = TextDocument(
        uri="file:///view.py",
        text=source,
    )
    engine = PythonIdsCompletionEngine(
        _python_index(),
        _kv_index(),
    )
    result = engine.complete(document, len(source))
    assert result is not None
    return [
        (
            item.label,
            item.insert_text,
        )
        for item in result.items
    ]


def _complete_in_source(
    source: str,
) -> list[tuple[str, str]]:
    document = TextDocument(
        uri="file:///view.py",
        text=source,
    )
    engine = PythonIdsCompletionEngine(
        _python_index(),
        _kv_index(),
    )
    result = engine.complete(document, len(source))
    assert result is not None
    return [
        (
            item.label,
            item.insert_text,
        )
        for item in result.items
    ]


def test_dot_id_completion() -> None:
    assert _complete("self.ids.") == [
        (
            "submit_button",
            "submit_button",
        ),
        (
            "title_label",
            "title_label",
        ),
    ]


def test_subscript_id_completion_closes_expression() -> None:
    assert _complete('self.ids["sub') == [
        (
            "submit_button",
            'submit_button"]',
        )
    ]


def test_dot_id_member_completion_uses_widget_type() -> None:
    assert _complete("self.ids.submit_button.tr") == [
        (
            "trigger",
            "trigger",
        )
    ]


def test_subscript_id_member_completion_uses_widget_type() -> None:
    expression = 'self.ids["submit_button"].te'
    assert _complete(expression) == [
        (
            "text",
            "text",
        )
    ]


def test_class_detection_ignores_class_text_in_docstrings() -> None:
    source = (
        "class RootView:\n"
        "    \"\"\"\n"
        "class NotARealClass:\n"
        "    \"\"\"\n"
        "    def test(self):\n"
        "        self.ids."
    )

    assert _complete_in_source(source) == [
        (
            "submit_button",
            "submit_button",
        ),
        (
            "title_label",
            "title_label",
        ),
    ]
