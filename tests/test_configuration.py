from pathlib import Path
import sys
import sysconfig

import pytest

from kivy_lsp.config import (
    CONFIG_FILENAME, ConfigError, GlobalImport, LibraryExports, ServerConfig,
    find_project_root, load_config, load_library_exports,
)
from kivy_lsp.python.environment import discover_python_environment
from kivy_lsp.python.locator import PythonModuleLocator


def test_format_defaults_and_project_width(tmp_path):
    assert load_config(tmp_path).format.line_length == 79
    (tmp_path / CONFIG_FILENAME).write_text(
        "[format]\nline-length = 100\n"
    )
    assert load_config(tmp_path).format.line_length == 100


def test_diagnostics_default_is_conservative_and_strict_is_optional(tmp_path):
    assert load_config(tmp_path).diagnostics.strict is False
    (tmp_path / CONFIG_FILENAME).write_text(
        "[diagnostics]\nstrict = true\n"
    )
    assert load_config(tmp_path).diagnostics.strict is True
    (tmp_path / CONFIG_FILENAME).write_text(
        "[diagnostics]\nstrict = false\n"
    )
    assert load_config(tmp_path).diagnostics.strict is False


@pytest.mark.parametrize("value", ['"true"', "1", "[]", "{}"])
def test_invalid_diagnostics_strict_reports_configuration_error(
    tmp_path, value
):
    (tmp_path / CONFIG_FILENAME).write_text(
        f"[diagnostics]\nstrict = {value}\n"
    )
    with pytest.raises(ConfigError, match="diagnostics.*strict"):
        load_config(tmp_path)


@pytest.mark.parametrize("value", ["true", '"79"', "0", "-1", "3"])
def test_invalid_format_width_has_configuration_error(tmp_path, value):
    (tmp_path / CONFIG_FILENAME).write_text(
        f"[format]\nline-length = {value}\n"
    )
    with pytest.raises(ConfigError, match="format"):
        load_config(tmp_path)


def test_qualified_projections_do_not_leak(tmp_path):
    config = ServerConfig(
        tmp_path,
        (tmp_path,),
        (tmp_path,),
        member_projections={"my.State": 0},
    )
    assert config.member_projection_for("my.State") == 0
    assert config.member_projection_for("unrelated.State") is None
    assert config.member_projection_for("State") is None
    config = ServerConfig(
        tmp_path,
        (tmp_path,),
        (tmp_path,),
        member_projections={"State": 1, "my.State": 0},
    )
    assert config.member_projection_for("my.State") == 0
    assert config.member_projection_for("unrelated.State") == 1


def test_explicit_environment_preserves_roots_without_host_packages(tmp_path):
    env = tmp_path / "external_env"
    packages = env / "lib" / "python3.12" / "site-packages"
    packages.mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("version = 3.12.14\n")
    (env / "bin").mkdir()
    (env / "bin" / "python").symlink_to(sys.executable)
    config = ServerConfig(
        tmp_path, (tmp_path,), (tmp_path,), python_environment=env
    )
    discovered = discover_python_environment(config)
    assert discovered.virtual_environment == env
    assert discovered.site_packages == (packages,)
    assert discovered.version == sys.version.split()[0]
    assert not discovered.issues


def test_interpreter_path_keeps_venv_symlink(tmp_path):
    env = tmp_path / "external_env"
    (env / "bin").mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("version = 3.12.14\n")
    interpreter = env / "bin" / "python"
    interpreter.symlink_to(Path(sys.executable))
    (tmp_path / CONFIG_FILENAME).write_text(
        'python-interpreter = "external_env/bin/python"\n'
    )
    config = load_config(tmp_path)
    assert config.python_interpreter == interpreter
    assert discover_python_environment(config).virtual_environment == env


