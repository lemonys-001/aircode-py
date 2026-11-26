# Installation

Aircode is a Python 3.11+ application. The native GUI additionally needs
PySide6, which is a large optional dependency. The TUI and web UI work without
it.

## Install a release binary

GitHub Actions builds these on every `v*` tag:

- Linux x86_64 and aarch64
- macOS x86_64 and arm64
- Windows x86_64

From anywhere, run the installer straight from GitHub:

```bash
gh api repos/lemonys-001/aircode-py/contents/install.sh \
  -H "Accept: application/vnd.github.raw" | sh
```

Or from a checkout:

```bash
sh install.sh
```

On Windows:

```powershell
.\install.ps1
```

The script downloads the asset for this machine, verifies `SHA256SUMS` when it
is present, and installs to `~/.local/bin/aircode` (Windows:
`%LOCALAPPDATA%\Aircode\bin\aircode.exe`). Use `--prefix DIR` to change that.
The Linux aarch64 build is made under QEMU in CI, so it is slower to produce.
The Linux binaries target Ubuntu 22.04 (x86_64) and 24.04 (aarch64); use
`--source` on older systems.

This repository is private, so use `gh auth login` first or set `GITHUB_TOKEN`.
The installer tries `gh release download` before `curl`/`wget`. A public fork
can use the plain URL:

```bash
curl -fsSL https://raw.githubusercontent.com/lemonys-001/aircode-py/main/install.sh | sh
```

If no release binary is available, the same command installs from source
automatically. On a desktop it includes the native window; on a headless Linux
machine it installs the TUI and web UI only. Force a source install with:

```bash
sh install.sh --source --gui
```

`--source` uses this checkout when it finds `pyproject.toml`, otherwise the
repository URL. Drop `--gui` for the TUI and web UI only.

The macOS binaries are unsigned. The installer clears the quarantine flag, but
Gatekeeper may still ask you to allow the app in System Settings.

## Quick start from source

```bash
git clone https://github.com/lemonys-001/aircode-py.git
cd aircode-py

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[gui]"

aircode --version
aircode doctor
aircode auth login
aircode
```

`aircode doctor` reports Python, GIL, CPU workers, the database, credentials,
the web build and whether PySide6 and a display are available.

## Publish a release

```bash
git tag v0.1.0
git push origin v0.1.0
```

The workflow `.github/workflows/release.yml` builds the assets and publishes the
GitHub release. It can also be run manually from the Actions tab; that run only
uploads workflow artifacts, not a release.

## Without the native GUI

```bash
python -m pip install -e .
```

Then `aircode` offers the terminal and web interfaces. `aircode gui` will not
work until PySide6 is installed:

```bash
python -m pip install "PySide6>=6.8"
```

## With uv

```bash
uv venv --python 3.13
uv pip install -e ".[gui]"
. .venv/bin/activate
aircode doctor
```

## Windows

