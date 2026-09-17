"""Compare import typing with different source trees on PYTHONPATH."""

from __future__ import annotations

import json
from pathlib import Path
import statistics
import tempfile
import time

from kivy_lsp.config import load_config
from kivy_lsp.features.completion import _completion_result
from kivy_lsp.workspace import dependency_scanner
from kivy_lsp.workspace.project import ProjectWorkspace

TARGETS = ("", "a", "ap", "app", "app.", "app.s", "app.sh", "app.sha")
REPEATS = 3
DEPENDENCY_MODULES = 250


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="kivy-import-benchmark-") as name:
        root = Path(name)
        src = root / "src"
        (src / "app/shared").mkdir(parents=True)
        (src / "app/__init__.py").write_text("")
        (src / "app/shared/formatter.py").write_text(
            "class Formatter:\n    pass\n"
        )
        env = root / ".venv"
        package = env / "lib/python3.12/site-packages/kivy"
        package.mkdir(parents=True)
        (env / "pyvenv.cfg").write_text("version = 3.12\n")
        (package / "__init__.py").write_text("")
        (root / "kivy-lsp.toml").write_text(
            'python-environment = ".venv"\n'
        )
        for number in range(DEPENDENCY_MODULES):
            source = "\n".join(
                f"class Item{number}_{item}:\n"
                "    value: int = 0\n"
                "    def show(self) -> str:\n"
                "        return str(self.value)\n"
                for item in range(12)
            )
            (package / f"module_{number}.py").write_text(source)

        timings: dict[str, list[tuple[float, float, int]]] = {
            target: [] for target in TARGETS
        }
        original_read = dependency_scanner._read_source
        reads: list[Path] = []

        def counted_read(path: Path) -> str:
            reads.append(path)
            return original_read(path)

        dependency_scanner._read_source = counted_read
        try:
            for _ in range(REPEATS):
                workspace = ProjectWorkspace(load_config(root))
                workspace.initialize()
                uri = (src / "view.kv").as_uri()
                for version, target in enumerate(TARGETS, 1):
                    text = "#:import Formatter " + target
                    reads.clear()
                    start = time.perf_counter()
                    workspace.update_document(uri, text, version)
                    update_ms = (time.perf_counter() - start) * 1000
                    document = workspace.document(uri)
                    assert document is not None
                    start = time.perf_counter()
                    result = _completion_result(
                        workspace, document, uri, len(text)
                    )
                    complete_ms = (time.perf_counter() - start) * 1000
                    if target == "app.sh":
                        assert result is not None
                        assert "shared" in {
                            item.label for item in result.items
                        }
                    timings[target].append(
                        (update_ms, complete_ms, len(reads))
                    )
        finally:
            dependency_scanner._read_source = original_read

        print(
            json.dumps(
                {
                    "dependency_modules": DEPENDENCY_MODULES + 1,
                    "repeats": REPEATS,
                    "samples": timings,
                    "medians": {
                        target: {
                            "update_ms": round(
                                statistics.median(
                                    sample[0] for sample in samples
                                ),
                                3,
                            ),
                            "completion_ms": round(
                                statistics.median(
                                    sample[1] for sample in samples
                                ),
                                3,
                            ),
                            "dependency_reads": statistics.median(
                                sample[2] for sample in samples
                            ),
                        }
                        for target, samples in timings.items()
                    },
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
