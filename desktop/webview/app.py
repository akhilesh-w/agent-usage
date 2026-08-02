#!/usr/bin/env python3
"""
Desktop target (WebView): native window around the local dashboard.

Requires: pip install pywebview
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import webview
    except ImportError:
        print("pywebview is required for the desktop webview app.", file=sys.stderr)
        print("  pip install -r desktop/webview/requirements.txt", file=sys.stderr)
        return 1

    from agent_readout.server_app import DashboardServer

    server = DashboardServer()
    url = server.start(background=True)
    print(f"Agent Readout (webview) → {url}")

    window = webview.create_window(
        title="Agent Readout",
        url=url,
        width=1280,
        height=860,
        min_size=(900, 600),
        background_color="#1a1a1c",
    )
    try:
        webview.start()
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
