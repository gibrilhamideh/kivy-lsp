from __future__ import annotations

from pathlib import Path

import pytest

from kivy_lsp.config import ServerConfig
from kivy_lsp.model.symbol import ModuleSymbol, SymbolKind
from kivy_lsp.python.environment import PythonEnvironment
from kivy_lsp.python.import_completion import PythonImportCompleter
from kivy_lsp.python.index import PythonIndex
from kivy_lsp.python.indexer import index_python_module
from kivy_lsp.python.locator import PythonModuleLocator
from kivy_lsp.python.module import PythonModule
from kivy_lsp.workspace.document import TextDocument


def write(root: Path, relative: str, text: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def locator(root: Path, *extra: Path) -> PythonModuleLocator:
    return PythonModuleLocator(
        PythonEnvironment(root, None, "3.12", (root, *extra), extra, ())
    )


def add(index: PythonIndex, path: Path, name: str, text: str) -> None:
    module = index_python_module(TextDocument(path.as_uri(), text), name)
    assert module.module is not None
    index.replace(module.module)


def names(completer: PythonImportCompleter, parent: str, prefix: str = ""):
    return [item.name for item in completer.candidates(parent, prefix)]


def test_index_only_includes_namespace_parents_and_module_symbols(tmp_path):
    index = PythonIndex()
    add(
        index,
        tmp_path / "app/shared/formatter.py",
        "app.shared.formatter",
        "class Formatter:\n    pass\n",
    )
    completer = PythonImportCompleter(index)
    assert names(completer, "", "a") == ["app"]
    assert names(completer, "app", "sh") == ["shared"]
    assert names(completer, "app.shared") == ["formatter"]
    (candidate,) = completer.candidates("app.shared.formatter", "For")
    assert candidate.qualified_name == "app.shared.formatter.Formatter"
    assert candidate.symbol is not None
    assert candidate.symbol.kind is SymbolKind.CLASS


def test_selected_environment_discovery_does_not_import_modules(tmp_path):
    marker = tmp_path / "executed"
    write(tmp_path, "kivy/__init__.py", "raise RuntimeError('no import')")
    write(tmp_path, "kivy/uix/widget.py", "class Widget: pass\n")
    write(
        tmp_path,
        "app/shared/formatter.py",
        f"open({str(marker)!r}, 'w').close()\n"
        "class Formatter: pass\n"
        "def format_value(value: int) -> str: return str(value)\n"
        "DEFAULT = 1\n",
    )
    index = PythonIndex()
    completer = PythonImportCompleter(index, locator(tmp_path))
    assert names(completer, "kivy.uix", "wi") == ["widget"]
    assert names(completer, "app.shared.formatter") == [
        "DEFAULT",
        "Formatter",
        "format_value",
    ]
    assert not marker.exists()
    assert index.modules == ()


def test_overlay_index_takes_precedence_over_disk(tmp_path):
    path = write(tmp_path, "helpers.py", "class Old: pass\n")
    index = PythonIndex()
    add(index, path, "helpers", "class New: pass\n")

    def unexpected(uri):
        pytest.fail(f"Indexed module should not be reread: {uri}")

    completer = PythonImportCompleter(index, locator(tmp_path), unexpected)
    assert names(completer, "helpers") == ["New"]


def test_unindexed_module_uses_source_document_overlay(tmp_path):
    path = write(tmp_path, "helpers.py", "class Old: pass\n")
    overlay = TextDocument(path.as_uri(), "class New: pass\n", 3)
    completer = PythonImportCompleter(
        PythonIndex(), locator(tmp_path), lambda uri: overlay
    )
    assert names(completer, "helpers") == ["New"]


def test_stub_modules_and_stub_only_packages_are_preferred(tmp_path):
    write(tmp_path, "library/__init__.py", "class Runtime: pass\n")
    write(tmp_path, "library/helpers.py", "class RuntimeHelper: pass\n")
    write(tmp_path, "library/helpers.pyi", "class StubHelper: ...\n")
    write(tmp_path, "external-stubs/__init__.pyi", "class Public: ...\n")
    write(tmp_path, "external-stubs/child.pyi", "class Child: ...\n")
    write(tmp_path, "external-stubs/ignored.py", "class Ignored: pass\n")
    completer = PythonImportCompleter(PythonIndex(), locator(tmp_path))
    assert names(completer, "", "ext") == ["external"]
    assert names(completer, "library.helpers") == ["StubHelper"]
    assert names(completer, "external") == ["Public", "child"]


def test_explicit_relative_reexports_keep_alias_and_symbol_metadata(tmp_path):
    write(
        tmp_path,
        "app/shared/__init__.py",
        "from .formatter import Formatter as Display\n",
    )
    write(
        tmp_path,
        "app/shared/formatter.py",
        'class Formatter:\n    """Format application values."""\n',
    )
    completer = PythonImportCompleter(PythonIndex(), locator(tmp_path))
    (candidate,) = completer.candidates("app.shared", "Dis")
    assert candidate.name == "Display"
    assert candidate.qualified_name == "app.shared.Display"
    assert candidate.symbol is not None
    assert candidate.symbol.kind is SymbolKind.CLASS
    assert candidate.symbol.documentation == "Format application values."


def test_reexports_resolve_parent_relative_imports(tmp_path):
    write(tmp_path, "app/model.py", "class Model: pass\n")
    write(
        tmp_path,
        "app/shared/__init__.py",
        "from ..model import Model\n",
    )
    completer = PythonImportCompleter(PythonIndex(), locator(tmp_path))
    (candidate,) = completer.candidates("app.shared", "Model")
    assert candidate.symbol is not None
    assert candidate.symbol.qualified_name == "app.model.Model"


def test_reexport_cycles_terminate(tmp_path):
    write(tmp_path, "one.py", "from two import Thing\n")
    write(tmp_path, "two.py", "from one import Thing\n")
    completer = PythonImportCompleter(PythonIndex(), locator(tmp_path))
    assert names(completer, "one") == ["Thing"]


def test_star_imports_do_not_trigger_unbounded_expansion(tmp_path):
    write(tmp_path, "one.py", "from two import *\n")
    write(tmp_path, "two.py", "class Thing: pass\n")
    completer = PythonImportCompleter(PythonIndex(), locator(tmp_path))
    assert names(completer, "one") == []


def test_project_exclusions_and_environment_packages(tmp_path):
    source = tmp_path / "src"
    dependencies = source / ".venv/site-packages"
    write(source, "app/visible.py", "")
    write(source, "app/generated/hide.py", "")
    write(source, "app/cache/hidden.py", "")
    write(dependencies, "external/module.py", "")
    config = ServerConfig(
        project_root=tmp_path,
        source_roots=(source,),
        kv_paths=(source,),
        excludes=("generated", "app/cache/**", ".venv"),
    )
    index = PythonIndex()
    add(index, source / "app/generated/hide.py", "app.generated.hide", "")
    completer = PythonImportCompleter(
        index, locator(source, dependencies), config=config
    )
    assert names(completer, "app") == ["visible"]
    assert names(completer, "app.generated") == []
    assert names(completer, "external") == ["module"]


def test_immediate_discovery_never_walks_recursively(tmp_path, monkeypatch):
    write(tmp_path, "app/child/deep.py", "")

    def unexpected(*args, **kwargs):
        pytest.fail("Import completion must not recursively walk packages")

    monkeypatch.setattr("os.walk", unexpected)
    completer = PythonImportCompleter(PythonIndex(), locator(tmp_path))
    assert names(completer, "app") == ["child"]


def test_invalid_identifiers_and_non_module_files_are_not_offered(tmp_path):
    for name in (
        "class.py",
        "not-python.txt",
        "invalid-name.py",
        "valid.py",
        "__init__.py",
        "_private.py",
        "native.cpython-312-x86_64.so",
    ):
        write(tmp_path, name)
    (tmp_path / "__pycache__").mkdir()
    completer = PythonImportCompleter(PythonIndex(), locator(tmp_path))
    assert names(completer, "") == ["native", "valid"]
    assert names(completer, "", "_p") == ["_private"]
    assert names(completer, "../app") == []
    assert names(completer, "class") == []
    assert names(completer, "", "../") == []


def test_multiple_namespace_portions_are_merged(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    write(first, "namespace/left.py")
    write(second, "namespace/right.py")
    completer = PythonImportCompleter(PythonIndex(), locator(first, second))
    assert names(completer, "namespace") == ["left", "right"]


def test_regular_package_shadows_other_namespace_portions(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    write(first, "namespace/left.py")
    write(second, "namespace/__init__.py")
    write(second, "namespace/right.py")
    completer = PythonImportCompleter(PythonIndex(), locator(first, second))
    assert names(completer, "namespace") == ["right"]


def test_module_file_does_not_expose_adjacent_namespace_children(tmp_path):
    write(tmp_path, "module.py", "class Public: pass\n")
    write(tmp_path, "module/hidden.py")
    completer = PythonImportCompleter(PythonIndex(), locator(tmp_path))
    assert names(completer, "module") == ["Public"]


def test_subsequent_requests_observe_disk_changes(tmp_path):
    path = write(tmp_path, "helpers.py", "class First: pass\n")
    completer = PythonImportCompleter(PythonIndex(), locator(tmp_path))
    assert names(completer, "helpers") == ["First"]
    path.write_text("class Second: pass\n")
    assert names(completer, "helpers") == ["Second"]


def test_invalid_module_source_fails_softly(tmp_path):
    write(tmp_path, "helpers.py", "class MissingBody(\n")
    completer = PythonImportCompleter(PythonIndex(), locator(tmp_path))
    assert names(completer, "helpers") == []


def test_source_reads_are_bounded_for_many_reexports(tmp_path):
    imports = []
    for number in range(50):
        imports.append(f"from helper{number} import Public as Item{number}")
        write(tmp_path, f"helper{number}.py", "class Public: pass\n")
    write(tmp_path, "package.py", "\n".join(imports))
    reads = []

    def document(uri):
        reads.append(uri)
        path = Path(uri.removeprefix("file://"))
        return TextDocument(uri, path.read_text())

    completer = PythonImportCompleter(
        PythonIndex(), locator(tmp_path), document
    )
    assert len(completer.candidates("package", "Item")) == 50
    assert len(reads) <= 32


def test_index_filtering_checks_only_matching_immediate_child_paths(
    tmp_path, monkeypatch
):
    index = PythonIndex()
    for number in range(1000):
        name = f"unrelated.module{number}"
        symbol = ModuleSymbol(
            name, (tmp_path / f"module{number}.py").as_uri(), (), ()
        )
        index.replace(PythonModule(symbol, ()))
    for number in range(20):
        add(
            index,
            tmp_path / f"app/shared/part{number}.py",
            f"app.shared.part{number}",
            "",
        )
    completer = PythonImportCompleter(index)
    checked = []

    def allowed(uri):
        checked.append(uri)
        return True

    monkeypatch.setattr(completer, "_allowed_uri", allowed)
    assert names(completer, "app", "sh") == ["shared"]
    assert len(checked) == 1
    checked.clear()
    assert names(completer, "", "app") == ["app"]
    assert len(checked) == 1


def test_excluded_index_match_does_not_hide_an_allowed_sibling(tmp_path):
    index = PythonIndex()
    add(index, tmp_path / "app/cache/one.py", "app.cache.one", "")
    add(index, tmp_path / "app/shared/two.py", "app.shared.two", "")
    config = ServerConfig(
        project_root=tmp_path,
        source_roots=(tmp_path,),
        kv_paths=(tmp_path,),
        excludes=("cache",),
    )
    completer = PythonImportCompleter(index, config=config)
    assert names(completer, "", "app") == ["app"]


def test_filesystem_prefix_filter_precedes_path_checks(tmp_path):
    for number in range(1500):
        write(tmp_path, f"unrelated{number}.py")
    selected = write(tmp_path, "target.py")
    checked = []

    def allowed(path):
        checked.append(path)
        return True

    modules = locator(tmp_path)
    assert modules.module_children(prefix="target", allow_path=allowed) == (
        "target",
    )
    assert checked == [tmp_path, selected]
    write(tmp_path, "target_new.py")
    assert modules.module_children(prefix="target") == (
        "target", "target_new"
    )
