from itertools import pairwise

import pytest
from lsprotocol import types
from pygls.lsp.server import LanguageServer

from kivy_lsp.analysis.editor_features import KvEditorFeatures, selection_spans
from kivy_lsp.analysis.scope_builder import build_kv_semantic_model
from kivy_lsp.config import ServerConfig
from kivy_lsp.features.editor_features import register_editor_features
from kivy_lsp.features.navigation import register_navigation
from kivy_lsp.features.refactoring import register_refactoring
from kivy_lsp.kv.index import KvIndex
from kivy_lsp.kv.parser import parse
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.workspace.document import TextDocument
from kivy_lsp.workspace.kv_scanner import KvScanner

PYTHON_SOURCE = '''from kivy.properties import OptionProperty, StringProperty
class Widget:
    pass

class Panel(Widget):
    """A panel with useful details."""
    title = StringProperty("initial")
    orientation = OptionProperty(
        "horizontal", options=["horizontal", "vertical"],
    )

    def describe(self, value: str, count: int = 1, *, unit: str = "") -> str:
        """Render a readable description."""
        return value

    def inner(self, first: int, second: int) -> int:
        return first
'''


def analyzed(source, tmp_path):
    python_index = PythonIndex()
    python_document = TextDocument(
        (tmp_path / "widgets.py").as_uri(), PYTHON_SOURCE
    )
    python_index.replace(
        index_python_module(python_document, "widgets").module
    )
    config = ServerConfig(tmp_path, (tmp_path,), (tmp_path,))
    document = TextDocument((tmp_path / "view.kv").as_uri(), source, 5)
    result = parse(source)
    kv_index = KvIndex()
    kv_index.replace(
        document.uri, KvScanner(()).scan_result(document.uri, result)
    )
    model = build_kv_semantic_model(
        document,
        result,
        python_index,
        config,
        kv_index,
    )
    engine = KvEditorFeatures(python_index, kv_index, config)
    return document, result, model, engine


def test_hover_type_documentation_and_property_metadata(tmp_path):
    source = '<Panel>:\n    title: root.describe("x")\n'
    document, result, model, engine = analyzed(source, tmp_path)
    class_hover = engine.hover(document, result, model, source.index("Panel"))
    assert "A panel with useful details." in class_hover.markdown
    hover = engine.hover(document, result, model, source.index("describe"))
    assert "describe" in hover.markdown
    assert "Render a readable description." in hover.markdown
    assert "-> str" in hover.markdown
    prop = engine.hover(document, result, model, source.index("title"))
    assert "str" in prop.markdown


@pytest.mark.parametrize(
    ("call", "needle", "active"),
    [
        ('root.describe("hello", 2)', "2", 1),
        ('root.describe("hello", unit="kg")', "kg", 2),
        ("root.describe(root.inner(1, 2), 3)", "2", 1),
        ("root.describe(root.inner(1, 2), 3)", "3", 1),
    ],
)
def test_signature_active_argument(tmp_path, call, needle, active):
    source = "<Panel>:\n    title: " + call + "\n"
    document, result, model, engine = analyzed(source, tmp_path)
    offset = source.rindex(needle) + len(needle)
    help = engine.signature_help(document, result, model, offset)
    assert help is not None
    assert help.active_parameter == active
    assert "self" not in help.label
    if needle == "2" and "inner" in call:
        assert help.label.startswith("inner(")
    else:
        assert help.label.startswith("describe(")


def test_signature_multiline_and_incomplete_calls(tmp_path):
    source = (
        "<Panel>:\n    title:\n        root.describe( \\\n"
        '        "😀, text", \\\n        unit="kg"\n'
    )
    document, result, model, engine = analyzed(source, tmp_path)
    help = engine.signature_help(
        document,
        result,
        model,
        source.index("kg") + 2,
    )
    assert help is not None
    assert help.active_parameter == 2


