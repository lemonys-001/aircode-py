# Aircode

Aircode is an independent Python coding agent for the terminal, a native desktop
window, and the browser. It talks to the OpenCode Zen gateway with its own OAuth
device login, so you can use the free Zen models without pasting a paid API key.

This repository is `aircode-py`. It is not affiliated with OpenCode, SST, or any
other product called Aircode. It is a clean-room Python implementation.

## Status

Working today:

- OpenCode Zen device login, token refresh, workspace selection
- Streaming chat with reasoning, tool calls and usage
- SQLite sessions: resume, list, export, delete
- Tools: read, write, edit, bash, glob, grep, list
- Permission prompts for writes, edits and shell commands
- Three interfaces: Textual TUI, PySide6 native window, Svelte web app
- Mode picker when you run `aircode-py` with no arguments
- Process-pool helper and a `doctor` report for GIL / CPU use
- CI-built release binaries and `install.sh` / `install.ps1` installers

Planned for full parity: LSP, MCP, skills, custom agents and commands,
subagents, plugins, ACP, GitHub integration, share links and themes.
See [Roadmap](#roadmap).

## Install

See [INSTALL.md](INSTALL.md) for the full guide, platform notes and troubleshooting.
Requires Python 3.11 or newer; 3.13 is recommended.

Install a prebuilt release binary from anywhere:

```bash
curl -fsSL https://raw.githubusercontent.com/lemonys-001/aircode-py/main/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/lemonys-001/aircode-py/main/install.ps1 | iex
```

The installer downloads the CI-built binary for your platform, verifies its
SHA-256 checksum, installs `aircode-py`, and adds it to PATH. If no binary is
available, it falls back to a source install. Force the source path with
`sh install.sh --source --gui`; drop `--gui` for the TUI and web UI only.
Source install works on any platform with Python 3.11+.

Or run it from a checkout:

```bash
sh install.sh
```

From source manually:

```bash
git clone https://github.com/lemonys-001/aircode-py.git
cd aircode-py
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[gui]"
```

Or with uv:

```bash
uv venv
uv pip install -e ".[gui]"
```

The `gui` extra installs PySide6, which is large. Without it, the launcher offers
the TUI and the web interface only.

## Log in

```bash
aircode-py auth login
```

A code and a URL are printed. Approve in the browser, pick a workspace if you
have more than one, and the token is stored at:

- Linux: `~/.local/share/aircode/auth.json` (mode 600)
- macOS: `~/Library/Application Support/Aircode/auth.json`
- Windows: `%LOCALAPPDATA%\Aircode\auth.json`

The free Zen tier is gated to OpenCode's own client and can change. Aircode sends
the same request shape, but it is not guaranteed to keep working. If it stops,
use an API-key provider or a local model; the provider layer is separate from the
rest of the app.

## Run

```bash
aircode-py            # mode picker: native desktop, terminal, web, headless
aircode-py gui        # native PySide6 window
aircode-py tui        # terminal interface
aircode-py web        # local web server + browser UI
aircode-py run "explain this repo"
aircode-py chat       # headless REPL
```

`aircode-py` with no arguments defaults to the native window when a display and
PySide6 are available, otherwise the TUI. The web server binds `127.0.0.1` by
default. If you bind a public address without a token, Aircode generates one and
prints it.

### Model

```bash
aircode-py models
aircode-py run -m deepseek-v4.1-flash-free "fix the failing test"
```

The default is `opencode/deepseek-v4.1-flash-free`. The model list comes from
`https://opencode.ai/zen/v1/models` and is cached for a day.

### Sessions

```bash
aircode-py sessions list
aircode-py sessions show <id>
aircode-py sessions export <id> --out session.json
```

Sessions are stored in SQLite in the app data directory.

## Configuration

Copy `config.example.toml` from the repository to your config directory. A project can override it in
`.aircode/config.toml`. Project instructions are read from `AGENTS.md` in the
working directory or its parents.

Permission policies are `ask`, `allow` or `deny` for `write`, `edit`, `bash` and
`webfetch`. `--auto` on `run`/`chat` approves everything for that process.

## Design

```
CLI        TUI (Textual)      GUI (PySide6)      Web (FastAPI + Svelte)
   \____________|___________________|____________________/
                        core (async, no UI imports)
   auth | catalog | provider | sessions | agent | tools | permissions
                        |
              SQLite (WAL) + config + auth.json
```

- One async engine, four front ends. Only the interfaces differ.
- Streaming is parsed from SSE in `provider.py`; the agent loop accumulates tool
  calls, asks for permission, runs tools, and persists every message.
- Secrets never enter the repository or logs.
- The web server serves the built Svelte app from `src/aircode/web/dist`.

### GIL and multiple cores

CPython's GIL is a build-time option. You cannot remove it from a normal 3.13
installation. Aircode therefore does three things:

1. All network, streaming and subprocess work is async, so many sessions and
   tools make progress without waiting.
2. CPU-heavy work runs through `ProcessPoolExecutor` with one worker per core
   (`aircode/parallel.py`). `aircode-py doctor --bench` measures the speedup.
3. If you run on a free-threaded build (3.14t, `PYTHON_GIL=0`), the same code
   uses threads without the GIL; `aircode-py doctor` reports which build you are on.

There is no global engine lock: sessions are independent async tasks and SQLite
uses WAL with short transactions.

## Security

- Write, edit and bash tools require explicit approval unless configured
  otherwise. Read-only tools run without a prompt inside the project; reads
  outside it ask first.
- The web server is local-only by default, and requires a bearer token when it is
  not.
- Auth files are written with mode 600. Tokens are never printed by `doctor` or
  included in session exports.
- Treat model output as untrusted. Review commands and diffs before approving.

## Roadmap

| Phase | Work |
|---|---|
| Done | Auth, Zen provider, sessions, tools, TUI, GUI, web, mode picker |
| Next | LSP diagnostics, MCP client, skills, custom agents and slash commands, subagents |
| Later | OpenCode JS plugin host, ACP server, GitHub workflow, share links, themes, packaging |

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
cd web && npm install && npm run build
```

## License

MIT. See `LICENSE`.
