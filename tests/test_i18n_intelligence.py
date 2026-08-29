import json
from pathlib import Path

from kivy_lsp.analysis.i18n import (
    TranslationCompletionEngine,
    TranslationDiagnosticAnalyzer,
    translation_key_target_at,
    translation_parameter_target_at,
)
from kivy_lsp.config import I18nConfig, load_config
from kivy_lsp.i18n.index import TranslationIndex
from kivy_lsp.kv.parser import parse
from kivy_lsp.workspace.document import TextDocument


def _catalog(tmp_path: Path) -> tuple[I18nConfig, TranslationIndex]:
    path = tmp_path / "en.json"
    path.write_text(
        json.dumps(
            {
                "features": {
                    "ventilation": {
                        "title": "Cycle ventilation",
                        "navigator": (
                            "Stage {number} of {count}"
                        ),
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    config = I18nConfig(
        source=path,
        properties=(
            "i18n_key",
            "hint_i18n_key",
        ),
    )
    index = TranslationIndex(config)
    index.refresh(force=True)
    return config, index


def test_config_loads_one_translation_source(tmp_path: Path) -> None:
    source = tmp_path / "translations" / "en.json"
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        (
            "[tool.kivy-lsp.i18n]\n"
            'source = "translations/en.json"\n'
            'properties = ["i18n_key", "label_key"]\n'
        ),
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.i18n is not None
    assert config.i18n.source == source.resolve()
    assert config.i18n.properties == (
        "i18n_key",
        "label_key",
    )


def test_translation_index_flattens_and_reloads(tmp_path: Path) -> None:
    config, index = _catalog(tmp_path)
    entry = index.entry(
        "features.ventilation.navigator"
    )

    assert entry is not None
    assert entry.value == "Stage {number} of {count}"
    assert entry.placeholder_names == (
        "number",
        "count",
    )

    config.source.write_text(
        '{"features": {"new_title": "New"}}',
        encoding="utf-8",
    )

    assert index.entry("features.ventilation.navigator") is None
    assert index.entry("features.new_title") is not None


def test_translation_index_reports_malformed_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "en.json"
    path.write_text('{"broken":', encoding="utf-8")
    index = TranslationIndex(I18nConfig(source=path))

    assert index.problems
    assert "Invalid translation JSON" in index.problems[0].message


def test_translation_key_completion_handles_dotted_prefix(
    tmp_path: Path,
) -> None:
    config, index = _catalog(tmp_path)
    source = (
        "<UIText>:\n"
        '    i18n_key: "features.vent'
    )
    document = TextDocument(
        uri="file:///view.kv",
        text=source,
    )
    result = TranslationCompletionEngine(
        index,
        config,
    ).complete(
        document,
        parse(source),
        len(source),
    )

    assert result is not None
    assert [item.label for item in result.items] == [
        "features.ventilation.navigator",
        "features.ventilation.title",
    ]
    assert result.items[0].insert_text == (
        '"features.ventilation.navigator"'
    )


def test_translation_parameter_completion(
    tmp_path: Path,
) -> None:
    config, index = _catalog(tmp_path)
    source = (
        "<UIText>:\n"
        "    i18n_key: "
        '"features.ventilation.navigator"\n'
        "    i18n_params: {\"nu"
    )
    document = TextDocument(
        uri="file:///view.kv",
        text=source,
    )
    result = TranslationCompletionEngine(
        index,
        config,
    ).complete(
        document,
        parse(source),
        len(source),
    )

    assert result is not None
    assert [item.label for item in result.items] == ["number"]
    assert result.items[0].insert_text == '"number"'


def test_translation_diagnostics_validate_keys_and_parameters(
    tmp_path: Path,
) -> None:
    config, index = _catalog(tmp_path)
    source = (
        "<UIText>:\n"
        "    i18n_key: "
        '"features.ventilation.navigator"\n'
        "    i18n_params: "
        '{"number": 1, "extra": 2, "extra": 3}\n'
        "<UIText>:\n"
        '    i18n_key: "missing.key"\n'
    )
    document = TextDocument(
        uri="file:///view.kv",
        text=source,
    )
    diagnostics = TranslationDiagnosticAnalyzer(
        index,
        config,
    ).analyze(
        document,
        parse(source),
    )
    codes = [diagnostic.code for diagnostic in diagnostics]

    assert "i18n-missing-params" in codes
    assert "i18n-unknown-param" in codes
    assert "i18n-duplicate-param" in codes
    assert "i18n-unknown-key" in codes


def test_dynamic_parameter_mapping_is_not_rejected(
    tmp_path: Path,
) -> None:
    config, index = _catalog(tmp_path)
    source = (
        "<UIText>:\n"
        "    i18n_key: "
        '"features.ventilation.navigator"\n'
        "    i18n_params: root.params\n"
    )
    document = TextDocument(
        uri="file:///view.kv",
        text=source,
    )
    diagnostics = TranslationDiagnosticAnalyzer(
        index,
        config,
    ).analyze(
        document,
        parse(source),
    )

    assert not any(
        diagnostic.code == "i18n-invalid-params"
        for diagnostic in diagnostics
    )


def test_translation_targets_find_json_key_and_placeholder(
    tmp_path: Path,
) -> None:
    config, index = _catalog(tmp_path)
    source = (
        "<UIText>:\n"
        "    i18n_key: "
        '"features.ventilation.navigator"\n'
        "    i18n_params: "
        '{"number": 1, "count": 2}\n'
    )
    document = TextDocument(
        uri="file:///view.kv",
        text=source,
    )
    parse_result = parse(source)
    key_offset = source.index("ventilation")
    parameter_offset = source.index('"count"') + 1
    key_target = translation_key_target_at(
        document,
        parse_result,
        key_offset,
        config,
        index,
    )
    parameter_target = translation_parameter_target_at(
        document,
        parse_result,
        parameter_offset,
        config,
        index,
    )

    assert key_target is not None
    assert key_target.entry is not None
    assert key_target.entry.key == (
        "features.ventilation.navigator"
    )
    assert parameter_target is not None
    assert parameter_target.placeholder is not None
    assert parameter_target.placeholder.name == "count"

