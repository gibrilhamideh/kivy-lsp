from __future__ import annotations

import pytest

from kivy_lsp.config import load_config
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.python.locator import PythonModuleLocator
from kivy_lsp.workspace import dependency_scanner
from kivy_lsp.workspace.dependency_scanner import DependencyScanner
from kivy_lsp.workspace.document import TextDocument
from kivy_lsp.workspace.project import ProjectWorkspace


@pytest.fixture
def dependency_workspace(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app").mkdir()
    (source / "app/__init__.py").write_text("")
    (source / "app/shared.py").write_text("class Formatter: pass\n")
    env = tmp_path / ".venv"
    env.mkdir()
    (env / "pyvenv.cfg").write_text("version = 3.12\n")
    site = env / "lib/python3.12/site-packages"
    kivy = site / "kivy"
    kivy.mkdir(parents=True)
    (kivy / "__init__.py").write_text("class Original: pass\n")
    for number in range(5):
        (kivy / f"module{number}.py").write_text("class Widget: pass\n")
    (tmp_path / "kivy-lsp.toml").write_text(
        'python-environment = ".venv"\n'
    )
    workspace = ProjectWorkspace(load_config(tmp_path))
    workspace.initialize()
    return workspace, site


@pytest.fixture
def dependency_io(monkeypatch):
    reads = []
    walks = []
    original_read = dependency_scanner._read_source
    original_walk = PythonModuleLocator.walk_package

    def read(path):
        reads.append(path)
        return original_read(path)

    def walk(locator, package):
        walks.append(package)
        return original_walk(locator, package)

    monkeypatch.setattr(dependency_scanner, "_read_source", read)
    monkeypatch.setattr(PythonModuleLocator, "walk_package", walk)
    return reads, walks


def kv_uri(workspace):
    return (workspace.config.source_roots[0] / "view.kv").as_uri()


def import_package(workspace, name):
    workspace.update_document(kv_uri(workspace), f"#:import Thing {name}\n")


def make_package(site, name, source="class Thing: pass\n"):
    path = site / name / "__init__.py"
    path.parent.mkdir(parents=True)
    path.write_text(source)
    return path


def test_typing_import_does_not_reread_or_walk_kivy(
    dependency_workspace, dependency_io
):
    workspace, _ = dependency_workspace
    reads, walks = dependency_io
    revision = workspace.python_index.revision
    for name in ("a", "ap", "app", "app.s", "app.sh", "app.shared"):
        import_package(workspace, name)
    assert reads == []
    assert walks == ["a", "ap", "app"]
    assert workspace.python_index.revision == revision
    assert workspace.python_index.class_named("kivy.Original") is not None


def test_add_remove_and_readd_dependency_only_reads_that_package(
    dependency_workspace, dependency_io
):
    workspace, site = dependency_workspace
    reads, walks = dependency_io
    path = make_package(site, "addon", "class Old: pass\n")
    import_package(workspace, "addon.Old")
    assert reads == [path]
    assert walks == ["addon"]
    assert workspace.python_index.class_named("addon.Old") is not None
    import_package(workspace, "app")
    assert workspace.python_index.class_named("addon.Old") is None
    assert path not in workspace.dependency_scan.indexed_files
    path.write_text("class New: pass\n")
    import_package(workspace, "addon.New")
    assert reads == [path, path]
    assert walks == ["addon", "app", "addon"]
    assert workspace.python_index.class_named("addon.New") is not None
    assert workspace.python_index.class_named("addon.Old") is None


def test_removed_package_drops_diagnostics_errors_and_stubs(
    dependency_workspace,
):
    workspace, site = dependency_workspace
    baseline_issues = workspace.status()["issues"]
    path = make_package(site, "addon")
    stub = path.with_suffix(".pyi")
    stub.write_text("class Stub: ...\n")
    (path.parent / "broken.py").write_text("class Broken(\n")
    (path.parent / "unreadable.py").write_bytes(b"\xff\n")
    import_package(workspace, "addon")
    assert workspace.python_index.class_named("addon.Stub") is not None
    assert workspace.python_index.class_named("addon.Thing") is None
    scan = workspace.dependency_scan
    assert scan.diagnostics
    assert scan.file_errors
    status = workspace.status()
    assert str(stub) in status["loadedStubs"]
    assert any("unreadable.py" in message for message in status["issues"])
    assert any("broken.py" in message for message in status["issues"])
    import_package(workspace, "missing_package")
    assert workspace.dependency_scan.package_issues
    assert not workspace.dependency_scan.diagnostics
    assert not workspace.dependency_scan.file_errors
    assert str(stub) not in workspace.status()["loadedStubs"]
    import_package(workspace, "app")
    assert not workspace.dependency_scan.package_issues
    assert workspace.status()["issues"] == baseline_issues


def test_project_overlay_is_not_deleted_with_dependency(
    dependency_workspace,
):
    workspace, site = dependency_workspace
    make_package(site, "addon")
    import_package(workspace, "addon")
    path = workspace.config.source_roots[0] / "addon.py"
    workspace.open_document(path.as_uri(), "class Local: pass\n", 1)
    import_package(workspace, "app")
    assert workspace.python_index.class_named("addon.Local") is not None
    assert workspace.document(path.as_uri()).version == 1
    assert "addon" not in workspace.dependency_scan.indexed_modules


@pytest.mark.parametrize("saved", [False, True])
def test_removing_project_shadow_restores_only_missing_dependency(
    dependency_workspace, dependency_io, saved
):
    workspace, site = dependency_workspace
    dependency = make_package(site, "addon")
    import_package(workspace, "addon")
    local = workspace.config.source_roots[0] / "addon.py"
    if saved:
        local.write_text("class Local: pass\n")
        workspace.refresh_files((local.as_uri(),))
    else:
        workspace.open_document(local.as_uri(), "class Local: pass\n")
    assert workspace.python_index.class_named("addon.Local") is not None
    assert dependency not in workspace.dependency_scan.indexed_files
    reads, walks = dependency_io
    reads.clear()
    walks.clear()
    if saved:
        local.unlink()
        workspace.refresh_files((local.as_uri(),))
    else:
        workspace.close_document(local.as_uri())
    assert reads == [dependency]
    assert walks == []
    assert workspace.python_index.class_named("addon.Thing") is not None
    assert workspace.python_index.class_named("addon.Local") is None
    assert dependency in workspace.dependency_scan.indexed_files


def test_external_open_dependency_is_retained_until_close(
    dependency_workspace,
):
    workspace, site = dependency_workspace
    path = make_package(site, "addon")
    import_package(workspace, "addon")
    workspace.open_document(path.as_uri(), path.read_text())
    import_package(workspace, "app")
    assert workspace.python_index.class_named("addon.Thing") is not None
    assert path not in workspace.dependency_scan.indexed_files
    workspace.close_document(path.as_uri())
    assert workspace.python_index.class_named("addon.Thing") is None


def test_reload_rereads_dependencies_and_preserves_project_overlay(
    dependency_workspace, dependency_io
):
    workspace, site = dependency_workspace
    path = site / "kivy/__init__.py"
    path.write_text("class Reloaded: pass\n")
    overlay = workspace.config.source_roots[0] / "overlay.py"
    workspace.open_document(overlay.as_uri(), "class Unsaved: pass\n", 9)
    reads, walks = dependency_io
    workspace.reload_project()
    assert set(reads) == set((site / "kivy").glob("*.py"))
    assert walks == ["kivy"]
    assert workspace.python_index.class_named("kivy.Reloaded") is not None
    assert workspace.python_index.class_named("kivy.Original") is None
    assert workspace.python_index.class_named("overlay.Unsaved") is not None
    assert workspace.document(overlay.as_uri()).version == 9


def test_scanner_default_retains_kivy_import_discovery(
    dependency_workspace, dependency_io
):
    workspace, site = dependency_workspace
    make_package(site, "kivy_addon")
    index = PythonIndex()
    module = index_python_module(
        TextDocument("file:///project.py", "import kivy_addon\n"), "project"
    ).module
    assert module is not None
    index.replace(module)
    scanner = DependencyScanner(PythonModuleLocator(workspace.environment))
    result = scanner.scan(index)
    assert result.packages == ("kivy", "kivy_addon")
    assert index.class_named("kivy_addon.Thing") is not None
    reads, walks = dependency_io
    reads.clear()
    walks.clear()
    result = scanner.scan(index, packages=("missing",), discover_imports=False)
    assert result.packages == ("missing",)
    assert walks == ["missing"]
    assert reads == []


@pytest.mark.parametrize("missing_file", [False, True])
def test_failed_dependency_restore_updates_status_without_repeated_reads(
    dependency_workspace, dependency_io, missing_file
):
    workspace, site = dependency_workspace
    path = make_package(site, "addon")
    import_package(workspace, "addon")
    overlay = workspace.config.source_roots[0] / "addon.py"
    workspace.open_document(overlay.as_uri(), "class Local: pass\n")
    if missing_file:
        path.unlink()
    else:
        path.write_text("class Invalid(\n")
    workspace.close_document(overlay.as_uri())
    assert workspace.python_index.module_named("addon") is None
    assert path not in workspace.dependency_scan.indexed_files
    if missing_file:
        assert workspace.dependency_scan.package_issues
    else:
        assert workspace.dependency_scan.diagnostics
    assert any("addon" in message for message in workspace.status()["issues"])
    reads, walks = dependency_io
    reads.clear()
    walks.clear()
    import_package(workspace, "addon.Other")
    assert reads == []
    assert walks == []


def test_pending_dependency_overlay_can_be_readded_then_removed(
    dependency_workspace,
):
    workspace, site = dependency_workspace
    path = make_package(site, "addon")
    import_package(workspace, "addon")
    workspace.open_document(path.as_uri(), path.read_text())
    import_package(workspace, "app")
    import_package(workspace, "addon")
    assert path in workspace.dependency_scan.indexed_files
    import_package(workspace, "app")
    workspace.close_document(path.as_uri())
    assert workspace.python_index.module_named("addon") is None