def test_environment_selectors_are_mutually_exclusive(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text(
        'python-interpreter = "python"\n'
        'python-environment = ".venv"\n'
    )
    with pytest.raises(ConfigError, match="only one"):
        load_config(tmp_path)


def test_missing_environment_reports_issue_without_host_fallback(tmp_path):
    config = ServerConfig(
        tmp_path,
        (tmp_path,),
        (tmp_path,),
        python_environment=tmp_path / "missing",
    )
    environment = discover_python_environment(config)
    assert environment.virtual_environment is None
    assert not environment.site_packages
    assert environment.issues


def test_current_interpreter_can_be_selected(tmp_path):
    config = ServerConfig(
        tmp_path,
        (tmp_path,),
        (tmp_path,),
        python_interpreter=Path(sys.executable),
    )
    environment = discover_python_environment(config)
    assert environment.version
    assert not environment.issues


@pytest.mark.parametrize("selector", ["environment", "interpreter"])
def test_selected_venv_locates_stdlib_without_host_packages(
    tmp_path, selector
):
    env = tmp_path / "venv"
    packages = env / "lib" / "python3.12" / "site-packages"
    packages.mkdir(parents=True)
    (env / "bin").mkdir()
    executable = env / "bin" / "python"
    executable.symlink_to(sys.executable)
    (env / "pyvenv.cfg").write_text(f"version = {sys.version.split()[0]}\n")
    config = ServerConfig(
        tmp_path,
        (tmp_path,),
        (tmp_path,),
        python_environment=env if selector == "environment" else None,
        python_interpreter=executable if selector == "interpreter" else None,
    )
    environment = discover_python_environment(config)
    assert not environment.issues
    assert environment.site_packages == (packages,)
    assert Path(sysconfig.get_path("purelib")) not in environment.search_paths
    assert PythonModuleLocator(environment).find_module("pathlib") is not None
    assert PythonModuleLocator(environment).find_module("json") is not None


def test_missing_interpreter_is_not_accepted_by_its_venv_parent(tmp_path):
    env = tmp_path / "venv"
    (env / "bin").mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("version = 3.12.14\n")
    executable = env / "bin" / "missing"
    config = ServerConfig(
        tmp_path, (tmp_path,), (tmp_path,), python_interpreter=executable
    )
    environment = discover_python_environment(config)
    assert environment.virtual_environment is None
    assert environment.version is None
    assert environment.search_paths == (tmp_path,)
    assert any(issue.path == executable for issue in environment.issues)


def test_venv_without_interpreter_can_use_cfg_home_and_version(tmp_path):
    env = tmp_path / "venv"
    env.mkdir()
    home = tmp_path / "base" / "bin"
    stdlib = home.parent / "lib" / "python3.12"
    stdlib.mkdir(parents=True)
    (stdlib / "pathlib.py").write_text("class Path: pass\n")
    (env / "pyvenv.cfg").write_text(f"home = {home}\nversion = 3.12.14\n")
    config = ServerConfig(
        tmp_path, (tmp_path,), (tmp_path,), python_environment=env
    )
    environment = discover_python_environment(config)
    assert not environment.issues
    assert environment.version == "3.12.14"
    assert PythonModuleLocator(environment).find_module("pathlib") is not None


def test_incomplete_venv_metadata_reports_unknown_stdlib(tmp_path):
    env = tmp_path / "venv"
    env.mkdir()
    (env / "pyvenv.cfg").write_text("")
    config = ServerConfig(
        tmp_path, (tmp_path,), (tmp_path,), python_environment=env
    )
    environment = discover_python_environment(config)
    assert environment.version is None
    assert environment.search_paths == (tmp_path,)
    assert any(
        "standard library" in issue.message for issue in environment.issues
    )


def test_full_standalone_configuration_preserves_all_options(tmp_path):
    source = '''app-class = "app.application.App"
source-roots = ["src", "shared", "src"]
kv-paths = ["views"]
excludes = ["generated", "build"]
python-environment = ".venv"

[globals]
controller = "app.controller.Controller"
[global-imports]
md_icons = "library.icons.md_icons"
I18nRef = "library.i18n.I18nRef"
[member-projections]
"library.State" = 0
[subscript-projections]
"library.Rows" = 1
[i18n]
source = "translations/en.json"
properties = ["i18n_key", "hint_i18n_key"]
[format]
line-length = 95
[diagnostics]
strict = true
'''
    (tmp_path / CONFIG_FILENAME).write_text(source)
    config = load_config(tmp_path)
    assert config.project_root == tmp_path
    assert config.app_class == "app.application.App"
    assert config.source_roots == (tmp_path / "src", tmp_path / "shared")
    assert config.kv_paths == (tmp_path / "views",)
    assert config.excludes == ("generated", "build")
    assert config.python_environment == tmp_path / ".venv"
    assert config.globals == {"controller": "app.controller.Controller"}
    assert config.global_imports == (
        GlobalImport("md_icons", "library.icons.md_icons"),
        GlobalImport("I18nRef", "library.i18n.I18nRef"),
    )
    assert config.member_projections == {"library.State": 0}
    assert config.subscript_projections == {"library.Rows": 1}
    assert config.i18n is not None
    assert config.i18n.source == tmp_path / "translations" / "en.json"
    assert config.i18n.properties == ("i18n_key", "hint_i18n_key")
    assert config.format.line_length == 95
    assert config.diagnostics.strict


@pytest.mark.parametrize("pyproject", [
    '[tool.kivy-lsp.format]\nline-length = 120\n',
    'this is not valid TOML [',
])
def test_pyproject_settings_are_never_read(tmp_path, pyproject):
    (tmp_path / "pyproject.toml").write_text(pyproject)
    (tmp_path / "src").mkdir()
    config = load_config(tmp_path)
    assert config.format.line_length == 79
    assert config.source_roots == (tmp_path / "src",)
    (tmp_path / CONFIG_FILENAME).write_text("[format]\nline-length = 91\n")
    assert load_config(tmp_path).format.line_length == 91


def test_missing_standalone_file_uses_project_directory_defaults(tmp_path):
    config = load_config(tmp_path)
    assert config.source_roots == (tmp_path,)
    assert config.kv_paths == (tmp_path,)
    assert config.app_class is None
    assert config.globals == {}
    assert config.global_imports == ()


@pytest.mark.parametrize("contents", [
    '[tool.kivy-lsp]\napp-class = "app.App"\n',
    '[tool.kivy-lsp.format]\nline-length = 90\n',
    '[kivy-lsp]\napp-class = "app.App"\n',
])
def test_old_wrapper_is_rejected_with_migration_guidance(tmp_path, contents):
    path = tmp_path / CONFIG_FILENAME
    path.write_text(contents)
    for loader, argument in ((load_config, tmp_path),
                             (load_library_exports, path)):
        with pytest.raises(ConfigError, match="remove.*wrapper"):
            loader(argument)


@pytest.mark.parametrize("contents", ["[format", '[globals]\nx = "\n'])
def test_malformed_standalone_file_reports_its_path(tmp_path, contents):
    (tmp_path / CONFIG_FILENAME).write_text(contents)
    with pytest.raises(ConfigError, match="kivy-lsp.toml"):
        load_config(tmp_path)


def test_configuration_directory_is_not_treated_as_missing(tmp_path):
    (tmp_path / CONFIG_FILENAME).mkdir()
    with pytest.raises(ConfigError, match="Could not load"):
        load_config(tmp_path)


def test_relative_paths_use_configuration_directory(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / CONFIG_FILENAME).write_text(
        'source-roots = ["../shared"]\nkv-paths = ["views"]\n'
        '[i18n]\nsource = "translations.json"\n'
    )
    monkeypatch.chdir(tmp_path)
    config = load_config(Path("project"))
    assert config.source_roots == (tmp_path / "shared",)
    assert config.kv_paths == (root / "views",)
    assert config.i18n is not None
    assert config.i18n.source == root / "translations.json"


@pytest.mark.parametrize("contents", [
    'globals = []\n',
    '[globals]\n"bad-name" = "module.value"\n',
    '[globals]\nvalue = 4\n',
    '[globals]\nvalue = " "\n',
    '[global-imports]\n"bad-name" = "module.value"\n',
    '[global-imports]\nvalue = ""\n',
    '[global-imports]\nvalue = false\n',
    '[member-projections]\n"" = 0\n',
    '[member-projections]\nState = -1\n',
    '[member-projections]\nState = true\n',
    '[subscript-projections]\nState = "0"\n',
    'subscript-projections = 0\n',
])
def test_project_and_library_share_export_validation(tmp_path, contents):
    path = tmp_path / CONFIG_FILENAME
    path.write_text(contents)
    with pytest.raises(ConfigError):
        load_config(tmp_path)
    with pytest.raises(ConfigError):
        load_library_exports(path)


def test_library_reads_only_export_tables(tmp_path):
    path = tmp_path / CONFIG_FILENAME
    path.write_text('''app-class = "unrelated.App"
source-roots = false
kv-paths = 12
excludes = 1
python-environment = "unrelated-env"
python-interpreter = "unrelated-python"
i18n = false
format = false
diagnostics = false
[globals]
icons = "library.icons"
[global-imports]
I18nRef = "library.i18n.I18nRef"
[member-projections]
"library.State" = 0
[subscript-projections]
"library.Rows" = 1
''')
    assert load_library_exports(path) == LibraryExports(
        globals={"icons": "library.icons"},
        global_imports=(GlobalImport("I18nRef", "library.i18n.I18nRef"),),
        member_projections={"library.State": 0},
        subscript_projections={"library.Rows": 1},
    )


def test_empty_library_exports_and_missing_library_file(tmp_path):
    path = tmp_path / CONFIG_FILENAME
    path.write_text("")
    assert load_library_exports(path) == LibraryExports()
    path.unlink()
    with pytest.raises(ConfigError, match="Could not load"):
        load_library_exports(path)


@pytest.mark.parametrize("contents", [
    'source-roots = []\n',
    'kv-paths = "views"\n',
    'app-class = 1\n',
    'excludes = [""]\n',
    '[i18n]\nsource = ""\n',
    '[i18n]\nsource = "en.json"\nproperties = []\n',
    '[i18n]\nsource = "en.json"\nproperties = ["bad-name"]\n',
])
def test_invalid_project_options_report_config_error(tmp_path, contents):
    (tmp_path / CONFIG_FILENAME).write_text(contents)
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_root_discovery_prefers_config_over_nearer_pyproject(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / CONFIG_FILENAME).write_text("")
    nested = tmp_path / "packages" / "app"
    nested.mkdir(parents=True)
    (nested / "pyproject.toml").write_text("malformed ignored TOML [")
    assert find_project_root(nested) == tmp_path
    assert find_project_root(nested / "new.kv") == tmp_path


def test_root_discovery_uses_nearest_dedicated_config(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text("")
    nested = tmp_path / "app"
    nested.mkdir()
    (nested / CONFIG_FILENAME).write_text("")
    assert find_project_root(nested / "new.kv") == nested


@pytest.mark.parametrize("git_is_file", [False, True])
def test_root_discovery_stops_at_nearest_git_boundary(tmp_path, git_is_file):
    (tmp_path / CONFIG_FILENAME).write_text("")
    nested = tmp_path / "app"
    nested.mkdir()
    if git_is_file:
        (nested / ".git").write_text("gitdir: /some/worktree\n")
    else:
        (nested / ".git").mkdir()
    assert find_project_root(nested / "new.kv") == nested


def test_root_discovery_falls_back_to_nearest_project_marker(tmp_path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "app"
    nested.mkdir()
    (nested / "pyproject.toml").write_text("")
    assert find_project_root(nested / "new.kv") == nested
    (nested / "pyproject.toml").unlink()
    assert find_project_root(nested / "new.kv") == tmp_path


def test_root_discovery_without_markers_uses_starting_directory(tmp_path):
    assert find_project_root(tmp_path) == tmp_path
    assert find_project_root(tmp_path / "new.kv") == tmp_path
