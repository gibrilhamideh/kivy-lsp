from pathlib import Path

from kivy_lsp.analysis.definition import (
    KvDefinitionEngine,
    PythonIdsDefinitionEngine,
)
from kivy_lsp.analysis.scope import KvSemanticModel
from kivy_lsp.analysis.scope_builder import build_kv_semantic_model
from kivy_lsp.config import I18nConfig, ServerConfig
from kivy_lsp.i18n.index import TranslationIndex
from kivy_lsp.kv.index import KvIndex
from kivy_lsp.kv.parser import ParseResult, parse
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.workspace.document import TextDocument
from kivy_lsp.workspace.kv_scanner import KvScanner


def _python_index() -> PythonIndex:
    source = (
        "class Widget:\n"
        "    ids = DictProperty({})\n"
        "    i18n_key = StringProperty('')\n"
        "    i18n_params = DictProperty({})\n"
        "\n"
        "class FnBoxLayout(Widget):\n"
        "    title = StringProperty('')\n"
        "    def clear_widgets(self) -> None:\n"
        "        pass\n"
        "\n"
        "class ChoiceA:\n"
        "    status = StringProperty('')\n"
        "\n"
        "class ChoiceB:\n"
        "    status = StringProperty('')\n"
        "\n"
        "class RootView(Widget):\n"
        "    title = StringProperty('')\n"
        "    choice: ChoiceA | ChoiceB = ObjectProperty(None)\n"
        "    def save(self) -> None:\n"
        "        pass\n"
    )
    index = PythonIndex()
    result = index_python_module(
        TextDocument(
            uri="file:///widgets.py",
            text=source,
        ),
        "widgets",
    )
    assert result.module is not None
    index.replace(result.module)
    return index


def _config(
    tmp_path: Path,
    translation_path: Path,
) -> ServerConfig:
    return ServerConfig(
        project_root=tmp_path,
        source_roots=(tmp_path,),
        kv_paths=(tmp_path,),
        i18n=I18nConfig(source=translation_path),
    )