Use 64-bit Python 3.13. From PowerShell:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[gui]"
aircode doctor
```

If PowerShell blocks the activation script for the current process:

```powershell
Set-ExecutionPolicy -Scope Process RemoteSigned
.venv\Scripts\Activate.ps1
```

## macOS

```bash
brew install python@3.13
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[gui]"
```

If the Qt window does not open, run `aircode doctor` and check that PySide6 is
installed. Reinstalling it usually fixes a missing platform plugin:

```bash
python -m pip install --force-reinstall "PySide6>=6.8"
```

## Linux

The GUI needs the usual Qt/X11 runtime libraries. On Debian or Ubuntu, for
example:

```bash
sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libegl1
```

Then install Aircode as above. On a headless machine, use the TUI or web UI; the
native window needs `DISPLAY` or `WAYLAND_DISPLAY`.

## Install from a wheel

```bash
uv build --wheel
python -m pip install dist/aircode_py-*.whl
```

The wheel already contains the built web UI. For the native window, also install
`PySide6>=6.8`.

## Log in

```bash
aircode auth login
```

Approve the printed code in a browser, then choose a workspace if you have more
than one. Use `aircode auth login --no-browser` to only print the URL and code.

```bash
aircode auth status
aircode auth import              # import an existing OpenCode credential
aircode auth logout
```

Tokens are stored with mode `600`:

- Linux: `~/.local/share/aircode/auth.json`
- macOS: `~/Library/Application Support/Aircode/auth.json`
- Windows: `%LOCALAPPDATA%\Aircode\auth.json`

The free Zen tier is gated to OpenCode's own client and may change. If it stops
working, use an API-key provider or a local model. Aircode is not affiliated
with OpenCode.

## Run

```bash
aircode                         # mode picker
aircode tui                     # terminal
aircode gui                     # native window
aircode web                     # local web server
aircode web --open              # start and open the browser
aircode run "explain this repo" # one prompt, headless
aircode chat                    # headless chat REPL
```

Useful options:

```bash
aircode run -m deepseek-v4.1-flash-free "fix the failing test"
aircode run --auto "format the project"   # skip tool prompts for this run
aircode run -s <session-id> "continue"
aircode run --cwd /path/to/project "summarise the code"
aircode models --refresh
aircode sessions list
aircode sessions show <session-id>
```

`--auto` approves writes, edits and shell commands for that process. Use it only
on a project you trust.

## Web UI

The server binds `127.0.0.1` by default. Binding a non-local address generates a
bearer token and prints a URL. The token is in the URL fragment, so the browser
does not send it to the server:

```bash
aircode web --host 0.0.0.0 --port 4096
```

Open the printed URL, or append `#token=<token>` to the local URL. Do not share
that URL.

The built Svelte app is committed at `src/aircode/web/dist`. Node is only needed
to change the frontend:

```bash
cd web
npm install
npm run build
```

Node 20 or newer is recommended. Vite writes the result to
`src/aircode/web/dist`.

## Configuration

Aircode works with defaults. To customise it:

```bash
mkdir -p ~/.config/aircode
cp config.example.toml ~/.config/aircode/config.toml
```

A project can override the global file in `.aircode/config.toml`. Project
instructions are read from `AGENTS.md` in the working directory or its parents.

Permission policies are `ask`, `allow` or `deny` for `write`, `edit`, `bash` and
`webfetch`. Read-only tools run without a prompt inside the project; reads
outside it ask first.

## Verify the install

```bash
aircode doctor
aircode models --refresh
aircode run "reply with ok"
```

For development:

```bash
python -m pip install -e ".[dev]"
pytest
ruff check src tests
```

## Troubleshooting

### `No terminal and no display`

Run `aircode tui` in a terminal, or start `aircode web` and open the printed URL.
The native window needs a graphical session and PySide6.

### Qt platform plugin errors on Linux

Install the Qt/X11 runtime packages listed above. If it still fails, reinstall
PySide6 and check `aircode doctor` from the same virtual environment.

### Imports fail after a uv install

A corrupted uv cache can produce packages with missing files. Recreate the venv
or reinstall the affected package without the cache:

```bash
uv cache clean
uv pip install --reinstall --no-cache certifi
```

A common symptom is `AttributeError: module 'certifi' has no attribute 'where'`.

### `npm install` fails with EACCES

Some removable or shared filesystems are mounted `noexec`. Build the frontend in
a normal temporary directory, then copy the output back:

```bash
cp -r web /tmp/aircode-web
cd /tmp/aircode-web
npm install
npm run build
cp -r /tmp/src/aircode/web/dist/. /path/to/aircode-py/src/aircode/web/dist/
```

### The free Zen tier rejects requests

That is an upstream access restriction, not a local install problem. Use an API
key provider or a local model. See the security and provider notes in
[README.md](README.md).

## Uninstall

```bash
python -m pip uninstall aircode-py
```

To remove local data as well, delete the config, data and cache directories
reported by `aircode doctor`. This deletes sessions and the stored token.
