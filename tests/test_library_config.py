from dataclasses import replace

import pytest

from kivy_lsp.config import CONFIG_FILENAME, load_config
from kivy_lsp.python.environment import (
    PythonEnvironment, discover_python_environment,
)
from kivy_lsp.python.locator import PythonModuleLocator
from kivy_lsp.workspace import library_config, project
from kivy_lsp.workspace.project import ProjectWorkspace


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    site = tmp_path / "site-packages"
    (site / "kivy").mkdir(parents=True)
    (site / "kivy/__init__.py").write_text("")
    environment = PythonEnvironment(
        tmp_path, None, "3.12", (source, site), (site,), (),
    )
    monkeypatch.setattr(
        project, "discover_python_environment", lambda _: environment,
    )
    return tmp_path, source, site


def package(site, name, exports="", source="class Value: pass\n"):
    directory = site.joinpath(*name.split("."))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "__init__.py").write_text(source)
    metadata = directory / CONFIG_FILENAME
    if exports:
        metadata.write_text(exports)
    return metadata


def workspace(setup, imports="", config=""):
    root, source, _ = setup
    (source / "widgets.py").write_text(
        imports + "\nclass Panel:\n    text: str\n",
    )
    (root / CONFIG_FILENAME).write_text(config)
    result = ProjectWorkspace(load_config(root))
    result.initialize()
    return result


def test_multiple_libraries_merge_and_never_execute_package(setup):
    _, source, site = setup
    first = package(site, "first", '''[globals]
First = "first.Value"
Shared = "first.Value"
[member-projections]
"first.Store" = 0
''', 'raise AssertionError("Do not import me")\nclass Value: pass\n')
    second = package(site, "second", '''[global-imports]
Second = "second.Value"
Shared = "first.Value"
[subscript-projections]
"second.Rows" = 1
''')
    result = workspace(setup, "import first\nfrom second import Value\n")
    assert result.config.globals == {
        "First": "first.Value", "Shared": "first.Value",
        "Second": "second.Value",
    }
    assert result.config.member_projections == {"first.Store": 0}
    assert result.config.subscript_projections == {"second.Rows": 1}
    assert not result.library_config_issues
    assert {first, second}.issubset(result.library_config_paths)
    uri = (source / "panel.kv").as_uri()
    result.open_document(uri, "<Panel>:\n    text: str(First())\n")
    assert not result.diagnostics_for(uri)
    assert result.python_index.class_named("first.Value") is not None


def test_library_project_settings_do_not_leak(setup):
    root, _, site = setup
    (root / "en.json").write_text('{"hello": "Hello"}')
    package(site, "first", '''source-roots = 42
python-environment = false
[format]
line-length = "invalid"
[diagnostics]
strict = "invalid"
[i18n]
source = 42
[globals]
First = "first.Value"
''')
    result = workspace(setup, "import first\n", '''[format]
line-length = 71
[i18n]
source = "en.json"
''')
    assert result.config.format.line_length == 71
    assert result.config.i18n.source == root / "en.json"
    assert not result.config.diagnostics.strict
    assert result.config.source_roots == (root / "src",)
    assert not result.library_config_issues


def test_project_overrides_either_library_global_table(setup):
    _, _, site = setup
    package(site, "first", '''[globals]
Shared = "first.Value"
[global-imports]
Other = "first.Value"
[member-projections]
"first.Store" = 0
''')
    package(site, "second", '''[global-imports]
Shared = "second.Value"
Other = "second.Value"
[member-projections]
"first.Store" = 1
''')
    result = workspace(setup, "import first, second\n", '''[globals]
Other = "second.Value"
Both = "first.Value"
[global-imports]
Shared = "second.Value"
Both = "second.Value"
[member-projections]
"first.Store" = 2
''')
    assert result.config.globals == {
        "Other": "second.Value", "Both": "first.Value",
    }
    imports = {
        item.name: item.target for item in result.config.global_imports
    }
    assert imports == {
        "Shared": "second.Value", "Both": "second.Value",
    }
    assert result.config.member_projections == {"first.Store": 2}
    assert not result.library_config_issues


