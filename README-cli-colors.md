# Colored check output

This archive contains only files changed since
`kivy-lsp-diagnostic-fixes.zip`. From the updated `kivy-lsp` repository root:

```sh
unzip -o ~/Downloads/kivy-lsp-cli-colors.zip -d .
```

If your terminal tool is installed in editable mode from this checkout,
the next command uses these changes immediately. Otherwise, from that same
repository root, install the checkout with:

```sh
uv tool install --force --editable .
hash -r
```

Run from your application project root:

```sh
kivy-lsp check
```

Errors are red, warnings yellow, information/hints cyan, and zero
error/warning totals green. Color is automatic for terminal output, with
plain text for pipes and files. Use `--color always` to force colors or
`--color never` to disable them. JSON stays free of color escapes in all
modes. `NO_COLOR` and `TERM=dumb` disable automatic coloring.

The change affects CLI presentation only. Diagnostic decisions, file
locations, and exit codes are unchanged. See `docs/cli.md` for all options.

Validation: all 30 CLI tests passed, including terminal detection, independent
stdout/stderr redirection, color overrides, and JSON compatibility. Pyright
and Ruff `E9,F` checks passed for the changed source, with Ruff also covering
the changed tests.