def test_selection_ranges_are_strictly_nested(tmp_path):
    source = "<Panel>:\n    Widget:\n        title: root.title\n"
    _, parsed, _, _ = analyzed(source, tmp_path)
    ranges = selection_spans(parsed, source.rindex("title") + 1)
    assert source[ranges[0].start : ranges[0].end] == "title"
    assert all(
        outer.encloses(inner) and outer != inner
        for inner, outer in pairwise(ranges)
    )
    assert ranges[-1] == parsed.document.span


class FakeWorkspace:
    def __init__(self, source, tmp_path):
        document, parsed, model, engine = analyzed(source, tmp_path)
        self.current = document
        self.parsed = parsed
        self.model = model
        self.python_index = engine.python_index
        self.kv_index = engine.kv_index
        self.config = ServerConfig(tmp_path, (tmp_path,), (tmp_path,))
        self.translation_index = None
        self.problems = ()

    def document(self, uri):
        return self.current if uri == self.current.uri else None

    def source_document(self, uri):
        return self.document(uri)

    def kv_result(self, uri):
        return self.parsed

    def semantic_model(self, uri):
        return self.model

    def diagnostics_for(self, uri):
        return self.problems

    def kv_documents(self):
        return (self.current,)


def feature(server, method):
    return server.protocol.fm.features[method]


def test_lsp_registrations_and_folds(tmp_path):
    workspace = FakeWorkspace(
        '<Panel>:\n    title:\n        "a" + \\\n        "b"\n',
        tmp_path,
    )
    server = LanguageServer("test", "0")
    register_editor_features(server, lambda: workspace)
    register_refactoring(server, lambda: workspace)
    register_navigation(server, lambda: workspace)
    for method in (
        types.TEXT_DOCUMENT_SIGNATURE_HELP,
        types.TEXT_DOCUMENT_FOLDING_RANGE,
        types.TEXT_DOCUMENT_SELECTION_RANGE,
        types.TEXT_DOCUMENT_CODE_ACTION,
        types.TEXT_DOCUMENT_REFERENCES,
        types.TEXT_DOCUMENT_RENAME,
        types.TEXT_DOCUMENT_PREPARE_RENAME,
        types.TEXT_DOCUMENT_HOVER,
    ):
        assert method in server.protocol.fm.features
    folds = feature(server, types.TEXT_DOCUMENT_FOLDING_RANGE)(
        types.FoldingRangeParams(
            types.TextDocumentIdentifier(workspace.current.uri)
        )
    )
    assert {(item.start_line, item.end_line) for item in folds} == {
        (0, 3),
        (1, 3),
    }


def test_quick_fix_options_are_versioned_and_bounded(tmp_path):
    from kivy_lsp.model.diagnostic import Diagnostic, DiagnosticSeverity

    workspace = FakeWorkspace(
        '<Panel>:\n    orientation: "sideways"\n', tmp_path
    )
    prop = workspace.parsed.document.items[0].body[0]
    workspace.problems = (
        Diagnostic(
            "Invalid option",
            prop.value.span,
            DiagnosticSeverity.ERROR,
            "kv-incompatible-property-value",
        ),
    )
    server = LanguageServer("test", "0")
    register_editor_features(server, lambda: workspace)
    action = feature(server, types.TEXT_DOCUMENT_CODE_ACTION)
    params = types.CodeActionParams(
        types.TextDocumentIdentifier(workspace.current.uri),
        types.Range(types.Position(1, 17), types.Position(1, 17)),
        types.CodeActionContext(diagnostics=[]),
    )
    results = action(params)
    assert len(results) == 2
    texts = set()
    for result in results:
        change = result.edit.document_changes[0]
        assert change.text_document.version == 5
        texts.add(change.edits[0].new_text)
        edit_range = change.edits[0].range
        assert edit_range.start.line == 1
        assert edit_range.start.character == 17
    assert texts == {"'horizontal'", "'vertical'"}


