# Synthetic workspace performance comparison

Measured on 17 September 2026 with Python 3.12.14, comparing the uploaded
source snapshot (`code_review/src`) against the implementation in this
package. These are measurements of a controlled synthetic project in the
shared development container, not latency guarantees for real applications.

## Workload and measurement

The reproducible script is `scripts/benchmark_workspace.py`. It creates only
temporary synthetic files and does not import or execute a Kivy application.
Both revisions use the same interpreter and fixture:

- 120 project Python modules, including two related widget classes.
- A minimal discoverable `.venv` with one empty `kivy` package. Both variants
  indexed exactly 121 Python modules; real Kivy dependency indexing is
  deliberately outside this measurement.
- 40 saved/open KV documents, each 62 lines and 1,231 UTF-8 bytes, containing
  a root widget and 12 child widgets.
- One Python edit changes a shared property from `StringProperty` to
  `NumericProperty`. The script checks that every open KV document receives
  a corresponding internal diagnostic.
- One ordinary KV edit changes a string literal in a root-widget document.
  It changes neither exported class/ID declarations nor dependencies.
- One warm-up iteration, followed by seven measured iterations per revision.
  Each iteration creates and initializes a fresh workspace, then opens all
  40 KV files before measuring the edits.

Measurements cover synchronous workspace initialization/update methods.
Opening all buffers is setup, not part of the reported edit durations. LSP
serialization, transport, and editor rendering are excluded. The baseline
already recomputed internal KV diagnostics after Python edits, but did not
publish all of them to the editor; that publication defect is fixed in the
implementation.

## Results

Wall-clock medians, in milliseconds:

| Operation | Uploaded baseline | Implementation | Change |
| --- | ---: | ---: | ---: |
| Initialize | 237.144 | 248.569 | +4.8% |
| Python edit affecting 40 open KV documents | 161.925 | 184.815 | +14.1% |
| Literal edit in one KV document | 15.552 | 11.871 | -23.7% |

Corresponding process-CPU medians were 237.092/248.575 ms for initialization,
160.283/184.818 ms for the Python edit, and 15.557/11.875 ms for the KV edit
(baseline/implementation). CPU and wall values are close in this run, but
absolute timings varied between runs. Treat small changes cautiously. Raw
samples, with each pair ordered `[wall_ms, cpu_ms]`, are preserved in
`docs/performance_samples.json`.

## Regression found and corrected

An earlier version of the implementation rebuilt every open semantic model
on every KV edit. A preliminary five-sample run measured an ordinary KV
literal edit at 164.7 ms versus the baseline's 10.7 ms. This was a concrete
regression caused by excessive invalidation.

The implementation now compares indexed KV declarations and the Python index
revision around an update. When neither changes, it analyzes only the edited
KV document. A class/ID declaration change or dependency change still rebuilds
the open consumers. Diagnostic publication still covers affected open
snapshots, including cleared errors. A regression test verifies the local
edit behavior; a separate test verifies that changing a dynamic class base
adds and clears diagnostics in an already-open consumer.

## Remaining costs and limits

The final Python edit remained approximately 14% slower in this fixture.
A diagnostic profiling run identified 4,440 calls to the new embedded-Python
source mapping helper for 1,480 analyzed expressions (three calls per
expression), taking about 57 ms cumulative under instrumentation. This
mapping preserves KV-to-Python offsets, including Unicode, and contributes
to the additional work. Profiling changes absolute timings, so this number
is evidence of where time is spent, not an exact attribution of the
unprofiled difference. Correct lexical-scope analysis also adds work.

No background scheduler or additional global cache was introduced. Reusing
embedded-expression analysis results is a possible follow-up after measuring
real project files. Initialization now also retains parsed source snapshots
and resolves static includes; the modest observed initialization increase
has not been established as a broadly reproducible regression.

The fixture does not measure a real Kivy installation, large include graphs,
dynamic-class-heavy projects, rename indexing, formatter throughput, startup
memory, editor round-trip latency, or a project with thousands of documents.
Those require representative project measurements before drawing wider
performance conclusions.

## Reproduction

Run both commands with the same Python 3.12 interpreter. Use the original
source archive for the baseline and this package for the implementation:

```sh
PYTHONPATH=/path/to/original/src \
    python /path/to/updated/scripts/benchmark_workspace.py > baseline.json

PYTHONPATH=/path/to/updated/src \
    python /path/to/updated/scripts/benchmark_workspace.py > updated.json
```

The script needs only the standard library and the selected LSP source tree.
Use an interpreter without an independently installed Kivy package on its
import path, and verify that both outputs report `indexed_python_modules`
as 121. Otherwise the dependency scan includes additional environment
sources and the result is a different workload. The script cleans its
synthetic project automatically.
