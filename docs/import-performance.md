# Import completion performance fix

This update fixes two measured causes of slow `#:import` suggestions.

1. Candidate lookup previously resolved source paths and checked exclusions
   for every indexed module before filtering its name. It now filters the
   parent/prefix and removes duplicate immediate children first. Directory
   discovery also filters names before asking for filesystem metadata.
2. Editing an import's top-level path previously removed and reindexed
   unchanged dependencies. The workspace now retains each active package's
   index and metadata, scans newly requested packages, and removes only
   symbols it owns when their dependency is no longer needed.

The candidate lookup uses no persistent filesystem cache, so subsequent
requests can still see newly created files. Dependency refresh preserves
project overlays, restores individually shadowed dependency modules when
needed, and keeps package status coherent. Explicit project reload still
refreshes the environment and dependency sources.

Completion also accepts whitespace after `#:`, including this exact form:

```kv
#: import Formatter app.s
```

## Apply

Apply this after the main update and import-completion update. From the
`kivy-lsp` repository root:

```sh
unzip -o ~/Downloads/kivy-lsp-import-performance.zip -d .
```

Restart Neovim so the running language server reloads these files. The
existing Neovim configuration and editable installation can remain as they
are. Try the same import again after project initialization has completed.
No patch file needs to be applied.

## Measurements

A synthetic lookup test with 5,001 indexed modules measured the `app.sh`
completion service at about 752 ms before the fix and 1.27 ms afterwards.
A 1,500-entry directory lookup for prefix `app` dropped from about 121 ms to
1.02 ms. These measure server code in this container, not the user's editor.

The included repeatable typing benchmark uses 251 dependency source files
and 3,000 small synthetic classes. It initializes a fresh workspace for each
of three runs, then types partial import paths. The table contains median
milliseconds, with dependency source-read counts before/after:

| Path | Edit before | Edit after | Completion before | Completion after | Reads before / after |
| --- | ---: | ---: | ---: | ---: | ---: |
| `a` | 717.41 | 1.17 | 22.61 | 0.66 | 251 / 0 |
| `ap` | 742.25 | 1.06 | 28.44 | 0.60 | 251 / 0 |
| `app` | 725.21 | 1.33 | 21.46 | 0.59 | 251 / 0 |
| `app.sh` | 0.33 | 0.36 | 23.08 | 0.86 | 0 / 0 |

Initialization is excluded from those edit/request timings. Source-read
counts prove that unchanged dependencies are no longer reparsed while typing
these prefixes. Timings vary with hardware, filesystem, project size, and
system load. Raw samples are in `import-performance-samples.json`.

A dependency not yet indexed can still incur its initial scan when first
needed. The fix removes repeated work for unchanged dependencies; it does
not make all project initialization or new dependency indexing free.

## Verify

From the LSP repository root, using its editable environment:

```sh
.venv/bin/python scripts/benchmark_import_completion.py
```

This creates and removes a temporary synthetic project. It does not execute
an application or modify an existing project. In the JSON result,
`dependency_reads` should remain zero while typing `a`, `ap`, and `app` after
initialization. Compare revisions by running the same script with each
source tree selected through `PYTHONPATH`.

The complete regression suite passes **275 tests**. New checks cover bounded
candidate path checks, immediate filesystem visibility, unchanged dependency
read/walk counts, new/removal/readdition of dependencies, stub preference,
overlay ownership, targeted restoration, metadata cleanup, project reload,
and the spaced directive form. Pyright reports zero errors and warnings;
Ruff `E9,F` passes. Changed/new Python lines stay within 79 characters.
