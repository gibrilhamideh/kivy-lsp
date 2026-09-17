"""Reference identity, source positions, and conservative rename failures."""

from __future__ import annotations

from pathlib import Path

import pytest

from kivy_lsp.analysis.references import (
    KvReferenceEngine,
    ReferenceDocument,
    RenameError,
)
from kivy_lsp.analysis.scope_builder import build_kv_semantic_model
from kivy_lsp.config import ServerConfig
from kivy_lsp.kv.index import KvIndex
from kivy_lsp.kv.parser import parse
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.workspace.document import TextDocument
from kivy_lsp.workspace.kv_scanner import KvScanner


def make_engine(root: Path, sources: dict[str, str]):
    config = ServerConfig(root, (root,), (root,))
    documents = {
        name: TextDocument((root / name).as_uri(), source)
        for name, source in sources.items()
    }
    python_index = PythonIndex()
    python_documents = []
    kv_index = KvIndex()
    scanner = KvScanner((root,))
    parsed_documents = []

    for name, document in documents.items():
        if name.endswith(".py"):
            result = index_python_module(document, Path(name).stem)
            assert result.module is not None
            python_index.replace(result.module)
            python_documents.append(document)
        else:
            parsed = parse(document.text)
            kv_index.replace(document.uri, scanner.scan_result(
                document.uri, parsed
            ))
            parsed_documents.append((document, parsed))

    references = tuple(
        ReferenceDocument(
            document,
            parsed,
            build_kv_semantic_model(
                document, parsed, python_index, config, kv_index
            ),
        )
        for document, parsed in parsed_documents
    )
    engine = KvReferenceEngine(
        references, python_index, kv_index, tuple(python_documents)
    )
    return engine, documents


def id_target(engine, document, name="target"):
    offset = document.text.index(f"id: {name}") + len("id: ")
    target = engine.target_at(document.uri, offset)
    assert target is not None
    return target


def test_identical_ids_in_different_rules_have_distinct_identity(tmp_path):
    source = (
        "<First>:\n"
        "    text: target.text\n"
        "    Label:\n"
        "        id: target\n"
        "<Second>:\n"
        "    text: target.text\n"
        "    Label:\n"
        "        id: target\n"
    )
    engine, documents = make_engine(tmp_path, {"view.kv": source})
    document = documents["view.kv"]
    edits = engine.rename(id_target(engine, document), "heading")

    assert len(edits) == 2
    assert all(edit.span.start < source.index("<Second>") for edit in edits)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_unicode_and_flat_multiline_references_select_only_names(
    tmp_path,
    newline,
):
    source = newline.join([
        "<View>:",
        "    text:",
        "        (",
        '        "é" + target.text',
        "# ignored comment",
        '        + root.ids["target"].text',
        "        + root.ids.target.text",
        "        )",
        "    Label:",
        "        id: target",
        "",
    ])
    engine, documents = make_engine(tmp_path, {"view.kv": source})
    edits = engine.rename(id_target(engine, documents["view.kv"]), "label")

    assert len(edits) == 4
    assert all(source[edit.span.start:edit.span.end] == "target"
               for edit in edits)


def test_comprehension_target_does_not_rename_its_local_uses(tmp_path):
    source = (
        "<View>:\n"
        "    values: [target.text for target in root.items] + [target.text]\n"
        "    Label:\n"
        "        id: target\n"
    )
    engine, documents = make_engine(tmp_path, {"view.kv": source})
    edits = engine.rename(id_target(engine, documents["view.kv"]), "label")

    assert len(edits) == 2
    assert {edit.span.start for edit in edits} == {
        source.rindex("[target.text]") + 1,
        source.index("id: target") + 4,
    }


@pytest.mark.parametrize(
    "expression",
    [
        "root.ids[root.selected_id].text",
        'root.ids["tar\\u0067et"].text',
        "app.use(root.ids)",
    ],
)
def test_unsupported_kv_access_blocks_partial_id_rename(tmp_path, expression):
    source = (
        f"<View>:\n    text: {expression}\n"
        "    Label:\n        id: target\n"
    )
    engine, documents = make_engine(tmp_path, {"view.kv": source})

    with pytest.raises(RenameError):
        engine.rename(id_target(engine, documents["view.kv"]), "label")


def test_self_ids_access_is_included_or_blocks_rename(tmp_path):
    source = (
        "<View>:\n"
        "    text: self.ids.target.text\n"
        "    Label:\n"
        "        id: target\n"
    )
    engine, documents = make_engine(tmp_path, {"view.kv": source})

    try:
        edits = engine.rename(id_target(engine, documents["view.kv"]), "label")
    except RenameError:
        return

    assert len(edits) == 2, "A safe rename must cover the self.ids use."


def test_escaped_python_id_key_blocks_partial_rename(tmp_path):
    sources = {
        "view.kv": "<View>:\n    Label:\n        id: target\n",
        "view.py": (
            "class View:\n"
            "    def read(self):\n"
            '        return self.ids["tar\\u0067et"]\n'
        ),
    }
    engine, documents = make_engine(tmp_path, sources)

    with pytest.raises(RenameError):
        engine.rename(id_target(engine, documents["view.kv"]), "label")


def test_python_inherited_id_access_is_included_or_blocks_rename(tmp_path):
    sources = {
        "view.kv": "<Base>:\n    Label:\n        id: target\n",
        "view.py": (
            "class Base:\n    pass\n"
            "class Child(Base):\n"
            "    def read(self):\n"
            "        return self.ids.target\n"
        ),
    }
    engine, documents = make_engine(tmp_path, sources)

    try:
        edits = engine.rename(id_target(engine, documents["view.kv"]), "label")
    except RenameError:
        return

    assert len(edits) == 2, "Inherited ID access must not be silently omitted."


@pytest.mark.parametrize(
    "expression", ['"SpecialLabel"', "Factory.SpecialLabel()"]
)
def test_dynamic_widget_expression_use_blocks_partial_rename(
    tmp_path,
    expression,
):
    source = (
        "<SpecialLabel@Label>:\n    text: 'hello'\n"
        f"RecycleView:\n    viewclass: {expression}\n"
    )
    engine, documents = make_engine(tmp_path, {"view.kv": source})
    target = engine.target_at(documents["view.kv"].uri, 1)
    assert target is not None

    with pytest.raises(RenameError):
        engine.rename(target, "HeadingLabel")


def test_class_style_selector_is_not_a_dynamic_class_reference(tmp_path):
    source = (
        "<SpecialLabel@Label>:\n    text: 'hello'\n"
        "<.SpecialLabel>:\n    opacity: 0.5\n"
        "SpecialLabel:\n"
    )
    engine, documents = make_engine(tmp_path, {"view.kv": source})
    target = engine.target_at(documents["view.kv"].uri, 1)
    assert target is not None
    edits = engine.rename(target, "HeadingLabel")

    assert len(edits) == 2
    class_style_start = source.index("<.SpecialLabel>")
    assert not any(class_style_start <= edit.span.start
                   < class_style_start + len("<.SpecialLabel>")
                   for edit in edits)


def test_new_id_does_not_capture_a_lambda_parameter(tmp_path):
    source = (
        "<View>:\n"
        "    callback: lambda label: target.text\n"
        "    Label:\n"
        "        id: target\n"
    )
    engine, documents = make_engine(tmp_path, {"view.kv": source})

    with pytest.raises(RenameError):
        engine.rename(id_target(engine, documents["view.kv"]), "label")
