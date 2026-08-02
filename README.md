# Agent Readout

Local dashboard for **Claude Code**, **Codex**, and **pi** session usage — inspired by Readout’s UI, with current model pricing (including Fable, Sonnet 5, Opus 4.8+, Grok, GPT‑5.x).

> Runs only on **your machine**. It reads local session logs. It is not a multi-tenant cloud app.

## Three ways to run it

| Target | Best for | Command |
|--------|----------|---------|
| **1. Web** | Dev, simplest | `python3 -m agent_readout` |
| **2. Desktop WebView** | Native window, light | `python3 desktop/webview/app.py` |
| **3. Desktop Electron** | Packaged app / multi-OS releases | `cd desktop/electron && npm start` |

GitHub Releases ship downloadable builds for each target (see [Releases](../../releases)).

---

### 1 — Web (browser)

```bash
git clone <repo-url> agent-readout
cd agent-readout
python3 -m agent_readout
# → http://127.0.0.1:7840
```

Or: `python3 web/run.py`

**Requires:** Python 3.10+ (stdlib only).

---

### 2 — Desktop WebView

Native window around the same UI ([pywebview](https://pywebview.flowrl.com/)).

```bash
pip install -r desktop/webview/requirements.txt
python3 desktop/webview/app.py
```

**Requires:** Python 3.10+, pywebview (uses Cocoa/GTK/Edge WebView2 by platform).

---

### 3 — Desktop Electron

Packaged shell that starts the local Python dashboard and shows it in an Electron window.

```bash
cd desktop/electron
npm install
npm start
```

Build installers:

```bash
npm run dist:mac    # .dmg / .zip
npm run dist:win    # .exe
npm run dist:linux  # AppImage
```

**Requires:** Node 20+, Python 3 on `PATH` (scanner still runs in Python).

---

## What it scans

| Agent | Path |
|-------|------|
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**`, `archived_sessions/**` |
| pi | `~/.pi/agent/sessions/**/*.jsonl` |

## Pricing

```bash
# optional — refresh catalog from a local pi install
pi update --models   # if you use pi
python3 scripts/sync_pricing.py
```

Unknown models fall back to family heuristics (opus/sonnet/fable/grok/…).  
Costs are **API list-price estimates**; subscriptions may bill differently.

## Repo layout

```
agent_readout/          # shared Python core (scanner + HTTP API)
web/static/             # UI
web/run.py              # web entry
desktop/webview/        # pywebview app
desktop/electron/       # Electron shell + electron-builder
data/pricing.json       # model rates
scripts/sync_pricing.py
.github/workflows/release.yml
```

## Release

```bash
git tag v0.1.0
git push origin v0.1.0
```

CI builds web zip, macOS WebView app, and Electron artifacts for macOS / Windows / Linux and attaches them to the GitHub Release.

## Privacy

- Binds to `127.0.0.1` only  
- No accounts, no upload  
- Session files never leave the machine that runs the app  

## License

MIT