def _kv_environment(
    tmp_path: Path,
):
    translation_path = tmp_path / "en.json"
    translation_path.write_text(
        (
            "{\n"
            '  "screen": {\n'
            '    "title": "Stage {number}"\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    source = (
        "<CustomToolbar@FnBoxLayout>:\n"
        "<RootView>:\n"
        "    FnBoxLayout:\n"
        "        id: toolbar\n"
        "        title: root.ids.toolbar.clear_widgets()\n"
        "        title: root.choice.status\n"
        '        i18n_key: "screen.title"\n'
        '        i18n_params: {"number": root.title}\n'
        "    CustomToolbar:\n"
    )
    uri = "file:///view.kv"
    document = TextDocument(uri=uri, text=source)
    parse_result = parse(source)
    python_index = _python_index()
    config = _config(tmp_path, translation_path)
    semantic_model = build_kv_semantic_model(
        document,
        parse_result,
        python_index,
        config,
    )
    kv_index = KvIndex()
    kv_index.replace(
        uri,
        KvScanner((tmp_path,)).scan_text(uri, source),
    )
    translation_index = TranslationIndex(config.i18n)
    translation_index.refresh(force=True)
    engine = KvDefinitionEngine(
        python_index,
        kv_index,
        config,
        translation_index,
    )
    return (
        source,
        document,
        parse_result,
        semantic_model,
        python_index,
        kv_index,
        engine,
    )


def _definitions_at(
    source: str,
    needle: str,
    occurrence: int,
    document: TextDocument,
    parse_result: ParseResult,
    semantic_model: KvSemanticModel,
    engine: KvDefinitionEngine,
):
    start = -1

    for _ in range(occurrence + 1):
        start = source.index(needle, start + 1)

    return engine.definition_at(
        document,
        parse_result,
        semantic_model,
        start,
    )


def test_kv_definition_is_sensitive_to_each_chain_segment(
    tmp_path: Path,
) -> None:
    (
        source,
        document,
        parse_result,
        semantic_model,
        _,
        _,
        engine,
    ) = _kv_environment(tmp_path)
    root = _definitions_at(
        source,
        "root",
        0,
        document,
        parse_result,
        semantic_model,
        engine,
    )
    toolbar = _definitions_at(
        source,
        "toolbar",
        1,
        document,
        parse_result,
        semantic_model,
        engine,
    )
    method = _definitions_at(
        source,
        "clear_widgets",
        0,
        document,
        parse_result,
        semantic_model,
        engine,
    )
    ids = _definitions_at(
        source,
        "ids",
        0,
        document,
        parse_result,
        semantic_model,
        engine,
    )
    property_definition = _definitions_at(
        source,
        "i18n_key",
        0,
        document,
        parse_result,
        semantic_model,
        engine,
    )

    assert root[0].uri == "file:///widgets.py"
    assert toolbar[0].uri == "file:///view.kv"
    assert source[toolbar[0].selection_span.start:
                  toolbar[0].selection_span.end] == "toolbar"
    assert method[0].uri == "file:///widgets.py"
    assert ids[0].uri == "file:///widgets.py"
    assert property_definition[0].uri == "file:///widgets.py"


def test_union_member_returns_every_valid_definition(
    tmp_path: Path,
) -> None:
    (
        source,
        document,
        parse_result,
        semantic_model,
        _,
        _,
        engine,
    ) = _kv_environment(tmp_path)
    locations = _definitions_at(
        source,
        "status",
        0,
        document,
        parse_result,
        semantic_model,
        engine,
    )

    assert len(locations) == 2
    assert {location.uri for location in locations} == {
        "file:///widgets.py"
    }


def test_widget_definition_prefers_python_or_dynamic_kv(
    tmp_path: Path,
) -> None:
    (
        source,
        document,
        parse_result,
        semantic_model,
        _,
        _,
        engine,
    ) = _kv_environment(tmp_path)
    python_widget = _definitions_at(
        source,
        "FnBoxLayout",
        1,
        document,
        parse_result,
        semantic_model,
        engine,
    )
    dynamic_widget = _definitions_at(
        source,
        "CustomToolbar",
        1,
        document,
        parse_result,
        semantic_model,
        engine,
    )

    assert python_widget[0].uri == "file:///widgets.py"
    assert dynamic_widget[0].uri == "file:///view.kv"
    selection = dynamic_widget[0].selection_span
    assert source[selection.start:selection.end] == "CustomToolbar"


def test_translation_definition_targets_key_and_placeholder(
    tmp_path: Path,
) -> None:
    (
        source,
        document,
        parse_result,
        semantic_model,
        _,
        _,
        engine,
    ) = _kv_environment(tmp_path)
    key = _definitions_at(
        source,
        "screen.title",
        0,
        document,
        parse_result,
        semantic_model,
        engine,
    )
    parameter = _definitions_at(
        source,
        "number",
        0,
        document,
        parse_result,
        semantic_model,
        engine,
    )

    assert key[0].uri == (tmp_path / "en.json").as_uri()
    assert parameter[0].uri == key[0].uri
    translation_source = (tmp_path / "en.json").read_text(
        encoding="utf-8",
    )
    key_span = key[0].selection_span
    parameter_span = parameter[0].selection_span
    assert translation_source[key_span.start:key_span.end] == "title"
    assert translation_source[
        parameter_span.start:parameter_span.end
    ] == "{number}"


def test_python_ids_definition_targets_kv_and_member(
    tmp_path: Path,
) -> None:
    (
        _,
        _,
        _,
        _,
        python_index,
        kv_index,
        _,
    ) = _kv_environment(tmp_path)
    source = (
        "class RootView:\n"
        "    def clear(self):\n"
        "        self.ids.toolbar.clear_widgets()\n"
    )
    document = TextDocument(
        uri="file:///root.py",
        text=source,
    )
    engine = PythonIdsDefinitionEngine(
        python_index,
        kv_index,
    )
    id_offset = source.index("toolbar")
    member_offset = source.index("clear_widgets")
    id_locations = engine.definition_at(document, id_offset)
    member_locations = engine.definition_at(
        document,
        member_offset,
    )

    assert id_locations[0].uri == "file:///view.kv"
    assert member_locations[0].uri == "file:///widgets.py"


def test_python_subscript_id_definition_targets_kv(
    tmp_path: Path,
) -> None:
    (
        _,
        _,
        _,
        _,
        python_index,
        kv_index,
        _,
    ) = _kv_environment(tmp_path)
    source = (
        "class RootView:\n"
        "    def clear(self):\n"
        '        self.ids["toolbar"].clear_widgets()\n'
    )
    document = TextDocument(
        uri="file:///root.py",
        text=source,
    )
    engine = PythonIdsDefinitionEngine(
        python_index,
        kv_index,
    )
    locations = engine.definition_at(
        document,
        source.index("toolbar"),
    )

    assert locations[0].uri == "file:///view.kv"

