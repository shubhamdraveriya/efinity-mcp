# Contributing

Bug reports, Efinity-version compatibility reports (especially Linux and other device families)
and pull requests are welcome.

## Setup

```bash
git clone https://github.com/shubhamdraveriya/efinity-mcp
cd efinity-mcp
python -m venv .venv
.venv/bin/pip install -e ".[dev]"       # Windows: .venv\Scripts\pip install -e ".[dev]"
```

## Tests

- `pytest` runs the unit tests. They use recorded report files in `tests/fixtures` and a fake
  Efinity folder, so they run anywhere (this is what CI runs).
- `pytest -m efinity -v` runs end-to-end tests against a real Efinity install: it creates a
  scratch project in a temp folder, builds its interface, compiles it, and checks the results.
  It takes about 2 minutes and never touches your own projects.
- `ruff check .` must pass.

## Layout

| File | What it holds |
|---|---|
| `src/efinity_mcp/server.py` | MCP tool definitions (the tool docstrings are what the assistant reads) |
| `src/efinity_mcp/env.py` | Finding Efinity and recreating `setup.bat` / `setup.sh` |
| `src/efinity_mcp/project.py` | Project `.xml` reading and editing |
| `src/efinity_mcp/reports.py` | Report and log parsers |
| `src/efinity_mcp/jobs.py` | Background jobs (efx_run, programmer, STA) |
| `src/efinity_mcp/interface.py` | Runs the Interface Designer worker |
| `src/efinity_mcp/pt_helper.py` | The worker. It runs under **Efinity's** Python, so standard library and Efinity modules only |

## Guidelines

- Raise `ValueError`, `RuntimeError`, `OSError` or `KeyError` for expected failures, with a message
  that tells the user what to do. The server turns these into readable tool errors; any other
  exception reaches the client only as "Error executing tool".
- Anything that changes files must be safe to run by an AI: validate inputs, make a backup, and
  don't save a half-applied change.
- Never add Efinix code or documents to this repository.
- If you add report parsing, add a trimmed real report to `tests/fixtures` with personal paths
  removed.
