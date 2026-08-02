# Agent Readout

A local dashboard for **Claude Code**, **Codex**, and **pi** session usage.

It runs on your machine, reads your local session logs, and never uploads anything. Costs are estimated from API list prices (subscriptions may bill differently).

---

## Quick start

### Option A — Web (browser)

```bash
git clone git@github.com:akhilesh-w/agent-readout.git
cd agent-readout
python3 -m agent_readout
```

Opens [http://127.0.0.1:7840](http://127.0.0.1:7840).

**Needs:** Python 3.9+

---

### Option B — macOS app (Raycast / Dock / Spotlight)

Build a normal `.app` once:

```bash
./scripts/build-macos-app.sh
open "dist/Agent Readout.app"
```

Optional install:

```bash
cp -R "dist/Agent Readout.app" /Applications/
```

Then open it like any other Mac app, or add it in **Raycast → Open App**.

**Needs:** Python 3 on the Mac (the app shells out to it for scanning), plus macOS 13+

---

## What you get

| View | Contents |
|------|----------|
| **Overview** | Spend totals, activity, cost-by-model, recent sessions |
| **Sessions** | Searchable list across Claude / Codex / pi |
| **Costs** | Per-model breakdown for today / week / month / all time |

Model names are normalized (e.g. `Opus 4.8`, `Fable 5`, `Grok 4.5`). Pricing comes from a bundled catalog you can refresh (see below).

---

## What it reads

| Agent | Location |
|-------|----------|
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**`, `~/.codex/archived_sessions/**` |
| pi | `~/.pi/agent/sessions/**/*.jsonl` |

Only these local files. Nothing is sent to a server.

---

## Pricing catalog

Rates live in `data/pricing.json` (USD per 1M tokens).

Refresh from a local [pi](https://github.com/earendil-works/pi) install when models change:

```bash
pi update --models          # optional
python3 scripts/sync_pricing.py
```

If a brand-new model isn’t in the catalog yet, family fallbacks still estimate cost (opus / sonnet / fable / grok / gpt-5, …).

---

## Project layout

```text
agent_readout/     Python core (scanner + local HTTP API)
web/static/        Dashboard UI
desktop/macos/     Swift app (WKWebView shell)
scripts/           build-macos-app.sh, sync_pricing.py
data/pricing.json  Model rates
```

Both the web and macOS targets share the same core and UI.

---

## Development

```bash
# Web, with browser
python3 -m agent_readout

# Or without auto-open
python3 web/run.py

# Rebuild macOS app after UI/Swift changes
./scripts/build-macos-app.sh
```

---

## Privacy

- Listens on `127.0.0.1` only  
- No accounts, analytics, or network calls for usage data  
- Session JSONL never leaves your machine  

---

## License

MIT
