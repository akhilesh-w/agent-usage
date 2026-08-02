# Agent Readout

Local dashboard for **Claude Code**, **Codex**, and **pi** usage. Runs on **your machine** only.

## Two ways to run

| Target | What | How |
|--------|------|-----|
| **Web** | Browser UI + local server | `python3 -m agent_readout` |
| **macOS app** | Swift + WKWebView window (Raycast / Dock / Spotlight) | Build once, then open the `.app` |

There is **no** cloud multi-user mode. Each person runs it locally against their own session logs.

---

### Web

```bash
cd /path/to/agent-readout
python3 -m agent_readout
# → http://127.0.0.1:7840
```

Requires Python 3.10+ (3.9 often works).

---

### macOS app (Swift)

Double-clickable app. Same UI in a native window. Suitable for **Raycast** (“Open App”), Dock, Spotlight.

**Build** (Command Line Tools is enough; full Xcode optional):

```bash
./scripts/build-macos-app.sh
open "dist/Agent Readout.app"
```

Optional install:

```bash
cp -R "dist/Agent Readout.app" /Applications/
```

**Raycast:** Add Command → Application → choose **Agent Readout**.

The app starts a local Python scanner (needs `python3` on the machine) and shows the UI in **WKWebView**. Quit the app to stop the server.

---

## What it scans

| Agent | Path |
|-------|------|
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**`, `archived_sessions/**` |
| pi | `~/.pi/agent/sessions/**/*.jsonl` |

## Pricing

```bash
python3 scripts/sync_pricing.py   # optional refresh from local pi catalogs
```

## Layout

```
agent_readout/          # shared Python core
web/static/             # UI
desktop/macos/          # Swift sources + Info.plist
scripts/build-macos-app.sh
data/pricing.json
```

## Privacy

Binds to `127.0.0.1` only. No accounts, no upload.

## License

MIT
