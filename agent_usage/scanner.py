#!/usr/bin/env python3
"""Scan local agent session logs for token usage.

Supported sources:
  - Claude Code (~/.claude/projects JSONL)
  - Codex (~/.codex sessions JSONL)
  - pi (~/.pi/agent/sessions JSONL)
  - OpenCode (~/.local/share/opencode/opencode.db)
  - Conductor (macOS app SQLite — sessions only; little/no token usage)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

HOME = Path.home()
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
# Prefer repo data/, fall back to package-local copy (frozen builds)
def _pricing_path() -> Path:
    cands = [
        REPO_ROOT / "data" / "pricing.json",
        PACKAGE_DIR / "data" / "pricing.json",
        Path.cwd() / "data" / "pricing.json",
    ]
    env_root = os.environ.get("AGENT_READOUT_ROOT")
    if env_root:
        cands.insert(0, Path(env_root) / "data" / "pricing.json")
    return next((p for p in cands if p.exists()), cands[0])


PRICING_PATH = _pricing_path()

# Fallback rates ($ / 1M tokens) when catalog miss — family heuristics
FAMILY_RATES = [
    (re.compile(r"fable", re.I), {"input": 10, "output": 50, "cacheRead": 1, "cacheWrite": 12.5}),
    (re.compile(r"opus", re.I), {"input": 5, "output": 25, "cacheRead": 0.5, "cacheWrite": 6.25}),
    (re.compile(r"sonnet-5|sonnet_5|sonnet5", re.I), {"input": 2, "output": 10, "cacheRead": 0.2, "cacheWrite": 2.5}),
    (re.compile(r"sonnet", re.I), {"input": 3, "output": 15, "cacheRead": 0.3, "cacheWrite": 3.75}),
    (re.compile(r"haiku", re.I), {"input": 1, "output": 5, "cacheRead": 0.1, "cacheWrite": 1.25}),
    (re.compile(r"grok-4\.5|grok-4-5", re.I), {"input": 2, "output": 6, "cacheRead": 0.3, "cacheWrite": 0}),
    (re.compile(r"grok", re.I), {"input": 1.25, "output": 2.5, "cacheRead": 0.2, "cacheWrite": 0}),
    (re.compile(r"gpt-5\.6-sol|gpt-5\.5", re.I), {"input": 5, "output": 30, "cacheRead": 0.5, "cacheWrite": 0}),
    (re.compile(r"gpt-5\.6-terra|gpt-5\.4(?!-mini|-nano)", re.I), {"input": 2.5, "output": 15, "cacheRead": 0.25, "cacheWrite": 0}),
    (re.compile(r"gpt-5\.4-mini|gpt-5-mini", re.I), {"input": 0.75, "output": 4.5, "cacheRead": 0.075, "cacheWrite": 0}),
    (re.compile(r"gpt-5", re.I), {"input": 1.75, "output": 14, "cacheRead": 0.175, "cacheWrite": 0}),
]


@dataclass
class TokenBucket:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def add(self, other: "TokenBucket") -> None:
        self.input += other.input
        self.output += other.output
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write

    def cost(self, rates: dict[str, float]) -> float:
        # rates are $ per 1M tokens
        return (
            self.input * rates.get("input", 0)
            + self.output * rates.get("output", 0)
            + self.cache_read * rates.get("cacheRead", 0)
            + self.cache_write * rates.get("cacheWrite", 0)
        ) / 1_000_000.0


@dataclass
class SessionSummary:
    id: str
    agent: str  # claude | codex | pi
    cwd: str | None
    project: str | None
    started_at: str | None
    updated_at: str | None
    models: list[str] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=dict)
    cost: float = 0.0
    message_preview: str | None = None
    path: str = ""


class Pricing:
    def __init__(self, path: Path | None = None) -> None:
        path = path or _pricing_path()
        self.by_id: dict[str, dict[str, float]] = {}
        self.meta: dict[str, dict[str, Any]] = {}
        self._resolve_cache: dict[str, tuple[dict[str, float], str]] = {}
        if path.exists():
            raw = json.loads(path.read_text())
            for mid, entry in raw.items():
                if not isinstance(entry, dict):
                    continue
                rates = {
                    "input": float(entry.get("input") or 0),
                    "output": float(entry.get("output") or 0),
                    "cacheRead": float(entry.get("cacheRead") or 0),
                    "cacheWrite": float(entry.get("cacheWrite") or 0),
                }
                self.by_id[mid.lower()] = rates
                self.meta[mid.lower()] = entry

    def rates_for(self, model: str | None) -> tuple[dict[str, float], str]:
        if not model or model in ("None", "<synthetic>"):
            return {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}, "unknown"
        cached = self._resolve_cache.get(model)
        if cached is not None:
            return cached
        key = model.lower().strip()
        key = re.sub(r"\[.*?\]", "", key).strip()
        result: tuple[dict[str, float], str]
        if key in self.by_id:
            result = (self.by_id[key], "catalog")
        else:
            bare = key.rsplit("/", 1)[-1] if "/" in key else key
            if bare in self.by_id:
                result = (self.by_id[bare], "catalog")
            else:
                candidates = [k for k in self.by_id if k in key or key in k]
                if candidates:
                    best = max(candidates, key=len)
                    result = (self.by_id[best], "fuzzy")
                else:
                    matched = None
                    for rx, rates in FAMILY_RATES:
                        if rx.search(key):
                            matched = (dict(rates), "family")
                            break
                    result = matched or (
                        {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "missing",
                    )
        self._resolve_cache[model] = result
        return result


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # ms or s
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(value, str):
        s = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


def _day_key(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone().date().isoformat()


def _project_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    p = Path(cwd.rstrip("/"))
    return p.name or str(p)


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _bucket_from_claude_usage(usage: dict[str, Any]) -> TokenBucket:
    return TokenBucket(
        input=int(usage.get("input_tokens") or usage.get("input") or 0),
        output=int(usage.get("output_tokens") or usage.get("output") or 0),
        cache_read=int(
            usage.get("cache_read_input_tokens")
            or usage.get("cacheRead")
            or usage.get("cache_read")
            or 0
        ),
        cache_write=int(
            usage.get("cache_creation_input_tokens")
            or usage.get("cacheWrite")
            or usage.get("cache_write")
            or 0
        ),
    )


def _bucket_from_codex_last(u: dict[str, Any]) -> TokenBucket:
    # OpenAI-style: input_tokens often includes cached; billable input ~= input - cached
    inp = int(u.get("input_tokens") or 0)
    cached = int(u.get("cached_input_tokens") or 0)
    cache_write = int(u.get("cache_write_input_tokens") or 0)
    out = int(u.get("output_tokens") or 0)
    non_cached = max(inp - cached, 0)
    return TokenBucket(input=non_cached, output=out, cache_read=cached, cache_write=cache_write)


def _bucket_from_pi_usage(usage: dict[str, Any]) -> TokenBucket:
    return TokenBucket(
        input=int(usage.get("input") or 0),
        output=int(usage.get("output") or 0),
        cache_read=int(usage.get("cacheRead") or 0),
        cache_write=int(usage.get("cacheWrite") or 0),
    )


def scan_claude_file(
    path: Path, pricing: Pricing
) -> tuple[SessionSummary | None, dict[str, dict[str, TokenBucket]]]:
    """Single pass: session summary + day→model buckets."""
    session_id = path.stem
    cwd = None
    started = None
    updated = None
    models: dict[str, TokenBucket] = defaultdict(TokenBucket)
    daily: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
    preview = None
    cost = 0.0

    for obj in _iter_jsonl(path):
        t = obj.get("type")
        ts = _parse_ts(obj.get("timestamp"))
        if ts:
            started = started or ts
            updated = ts if not updated or ts > updated else updated
        if obj.get("cwd") and not cwd:
            cwd = obj.get("cwd")
        if obj.get("sessionId"):
            session_id = str(obj.get("sessionId"))

        if t == "user" and preview is None:
            msg = obj.get("message")
            text = None
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(str(c.get("text") or ""))
                        elif isinstance(c, str):
                            parts.append(c)
                    text = " ".join(parts)
            elif isinstance(msg, str):
                text = msg
            if text:
                preview = text.strip().replace("\n", " ")[:160]

        if t == "assistant":
            msg = obj.get("message") or {}
            if not isinstance(msg, dict):
                continue
            model = str(msg.get("model") or "unknown")
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            b = _bucket_from_claude_usage(usage)
            models[model].add(b)
            rates, _ = pricing.rates_for(model)
            cost += b.cost(rates)
            day = _day_key(ts)
            if day:
                daily[day][model].add(b)

    if not models and not started:
        return None, daily

    totals = TokenBucket()
    for b in models.values():
        totals.add(b)

    summary = SessionSummary(
        id=session_id,
        agent="claude",
        cwd=cwd,
        project=_project_from_cwd(cwd) or path.parent.name,
        started_at=started.isoformat() if started else None,
        updated_at=updated.isoformat() if updated else None,
        models=sorted(models.keys()),
        tokens={
            "input": totals.input,
            "output": totals.output,
            "cacheRead": totals.cache_read,
            "cacheWrite": totals.cache_write,
        },
        cost=cost,
        message_preview=preview,
        path=str(path),
    )
    return summary, daily


def scan_codex_file(
    path: Path, pricing: Pricing
) -> tuple[SessionSummary | None, dict[str, dict[str, TokenBucket]]]:
    session_id = path.stem
    cwd = None
    started = None
    updated = None
    current_model = "unknown"
    models: dict[str, TokenBucket] = defaultdict(TokenBucket)
    daily: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
    cost = 0.0
    preview = None

    for obj in _iter_jsonl(path):
        ts = _parse_ts(obj.get("timestamp"))
        if ts:
            started = started or ts
            updated = ts if not updated or ts > updated else updated
        t = obj.get("type")
        pl = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}

        if t == "session_meta":
            session_id = str(pl.get("session_id") or pl.get("id") or session_id)
            cwd = pl.get("cwd") or cwd
            if pl.get("model"):
                current_model = str(pl.get("model"))

        if t == "turn_context" or pl.get("type") == "turn_context":
            if pl.get("model"):
                current_model = str(pl.get("model"))
            cwd = pl.get("cwd") or cwd

        if preview is None and t == "response_item":
            if pl.get("type") == "message" and pl.get("role") == "user":
                content = pl.get("content")
                if isinstance(content, list):
                    texts = [
                        str(c.get("text") or "")
                        for c in content
                        if isinstance(c, dict) and c.get("type") in ("input_text", "text")
                    ]
                    joined = " ".join(texts).strip()
                    if joined:
                        preview = joined[:160]

        if t == "event_msg" and pl.get("type") == "token_count":
            info = pl.get("info") or {}
            last = info.get("last_token_usage") or {}
            if not isinstance(last, dict) or not last:
                continue
            model = str(info.get("model") or current_model or "unknown")
            b = _bucket_from_codex_last(last)
            models[model].add(b)
            rates, _ = pricing.rates_for(model)
            cost += b.cost(rates)
            day = _day_key(ts)
            if day:
                daily[day][model].add(b)

    if not models and not started:
        return None, daily

    totals = TokenBucket()
    for b in models.values():
        totals.add(b)

    summary = SessionSummary(
        id=session_id,
        agent="codex",
        cwd=cwd,
        project=_project_from_cwd(cwd),
        started_at=started.isoformat() if started else None,
        updated_at=updated.isoformat() if updated else None,
        models=sorted(models.keys()),
        tokens={
            "input": totals.input,
            "output": totals.output,
            "cacheRead": totals.cache_read,
            "cacheWrite": totals.cache_write,
        },
        cost=cost,
        message_preview=preview,
        path=str(path),
    )
    return summary, daily


def scan_pi_file(
    path: Path, pricing: Pricing
) -> tuple[SessionSummary | None, dict[str, dict[str, TokenBucket]]]:
    session_id = path.stem
    cwd = None
    started = None
    updated = None
    provider = None
    model_id = None
    models: dict[str, TokenBucket] = defaultdict(TokenBucket)
    daily: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
    cost = 0.0
    preview = None

    for obj in _iter_jsonl(path):
        t = obj.get("type")
        ts = _parse_ts(obj.get("timestamp"))
        if ts:
            started = started or ts
            updated = ts if not updated or ts > updated else updated

        if t == "session":
            session_id = str(obj.get("id") or session_id)
            cwd = obj.get("cwd") or cwd
        elif t == "model_change":
            provider = obj.get("provider") or provider
            model_id = obj.get("modelId") or model_id
        elif t == "message":
            msg = obj.get("message") or {}
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "user" and preview is None:
                content = msg.get("content")
                if isinstance(content, str):
                    preview = content.strip().replace("\n", " ")[:160]
                elif isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(str(c.get("text") or ""))
                    if parts:
                        preview = " ".join(parts).strip()[:160]
            usage = msg.get("usage")
            if role == "assistant" and isinstance(usage, dict):
                mid = str(msg.get("model") or model_id or "unknown")
                if (
                    provider
                    and "/" not in mid
                    and not mid.startswith("grok")
                    and not mid.startswith("gpt")
                    and not mid.startswith("claude")
                ):
                    label = f"{provider}/{mid}"
                else:
                    label = mid
                b = _bucket_from_pi_usage(usage)
                models[label].add(b)
                day = _day_key(ts)
                if day:
                    daily[day][label].add(b)
                embedded = usage.get("cost")
                if isinstance(embedded, dict) and embedded.get("total") is not None:
                    cost += float(embedded.get("total") or 0)
                else:
                    rates, _ = pricing.rates_for(mid)
                    cost += b.cost(rates)

    if not models and not started:
        return None, daily

    totals = TokenBucket()
    for b in models.values():
        totals.add(b)

    summary = SessionSummary(
        id=session_id,
        agent="pi",
        cwd=cwd,
        project=_project_from_cwd(cwd),
        started_at=started.isoformat() if started else None,
        updated_at=updated.isoformat() if updated else None,
        models=sorted(models.keys()),
        tokens={
            "input": totals.input,
            "output": totals.output,
            "cacheRead": totals.cache_read,
            "cacheWrite": totals.cache_write,
        },
        cost=cost,
        message_preview=preview,
        path=str(path),
    )
    return summary, daily


def _claude_paths() -> list[Path]:
    root = HOME / ".claude" / "projects"
    if not root.is_dir():
        return []
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # skip subagents for top-level session list (still count? include for cost accuracy)
        for fn in filenames:
            if fn.endswith(".jsonl"):
                out.append(Path(dirpath) / fn)
    return out


def _codex_paths() -> list[Path]:
    out: list[Path] = []
    for base in (HOME / ".codex" / "sessions", HOME / ".codex" / "archived_sessions"):
        if not base.is_dir():
            continue
        out.extend(base.rglob("rollout-*.jsonl"))
    return out


def _pi_paths() -> list[Path]:
    root = HOME / ".pi" / "agent" / "sessions"
    if not root.is_dir():
        return []
    return list(root.rglob("*.jsonl"))


def _opencode_db_path() -> Path | None:
    candidates = [
        HOME / ".local" / "share" / "opencode" / "opencode.db",
        HOME / "Library" / "Application Support" / "opencode" / "opencode.db",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _conductor_db_path() -> Path | None:
    p = HOME / "Library" / "Application Support" / "com.conductor.app" / "conductor.db"
    return p if p.is_file() else None


def _ms_to_dt(ms: Any) -> datetime | None:
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return None
    if v > 1e12:
        v /= 1000.0
    try:
        return datetime.fromtimestamp(v, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_opencode_model(raw: Any) -> str:
    if raw is None:
        return "unknown"
    if isinstance(raw, dict):
        mid = raw.get("id") or raw.get("modelID") or raw.get("modelId")
        prov = raw.get("providerID") or raw.get("providerId") or raw.get("provider")
        if mid and prov:
            return f"{prov}/{mid}"
        if mid:
            return str(mid)
        return json.dumps(raw)[:80]
    s = str(raw).strip()
    if not s:
        return "unknown"
    if s.startswith("{"):
        try:
            return _parse_opencode_model(json.loads(s))
        except json.JSONDecodeError:
            pass
    return s


def scan_opencode(
    pricing: Pricing, db_path: Path | None = None
) -> tuple[list[SessionSummary], dict[str, dict[str, TokenBucket]]]:
    """Read OpenCode SQLite DB (session totals + step-finish parts for daily)."""
    path = db_path or _opencode_db_path()
    empty_daily: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
    if not path:
        return [], empty_daily

    sessions: list[SessionSummary] = []
    daily: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))

    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Prefer step-finish parts for usage (accurate). Fall back to session row totals.
        part_usage: dict[str, TokenBucket] = defaultdict(TokenBucket)
        part_cost: dict[str, float] = defaultdict(float)
        part_models: dict[str, set[str]] = defaultdict(set)

        try:
            rows = conn.execute(
                """
                SELECT session_id, time_created, data
                FROM part
                WHERE data LIKE '%step-finish%'
                """
            ).fetchall()
        except sqlite3.Error:
            rows = []

        for row in rows:
            try:
                data = json.loads(row["data"] or "{}")
            except json.JSONDecodeError:
                continue
            if data.get("type") != "step-finish":
                continue
            tokens = data.get("tokens") or {}
            if not isinstance(tokens, dict):
                continue
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            b = TokenBucket(
                input=int(tokens.get("input") or 0),
                output=int(tokens.get("output") or 0),
                cache_read=int(cache.get("read") or 0),
                cache_write=int(cache.get("write") or 0),
            )
            sid = str(row["session_id"])
            part_usage[sid].add(b)
            embedded = data.get("cost")
            if isinstance(embedded, (int, float)):
                part_cost[sid] += float(embedded)
            day = _day_key(_ms_to_dt(row["time_created"]))
            # model often lives on the session, not the part — fill later
            if day:
                # temporary key; re-key after we know session models
                daily[day][f"__sid__:{sid}"].add(b)

        try:
            sess_rows = conn.execute(
                """
                SELECT id, title, directory, model, cost,
                       tokens_input, tokens_output, tokens_cache_read, tokens_cache_write,
                       time_created, time_updated
                FROM session
                """
            ).fetchall()
        except sqlite3.Error:
            sess_rows = []

        session_model: dict[str, str] = {}
        for row in sess_rows:
            sid = str(row["id"])
            model = _parse_opencode_model(row["model"])
            if not model or model == "unknown":
                model = "opencode/unknown"
            session_model[sid] = model

            if sid in part_usage:
                totals = part_usage[sid]
                cost = part_cost[sid]
                if cost <= 0:
                    rates, _ = pricing.rates_for(model)
                    cost = totals.cost(rates)
            else:
                totals = TokenBucket(
                    input=int(row["tokens_input"] or 0),
                    output=int(row["tokens_output"] or 0),
                    cache_read=int(row["tokens_cache_read"] or 0),
                    cache_write=int(row["tokens_cache_write"] or 0),
                )
                cost = float(row["cost"] or 0)
                if cost <= 0 and (totals.input or totals.output or totals.cache_read or totals.cache_write):
                    rates, _ = pricing.rates_for(model)
                    cost = totals.cost(rates)
                # attribute session totals to created day if no parts
                day = _day_key(_ms_to_dt(row["time_created"]))
                if day and (totals.input or totals.output or totals.cache_read or totals.cache_write):
                    daily[day][model].add(totals)

            if not totals.input and not totals.output and not totals.cache_read and not cost:
                # still list empty sessions lightly
                pass

            started = _ms_to_dt(row["time_created"])
            updated = _ms_to_dt(row["time_updated"]) or started
            cwd = row["directory"] or None
            title = (row["title"] or "").strip() or None

            sessions.append(
                SessionSummary(
                    id=sid,
                    agent="opencode",
                    cwd=cwd,
                    project=_project_from_cwd(cwd),
                    started_at=started.isoformat() if started else None,
                    updated_at=updated.isoformat() if updated else None,
                    models=[model],
                    tokens={
                        "input": totals.input,
                        "output": totals.output,
                        "cacheRead": totals.cache_read,
                        "cacheWrite": totals.cache_write,
                    },
                    cost=cost,
                    message_preview=title,
                    path=str(path),
                )
            )

        # Re-key temporary daily sid buckets to real model names
        fixed: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
        for day, models in daily.items():
            for key, bucket in models.items():
                if key.startswith("__sid__:"):
                    sid = key.split(":", 1)[1]
                    model = session_model.get(sid, "opencode/unknown")
                    fixed[day][model].add(bucket)
                else:
                    fixed[day][key].add(bucket)
        daily = fixed
    finally:
        conn.close()

    return sessions, daily


def scan_conductor(
    pricing: Pricing, db_path: Path | None = None
) -> tuple[list[SessionSummary], dict[str, dict[str, TokenBucket]]]:
    """Conductor app sessions (macOS). Usage tokens are usually absent."""
    path = db_path or _conductor_db_path()
    empty_daily: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
    if not path:
        return [], empty_daily

    sessions: list[SessionSummary] = []
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                """
                SELECT s.id, s.title, s.model, s.agent_type, s.status,
                       s.context_token_count, s.created_at, s.updated_at,
                       w.directory_name, r.name AS repo_name, r.root_path
                FROM sessions s
                LEFT JOIN workspaces w ON s.workspace_id = w.id
                LEFT JOIN repos r ON w.repository_id = r.id
                """
            ).fetchall()
        except sqlite3.Error:
            rows = conn.execute(
                "SELECT id, title, model, agent_type, status, context_token_count, created_at, updated_at FROM sessions"
            ).fetchall()

        for row in rows:
            model_raw = row["model"] or "unknown"
            agent_type = (row["agent_type"] or "").strip().lower()
            # Conductor hosts Claude/Codex agents — label model helpfully
            if agent_type and model_raw and model_raw != "unknown":
                if not str(model_raw).startswith("claude") and agent_type == "claude":
                    model = f"claude/{model_raw}"
                elif agent_type == "codex" and not str(model_raw).startswith("gpt"):
                    model = f"codex/{model_raw}"
                else:
                    model = str(model_raw)
            else:
                model = str(model_raw)

            cwd = None
            project = None
            try:
                cwd = row["root_path"] or None
                project = row["repo_name"] or row["directory_name"] or _project_from_cwd(cwd)
            except (IndexError, KeyError):
                project = None

            started = _parse_ts(row["created_at"])
            updated = _parse_ts(row["updated_at"]) or started
            title = (row["title"] or "").strip()
            if title in ("", "Untitled"):
                title = f"Conductor · {agent_type or 'session'}"

            # No reliable per-token usage in DB today
            tokens = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
            ctx = int(row["context_token_count"] or 0)
            if ctx:
                tokens["input"] = ctx

            sessions.append(
                SessionSummary(
                    id=str(row["id"]),
                    agent="conductor",
                    cwd=cwd,
                    project=project,
                    started_at=started.isoformat() if started else None,
                    updated_at=updated.isoformat() if updated else None,
                    models=[model] if model and model != "unknown" else [],
                    tokens=tokens,
                    cost=0.0,
                    message_preview=title,
                    path=str(path),
                )
            )
    finally:
        conn.close()

    return sessions, empty_daily


def build_report(agents: Iterable[str] | None = None) -> dict[str, Any]:
    wanted = set(agents or ("claude", "codex", "pi", "opencode", "conductor"))
    pricing = Pricing()

    sessions: list[SessionSummary] = []
    # day -> model -> TokenBucket
    daily_models: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
    errors: list[str] = []

    def accumulate_daily(dmap: dict[str, dict[str, TokenBucket]]) -> None:
        for day, models in dmap.items():
            for model, bucket in models.items():
                daily_models[day][model].add(bucket)

    def scan_all(label: str, paths: list[Path], fn) -> None:
        for p in paths:
            try:
                summary, daily = fn(p, pricing)
                if summary:
                    sessions.append(summary)
                if daily:
                    accumulate_daily(daily)
            except Exception as e:
                errors.append(f"{label} {p}: {e}")

    if "claude" in wanted:
        scan_all("claude", _claude_paths(), scan_claude_file)
    if "codex" in wanted:
        scan_all("codex", _codex_paths(), scan_codex_file)
    if "pi" in wanted:
        scan_all("pi", _pi_paths(), scan_pi_file)
    if "opencode" in wanted:
        try:
            oc_sessions, oc_daily = scan_opencode(pricing)
            sessions.extend(oc_sessions)
            accumulate_daily(oc_daily)
        except Exception as e:
            errors.append(f"opencode: {e}")
    if "conductor" in wanted:
        try:
            c_sessions, c_daily = scan_conductor(pricing)
            sessions.extend(c_sessions)
            accumulate_daily(c_daily)
        except Exception as e:
            errors.append(f"conductor: {e}")

    sessions.sort(key=lambda s: s.updated_at or s.started_at or "", reverse=True)

    # Precompute each day's cost/tokens/models once (avoids re-walking 30+ times)
    day_summaries: dict[str, dict[str, Any]] = {}
    for day, models in daily_models.items():
        totals = TokenBucket()
        by_model: dict[str, dict[str, Any]] = {}
        cost = 0.0
        for model, b in models.items():
            totals.add(b)
            rates, src = pricing.rates_for(model)
            c = b.cost(rates)
            cost += c
            by_model[model] = {
                "input": b.input,
                "output": b.output,
                "cacheRead": b.cache_read,
                "cacheWrite": b.cache_write,
                "cost": c,
                "pricing": src,
                "rates": rates,
            }
        day_summaries[day] = {
            "cost": cost,
            "tokens": {
                "input": totals.input,
                "output": totals.output,
                "cacheRead": totals.cache_read,
                "cacheWrite": totals.cache_write,
            },
            "models": by_model,
        }

    def merge_days(days: set[str] | None = None) -> dict[str, Any]:
        totals = TokenBucket()
        by_model: dict[str, dict[str, Any]] = {}
        cost = 0.0
        for day, summary in day_summaries.items():
            if days is not None and day not in days:
                continue
            cost += summary["cost"]
            t = summary["tokens"]
            totals.input += t["input"]
            totals.output += t["output"]
            totals.cache_read += t["cacheRead"]
            totals.cache_write += t["cacheWrite"]
            for model, slot in summary["models"].items():
                acc = by_model.setdefault(
                    model,
                    {
                        "input": 0,
                        "output": 0,
                        "cacheRead": 0,
                        "cacheWrite": 0,
                        "cost": 0.0,
                        "pricing": slot["pricing"],
                        "rates": slot["rates"],
                    },
                )
                acc["input"] += slot["input"]
                acc["output"] += slot["output"]
                acc["cacheRead"] += slot["cacheRead"]
                acc["cacheWrite"] += slot["cacheWrite"]
                acc["cost"] += slot["cost"]
        return {
            "cost": cost,
            "tokens": {
                "input": totals.input,
                "output": totals.output,
                "cacheRead": totals.cache_read,
                "cacheWrite": totals.cache_write,
            },
            "models": dict(sorted(by_model.items(), key=lambda kv: kv[1]["cost"], reverse=True)),
        }

    today = datetime.now().astimezone().date()
    today_s = today.isoformat()
    week_days = {(today.fromordinal(today.toordinal() - i)).isoformat() for i in range(7)}
    month_prefix = today.strftime("%Y-%m")
    month_days = {d for d in day_summaries if d.startswith(month_prefix)}

    empty_day = {
        "cost": 0.0,
        "tokens": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
    }
    series = []
    for i in range(29, -1, -1):
        d = (today.fromordinal(today.toordinal() - i)).isoformat()
        s = day_summaries.get(d, empty_day)
        series.append({"date": d, "cost": s["cost"], "tokens": s["tokens"]})

    all_time = merge_days(None)

    return {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "pricingModels": len(pricing.by_id),
        "periods": {
            "today": merge_days({today_s}),
            "week": merge_days(week_days),
            "month": merge_days(month_days),
            "all": all_time,
        },
        "daily": series,
        "sessions": [asdict(s) for s in sessions[:200]],
        "sessionCount": len(sessions),
        "models": all_time["models"],
        "errors": errors[:20],
    }


if __name__ == "__main__":
    import pprint

    report = build_report()
    print(json.dumps({
        "generatedAt": report["generatedAt"],
        "pricingModels": report["pricingModels"],
        "sessionCount": report["sessionCount"],
        "today": report["periods"]["today"]["cost"],
        "week": report["periods"]["week"]["cost"],
        "month": report["periods"]["month"]["cost"],
        "all": report["periods"]["all"]["cost"],
        "topModels": list(report["models"].items())[:8],
    }, indent=2))
