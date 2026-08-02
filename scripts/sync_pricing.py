#!/usr/bin/env python3
"""Refresh data/pricing.json from a local pi-ai model catalog, if present."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "pricing.json"


def find_data_dirs() -> list[Path]:
    dirs: list[Path] = []
    candidates = [
        Path(
            "/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/"
            "node_modules/@earendil-works/pi-ai/dist/providers/data"
        ),
        Path.home()
        / ".npm-global/lib/node_modules/@earendil-works/pi-coding-agent/"
        / "node_modules/@earendil-works/pi-ai/dist/providers/data",
    ]
    nvm = Path.home() / ".nvm/versions/node"
    if nvm.is_dir():
        candidates.extend(
            nvm.glob(
                "*/lib/node_modules/@earendil-works/pi-coding-agent/"
                "node_modules/@earendil-works/pi-ai/dist/providers/data"
            )
        )
    seen: set[str] = set()
    for c in candidates:
        if c.is_dir():
            key = str(c.resolve())
            if key not in seen:
                seen.add(key)
                dirs.append(c)
    return dirs


def load_catalog(data_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    pref = {"anthropic", "openai", "xai", "openai-codex", "opencode"}
    for path in sorted(data_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        for _api, models in raw.items():
            if not isinstance(models, dict):
                continue
            for mid, m in models.items():
                if not isinstance(m, dict):
                    continue
                cost = m.get("cost") if isinstance(m.get("cost"), dict) else {}
                entry = {
                    "provider": m.get("provider") or path.stem,
                    "name": m.get("name") or mid,
                    "input": float(cost.get("input") or 0),
                    "output": float(cost.get("output") or 0),
                    "cacheRead": float(cost.get("cacheRead") or 0),
                    "cacheWrite": float(cost.get("cacheWrite") or 0),
                }
                if mid not in out:
                    out[mid] = entry
                elif out[mid].get("provider") not in pref and entry.get("provider") in pref:
                    out[mid] = entry
                if "/" in mid:
                    bare = mid.rsplit("/", 1)[-1]
                    out.setdefault(bare, entry)
    return out


def main() -> int:
    dirs = find_data_dirs()
    if not dirs:
        print("No pi-ai provider data dir found.", file=sys.stderr)
        return 1
    merged: dict[str, dict] = {}
    for d in dirs:
        cat = load_catalog(d)
        print(f"loaded {len(cat)} entries from {d}")
        for k, v in cat.items():
            if k not in merged:
                merged[k] = v
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(merged)} models → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
