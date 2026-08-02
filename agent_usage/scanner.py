#!/usr/bin/env python3
"""Scan local Claude Code, Codex, and pi session logs for token usage."""

from __future__ import annotations

import json
import os
import re
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
    def __init__(self, path: Path = PRICING_PATH) -> None:
        self.by_id: dict[str, dict[str, float]] = {}
        self.meta: dict[str, dict[str, Any]] = {}
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
        key = model.lower().strip()
        # strip suffixes like [1m]
        key = re.sub(r"\[.*?\]", "", key).strip()
        if key in self.by_id:
            return self.by_id[key], "catalog"
        # try bare after slash
        if "/" in key:
            bare = key.rsplit("/", 1)[-1]
            if bare in self.by_id:
                return self.by_id[bare], "catalog"
        # fuzzy contains match preferring longer ids
        candidates = [k for k in self.by_id if k in key or key in k]
        if candidates:
            best = max(candidates, key=len)
            return self.by_id[best], "fuzzy"
        for rx, rates in FAMILY_RATES:
            if rx.search(key):
                return dict(rates), "family"
        return {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}, "missing"


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


def scan_claude_file(path: Path, pricing: Pricing) -> SessionSummary | None:
    session_id = path.stem
    cwd = None
    started = None
    updated = None
    models: dict[str, TokenBucket] = defaultdict(TokenBucket)
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
            model = msg.get("model") or "unknown"
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            b = _bucket_from_claude_usage(usage)
            models[str(model)].add(b)
            rates, _ = pricing.rates_for(str(model))
            cost += b.cost(rates)

    if not models and not started:
        return None

    totals = TokenBucket()
    for b in models.values():
        totals.add(b)

    return SessionSummary(
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


def scan_claude_file_daily(path: Path, pricing: Pricing) -> dict[str, dict[str, TokenBucket]]:
    """day -> model -> tokens"""
    daily: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
    for obj in _iter_jsonl(path):
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") or {}
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        model = str(msg.get("model") or "unknown")
        day = _day_key(_parse_ts(obj.get("timestamp")))
        if not day:
            continue
        daily[day][model].add(_bucket_from_claude_usage(usage))
    return daily


def scan_codex_file(path: Path, pricing: Pricing) -> SessionSummary | None:
    session_id = path.stem
    cwd = None
    started = None
    updated = None
    current_model = "unknown"
    models: dict[str, TokenBucket] = defaultdict(TokenBucket)
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

        # user message preview
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

    if not models and not started:
        return None

    totals = TokenBucket()
    for b in models.values():
        totals.add(b)

    return SessionSummary(
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


def scan_codex_file_daily(path: Path, pricing: Pricing) -> dict[str, dict[str, TokenBucket]]:
    daily: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
    current_model = "unknown"
    for obj in _iter_jsonl(path):
        pl = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        t = obj.get("type")
        if t == "turn_context" or pl.get("type") == "turn_context":
            if pl.get("model"):
                current_model = str(pl.get("model"))
        if t == "session_meta" and pl.get("model"):
            current_model = str(pl.get("model"))
        if t == "event_msg" and pl.get("type") == "token_count":
            info = pl.get("info") or {}
            last = info.get("last_token_usage") or {}
            if not isinstance(last, dict) or not last:
                continue
            day = _day_key(_parse_ts(obj.get("timestamp")))
            if not day:
                continue
            model = str(info.get("model") or current_model or "unknown")
            daily[day][model].add(_bucket_from_codex_last(last))
    return daily


def scan_pi_file(path: Path, pricing: Pricing) -> SessionSummary | None:
    session_id = path.stem
    cwd = None
    started = None
    updated = None
    provider = None
    model_id = None
    models: dict[str, TokenBucket] = defaultdict(TokenBucket)
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
                # pi sometimes stores provider/model separately
                if provider and "/" not in mid and not mid.startswith("grok") and not mid.startswith("gpt") and not mid.startswith("claude"):
                    label = f"{provider}/{mid}"
                else:
                    label = mid
                b = _bucket_from_pi_usage(usage)
                models[label].add(b)
                # Prefer embedded cost if present (already priced by pi)
                embedded = usage.get("cost")
                if isinstance(embedded, dict) and embedded.get("total") is not None:
                    cost += float(embedded.get("total") or 0)
                else:
                    rates, _ = pricing.rates_for(mid)
                    cost += b.cost(rates)

    if not models and not started:
        return None

    totals = TokenBucket()
    for b in models.values():
        totals.add(b)

    return SessionSummary(
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


def scan_pi_file_daily(path: Path, pricing: Pricing) -> dict[str, dict[str, TokenBucket]]:
    daily: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
    model_id = "unknown"
    for obj in _iter_jsonl(path):
        if obj.get("type") == "model_change":
            model_id = str(obj.get("modelId") or model_id)
        if obj.get("type") != "message":
            continue
        msg = obj.get("message") or {}
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        day = _day_key(_parse_ts(obj.get("timestamp")))
        if not day:
            continue
        mid = str(msg.get("model") or model_id)
        daily[day][mid].add(_bucket_from_pi_usage(usage))
    return daily


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


def build_report(agents: Iterable[str] | None = None) -> dict[str, Any]:
    wanted = set(agents or ("claude", "codex", "pi"))
    pricing = Pricing()

    sessions: list[SessionSummary] = []
    # day -> model -> TokenBucket
    daily_models: dict[str, dict[str, TokenBucket]] = defaultdict(lambda: defaultdict(TokenBucket))
    # model totals
    model_totals: dict[str, TokenBucket] = defaultdict(TokenBucket)
    model_cost: dict[str, float] = defaultdict(float)
    model_source: dict[str, str] = {}

    def accumulate_daily(dmap: dict[str, dict[str, TokenBucket]]) -> None:
        for day, models in dmap.items():
            for model, bucket in models.items():
                daily_models[day][model].add(bucket)
                model_totals[model].add(bucket)
                rates, src = pricing.rates_for(model)
                model_cost[model] += bucket.cost(rates)
                model_source[model] = src

    errors: list[str] = []

    if "claude" in wanted:
        for p in _claude_paths():
            try:
                # skip empty-ish
                s = scan_claude_file(p, pricing)
                if s:
                    sessions.append(s)
                accumulate_daily(scan_claude_file_daily(p, pricing))
            except Exception as e:
                errors.append(f"claude {p}: {e}")

    if "codex" in wanted:
        for p in _codex_paths():
            try:
                s = scan_codex_file(p, pricing)
                if s:
                    sessions.append(s)
                accumulate_daily(scan_codex_file_daily(p, pricing))
            except Exception as e:
                errors.append(f"codex {p}: {e}")

    if "pi" in wanted:
        for p in _pi_paths():
            try:
                s = scan_pi_file(p, pricing)
                if s:
                    sessions.append(s)
                accumulate_daily(scan_pi_file_daily(p, pricing))
            except Exception as e:
                errors.append(f"pi {p}: {e}")

    # sort sessions
    sessions.sort(key=lambda s: s.updated_at or s.started_at or "", reverse=True)

    # rebuild session costs already done; compute period totals from daily
    def period_cost(days: set[str] | None = None) -> dict[str, Any]:
        totals = TokenBucket()
        by_model: dict[str, dict[str, Any]] = {}
        cost = 0.0
        for day, models in daily_models.items():
            if days is not None and day not in days:
                continue
            for model, b in models.items():
                totals.add(b)
                rates, src = pricing.rates_for(model)
                c = b.cost(rates)
                cost += c
                slot = by_model.setdefault(
                    model,
                    {
                        "input": 0,
                        "output": 0,
                        "cacheRead": 0,
                        "cacheWrite": 0,
                        "cost": 0.0,
                        "pricing": src,
                        "rates": rates,
                    },
                )
                slot["input"] += b.input
                slot["output"] += b.output
                slot["cacheRead"] += b.cache_read
                slot["cacheWrite"] += b.cache_write
                slot["cost"] += c
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
    # month: same calendar month
    month_prefix = today.strftime("%Y-%m")
    month_days = {d for d in daily_models if d.startswith(month_prefix)}

    # daily series (last 30 days)
    series = []
    for i in range(29, -1, -1):
        d = (today.fromordinal(today.toordinal() - i)).isoformat()
        p = period_cost({d})
        series.append({"date": d, "cost": p["cost"], "tokens": p["tokens"]})

    all_time = period_cost(None)

    return {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "pricingModels": len(pricing.by_id),
        "periods": {
            "today": period_cost({today_s}),
            "week": period_cost(week_days),
            "month": period_cost(month_days),
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