def test_conflicting_exports_are_visible_and_not_selected(setup):
    _, source, site = setup
    first = package(site, "first", '''[globals]
Shared = "first.Value"
[subscript-projections]
"first.Rows" = 0
''')
    second = package(site, "second", '''[globals]
Shared = "second.Value"
[subscript-projections]
"first.Rows" = 1
''')
    result = workspace(setup, "import first, second\n")
    assert "Shared" not in result.config.globals
    assert "first.Rows" not in result.config.subscript_projections
    assert {issue.path for issue in result.library_config_issues} == {
        first, second,
    }
    assert result.library_config_issues == result.scan_errors
    assert all(
        item.code == "library-config"
        for item in result.diagnostics_for(first.as_uri())
    )
    uri = (source / "panel.kv").as_uri()
    result.open_document(uri, '<Panel>:\n    text: "fine"\n')
    assert not result.diagnostics_for(uri)
    assert any(
        "Conflicting" in message for message in result.status()["issues"]
    )


def test_kv_directive_overrides_project_and_library(setup):
    _, source, site = setup
    package(site, "first", '[globals]\nShared = "first.Value"\n')
    package(site, "second")
    result = workspace(
        setup, "import first\n", '[globals]\nShared = "first.Value"\n',
    )
    uri = (source / "panel.kv").as_uri()
    result.open_document(
        uri, '#:import Shared second.Value\n<Panel>:\n    text: str(Shared)\n',
    )
    model = result.semantic_model(uri)
    binding = model.scopes[0].binding_named("Shared")
    assert binding.value.class_symbol.qualified_name == "second.Value"


def test_nested_namespace_package_metadata_is_discovered(setup):
    _, _, site = setup
    first = package(site, "ecosystem.store", '''[globals]
Store = "ecosystem.store.Value"
''')
    second = package(site, "ecosystem.three", '''[globals]
Scene = "ecosystem.three.Value"
''')
    result = workspace(
        setup, "from ecosystem.store import Value\nimport ecosystem.three\n",
    )
    assert result.config.globals == {
        "Store": "ecosystem.store.Value", "Scene": "ecosystem.three.Value",
    }
    assert {first, second}.issubset(result.library_config_paths)


def test_export_targets_discover_required_library_metadata(setup):
    _, _, site = setup
    package(site, "first", '[globals]\nSecond = "second.Value"\n')
    package(site, "second", '[globals]\nThird = "third.Value"\n')
    package(site, "third", '[globals]\nFirst = "first.Value"\n')
    result = workspace(setup, "import first\n")
    assert result.config.globals == {
        "First": "first.Value", "Second": "second.Value",
        "Third": "third.Value",
    }
    assert result.python_index.class_named("third.Value") is not None


def test_unused_and_ordinary_libraries_are_not_scanned(setup, monkeypatch):
    _, _, site = setup
    package(site, "unused", '[globals]\nHidden = "unused.Value"\n')
    package(site, "ordinary")
    walks = []
    original = PythonModuleLocator.walk_package

    def walk(locator, name):
        walks.append(name)
        return original(locator, name)

    monkeypatch.setattr(PythonModuleLocator, "walk_package", walk)
    result = workspace(setup, "import ordinary\n")
    assert not result.config.globals
    assert walks == ["kivy"]
    assert result.python_index.module_named("ordinary") is None


def test_metadata_changes_create_delete_and_clear_errors(setup):
    _, source, site = setup
    path = package(site, "first")
    result = workspace(setup, "import first\n")
    assert path in result.library_config_paths
    uri = (source / "panel.kv").as_uri()
    result.open_document(uri, '<Panel>:\n    text: str(First)\n')
    path.write_text('[globals]\nFirst = "first.Value"\n')
    update = result.refresh_files((path.as_uri(),))
    assert uri in update.affected_uris
    assert not result.diagnostics_for(uri)
    path.write_text('[globals]\nFirst = 4\n')
    update = result.refresh_files((path.as_uri(),))
    assert path.as_uri() in update.affected_uris
    assert result.diagnostics_for(path.as_uri())
    path.unlink()
    update = result.refresh_files((path.as_uri(),))
    assert path.as_uri() in update.affected_uris
    assert not result.diagnostics_for(path.as_uri())
    assert not result.library_config_issues
    assert "First" not in result.config.globals


def test_removed_provider_and_override_do_not_leave_stale_defaults(setup):
    root, source, site = setup
    package(site, "first", '[globals]\nFirst = "first.Value"\n')
    package(site, "second")
    result = workspace(setup, "import first\n")
    config = root / CONFIG_FILENAME
    config.write_text('[global-imports]\nFirst = "second.Value"\n')
    result.refresh_files((config.as_uri(),))
    assert "First" not in result.config.globals
    config.write_text("")
    result.refresh_files((config.as_uri(),))
    assert result.config.globals["First"] == "first.Value"
    path = source / "widgets.py"
    result.update_document(path.as_uri(), "class Panel: pass\n")
    assert "First" not in result.config.globals
    assert result.python_index.module_named("first") is None


