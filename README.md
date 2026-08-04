# Agent Usage

Local dashboard for **Claude Code**, **Codex**, and **pi** session usage.

Runs on your machine, reads your session logs, estimates API spend. Nothing is uploaded.

---

## Quick start

### Web (browser)

```bash
git clone git@github.com:akhilesh-w/agent-usage.git
cd agent-usage
python3 -m agent_usage
```

Opens [http://127.0.0.1:7840](http://127.0.0.1:7840).

Requires Python 3.9+.

### macOS app (Raycast / Dock / Spotlight)

```bash
./scripts/build-macos-app.sh
open "dist/Agent Usage.app"
```

Optional:

```bash
cp -R "dist/Agent Usage.app" /Applications/
```

Then open from Raycast (**Open App**), Spotlight, or the Dock.

Requires Python 3 on the Mac (the app uses it for scanning) and macOS 13+.

---

## Features

| View | What it shows |
|------|----------------|
| **Overview** | Totals, activity charts, cost by model, recent sessions |
| **Sessions** | Searchable Claude / Codex / pi history |
| **Costs** | Per-model breakdown (today / week / month / all time) |

Friendly model names (`Opus 4.8`, `Fable 5`, `Grok 4.5`, …) and a refreshable pricing catalog.

---

## Data sources

| Agent | Path | Notes |
|-------|------|--------|
| Claude Code | `~/.claude/projects/**/*.jsonl` | Full token/cost usage |
| Codex | `~/.codex/sessions/**`, `archived_sessions/**` | Full token/cost usage |
| pi | `~/.pi/agent/sessions/**/*.jsonl` | Full token/cost usage |
| OpenCode | `~/.local/share/opencode/opencode.db` | SQLite; tokens + cost |
| Conductor | `~/Library/Application Support/com.conductor.app/conductor.db` | Sessions list (little/no usage data) |

Sources are auto-detected if present. Toggle them in the sidebar.

---

## Pricing

Rates live in `data/pricing.json` (USD per 1M tokens).

```bash
pi update --models                 # optional, if you use pi
python3 scripts/sync_pricing.py    # refresh catalog
```

Unknown models fall back to family heuristics (opus / sonnet / fable / grok / gpt-5, …).

Costs are **API list-price estimates**. Subscriptions may bill differently.

---

## Layout

```text
agent_usage/           Python core (scanner + local HTTP API)
web/static/            Dashboard UI
desktop/macos/         Swift host (WKWebView shell today)
scripts/               build-macos-app.sh, sync_pricing.py
data/pricing.json
```

---

## Dev

```bash
python3 -m agent_usage
./scripts/build-macos-app.sh
```

---

## Privacy

- Binds to `127.0.0.1` only  
- No accounts or telemetry  
- Session files stay on your machine  

---

## License

MIT