def test_lsp_hover_without_i18n_and_signature_unicode_range(tmp_path):
    workspace = FakeWorkspace(
        '<Panel>:\n    title: "😀" + root.describe("x", 2)\n',
        tmp_path,
    )
    server = LanguageServer("test", "0")
    register_editor_features(server, lambda: workspace)
    register_navigation(server, lambda: workspace)
    offset = workspace.current.text.index("describe")
    position = workspace.current.position_at(offset)
    hover = feature(server, types.TEXT_DOCUMENT_HOVER)(
        types.HoverParams(
            types.TextDocumentIdentifier(workspace.current.uri),
            types.Position(position.line, position.character),
        )
    )
    assert hover is not None
    assert hover.range.start.character == position.character
    assert "Render a readable description." in hover.contents.value


def test_translation_hover_preserved(tmp_path):
    from dataclasses import replace

    from kivy_lsp.config import I18nConfig
    from kivy_lsp.i18n.index import TranslationIndex

    path = tmp_path / "en.json"
    path.write_text('{"greeting": "Hello {name}"}')
    workspace = FakeWorkspace(
        '<Panel>:\n    i18n_key: "greeting"\n',
        tmp_path,
    )
    i18n = I18nConfig(path)
    workspace.config = replace(workspace.config, i18n=i18n)
    workspace.translation_index = TranslationIndex(i18n)
    server = LanguageServer("test", "0")
    register_navigation(server, lambda: workspace)
    hover = feature(server, types.TEXT_DOCUMENT_HOVER)(
        types.HoverParams(
            types.TextDocumentIdentifier(workspace.current.uri),
            types.Position(1, 16),
        )
    )
    assert hover is not None
    assert "Hello {name}" in hover.contents.value
    assert "Parameters: `name`" in hover.contents.value


def test_lsp_id_reference_and_versioned_rename(tmp_path):
    source = (
        "<Panel>:\n    Widget:\n        id: target\n"
        '    title: "😀" + target.title\n'
    )
    workspace = FakeWorkspace(source, tmp_path)
    server = LanguageServer("test", "0")
    register_refactoring(server, lambda: workspace)
    uri = workspace.current.uri
    position = types.Position(2, 13)
    refs = feature(server, types.TEXT_DOCUMENT_REFERENCES)(
        types.ReferenceParams(
            text_document=types.TextDocumentIdentifier(uri),
            position=position,
            context=types.ReferenceContext(include_declaration=False),
        )
    )
    assert len(refs) == 1
    assert refs[0].range.start == types.Position(3, 18)
    prepare = feature(server, types.TEXT_DOCUMENT_PREPARE_RENAME)(
        types.PrepareRenameParams(types.TextDocumentIdentifier(uri), position)
    )
    assert prepare.placeholder == "target"
    result = feature(server, types.TEXT_DOCUMENT_RENAME)(
        types.RenameParams(
            text_document=types.TextDocumentIdentifier(uri),
            position=position,
            new_name="renamed",
        )
    )
    change = result.document_changes[0]
    assert change.text_document.version == 5
    assert len(change.edits) == 2
    assert all(edit.new_text == "renamed" for edit in change.edits)


def test_option_quickfix_preserves_inline_comment(tmp_path):
    from kivy_lsp.model.diagnostic import Diagnostic, DiagnosticSeverity

    source = '<Panel>:\n    orientation: "sideways" # preserve\n'
    document, parsed, model, engine = analyzed(source, tmp_path)
    prop = parsed.document.items[0].body[0]
    problem = Diagnostic(
        "Invalid option",
        prop.value.span,
        DiagnosticSeverity.ERROR,
        "kv-incompatible-property-value",
    )
    fixes = engine.quick_fixes(document, parsed, model, (problem,))
    assert len(fixes) == 2
    fix = fixes[0]
    changed = (
        source[: fix.span.start] + fix.replacement + source[fix.span.end :]
    )
    assert "# preserve" in changed