def test_warm_edits_do_not_reload_or_rediscover_metadata(setup, monkeypatch):
    _, source, site = setup
    package(site, "first", '[globals]\nFirst = "first.Value"\n')
    result = workspace(setup, "import first\n")

    def unexpected(*args, **kwargs):
        raise AssertionError("Warm edits must reuse metadata")

    monkeypatch.setattr(library_config, "load_library_exports", unexpected)
    monkeypatch.setattr(
        library_config.LibraryConfigResolver, "_candidate_paths", unexpected,
    )
    monkeypatch.setattr(PythonModuleLocator, "walk_package", unexpected)
    uri = (source / "panel.kv").as_uri()
    for text in ("a", "ab", "abc"):
        result.update_document(uri, f'<Panel>:\n    text: "{text}"\n')


def test_library_merge_preserves_unsaved_translation_overlay(setup):
    root, source, site = setup
    catalog = root / "en.json"
    catalog.write_text('{"saved": "Saved"}')
    package(site, "first", '[globals]\nFirst = "first.Value"\n')
    result = workspace(setup, "", '[i18n]\nsource = "en.json"\n')
    result.open_document(catalog.as_uri(), '{"unsaved": "Unsaved"}')
    index = result.translation_index
    uri = (source / "panel.kv").as_uri()
    result.open_document(uri, '#:import First first.Value\n<Panel>:\n')
    assert result.translation_index is index
    assert '"unsaved"' in index.source
    assert result.config.globals["First"] == "first.Value"


def test_editable_path_metadata_uses_selected_environment(setup):
    root, source, site = setup
    venv = root / "selected"
    selected_site = venv / "lib/python3.12/site-packages"
    selected_site.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("version = 3.12\n")
    linked = root / "linked-src"
    metadata = package(linked, "linked", '[globals]\nLink = "linked.Value"\n')
    package(site, "linked", '[globals]\nWrong = "linked.Value"\n')
    (selected_site / "linked.pth").write_text(str(linked) + "\n")
    config = replace(load_config(root), python_environment=venv)
    environment = discover_python_environment(config)
    resolved = library_config.LibraryConfigResolver(environment).resolve(
        config, {"linked"},
    )
    assert resolved.config.globals == {"Link": "linked.Value"}
    assert metadata in resolved.paths


@pytest.mark.parametrize("table", ["globals", "global-imports"])
def test_project_global_target_alone_discovers_and_indexes_library(
    setup, table,
):
    _, _, site = setup
    package(site, "first", '[globals]\nExtra = "first.Value"\n')
    result = workspace(setup, "", f'[{table}]\nFirst = "first.Value"\n')
    assert result.config.globals["Extra"] == "first.Value"
    assert result.python_index.class_named("first.Value") is not None


def test_namespace_portions_from_separate_source_locations_are_additive(setup):
    root, source, site = setup
    other = root / "other-source"
    first = package(site, "ecosystem.store", '''[globals]
Store = "ecosystem.store.Value"
''')
    second = package(other, "ecosystem.three", '''[globals]
Scene = "ecosystem.three.Value"
''')
    environment = PythonEnvironment(
        root, None, "3.12", (source, site, other), (site,), (),
    )
    result = library_config.LibraryConfigResolver(environment).resolve(
        load_config(root), {"ecosystem.store", "ecosystem.three"},
    )
    assert result.config.globals == {
        "Store": "ecosystem.store.Value", "Scene": "ecosystem.three.Value",
    }
    assert {first, second}.issubset(result.paths)


def test_regular_package_shadows_other_namespace_metadata(setup):
    root, source, site = setup
    namespace = source / "first"
    namespace.mkdir()
    hidden = namespace / CONFIG_FILENAME
    hidden.write_text('[globals]\nHidden = "first.Value"\n')
    visible = package(site, "first", '[globals]\nVisible = "first.Value"\n')
    result = workspace(setup, "import first\n")
    assert result.config.globals == {"Visible": "first.Value"}
    assert hidden not in result.library_config_paths
    assert visible in result.library_config_paths
