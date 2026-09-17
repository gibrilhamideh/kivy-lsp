from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

from kivy_lsp.config import load_config
from kivy_lsp.workspace.project import ProjectWorkspace

REPEATS = 7
DOCUMENTS = 40
PYTHON_FILES = 120


def measure(call, *args):
    start = time.perf_counter()
    cpu_start = time.process_time()
    call(*args)
    return (
        (time.perf_counter() - start) * 1000,
        (time.process_time() - cpu_start) * 1000,
    )


with tempfile.TemporaryDirectory(prefix="kivy-lsp-performance-") as temp:
    root = Path(temp)
    src = root / "src"
    src.mkdir()
    env = root / ".venv"
    env.mkdir()
    (env / "pyvenv.cfg").write_text("version = 3.12.14\n")
    package = env / "lib/python3.12/site-packages/kivy"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    model = (
        "from kivy.properties import (\n"
        "    StringProperty, NumericProperty, BooleanProperty\n"
        ")\n"
        "class Panel:\n"
        '    text = StringProperty("")\n'
        "    count = NumericProperty(0)\n"
        "    active = BooleanProperty(False)\n"
        "class Label(Panel):\n"
        "    pass\n"
    )
    (src / "models.py").write_text(model)
    for index in range(PYTHON_FILES - 1):
        (src / f"module_{index:03}.py").write_text(
            f"class Item{index}:\n"
            "    value: int = 0\n"
            "    def doubled(self) -> int:\n"
            "        return self.value * 2\n"
        )
    kv = 'Panel:\n    text: "Panel"\n'
    for index in range(12):
        kv += (
            "    Label:\n"
            f"        id: label_{index}\n"
            f'        text: "Label {index}"\n'
            f"        count: {index}\n"
            "        active: root.active\n"
        )
    files = [src / f"view_{index:02}.kv" for index in range(DOCUMENTS)]
    for path in files:
        path.write_text(kv)
    config = load_config(root)
    timings = {name: [] for name in ("initialize", "python_edit", "kv_edit")}
    indexed = None
    for repetition in range(REPEATS + 1):
        workspace = ProjectWorkspace(config)
        initial = measure(workspace.initialize)
        for path in files:
            workspace.open_document(path.as_uri(), kv, 1)
        python_uri = (src / "models.py").as_uri()
        # Editing a property type changes diagnostics in all 40 open views.
        changed_model = model.replace(
            'text = StringProperty("")', "text = NumericProperty(0)"
        )
        py_ms = measure(
            workspace.update_document, python_uri, changed_model, 2
        )
        assert all(workspace.diagnostics_for(path.as_uri()) for path in files)
        # Literal-only edit: no exported classes or include graph changes.
        kv_ms = measure(
            workspace.update_document,
            files[0].as_uri(),
            kv.replace('"Panel"', '"Changed"'),
            2,
        )
        if repetition:
            timings["initialize"].append(initial)
            timings["python_edit"].append(py_ms)
            timings["kv_edit"].append(kv_ms)
        indexed = len(workspace.python_index.modules)
    print(
        json.dumps(
            {
                "python_files": PYTHON_FILES,
                "open_kv_files": DOCUMENTS,
                "kv_lines_per_file": len(kv.splitlines()),
                "kv_bytes_per_file": len(kv.encode()),
                "indexed_python_modules": indexed,
                "samples_ms": timings,
                "median_ms": {
                    key: {
                        "wall": round(
                            statistics.median(v[0] for v in values), 3
                        ),
                        "cpu": round(
                            statistics.median(v[1] for v in values), 3
                        ),
                    }
                    for key, values in timings.items()
                },
            },
            indent=2,
        )
    )
