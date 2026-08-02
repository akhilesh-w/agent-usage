#!/usr/bin/env python3
"""Web target: local server + system browser."""

from agent_readout.server_app import run_browser

if __name__ == "__main__":
    run_browser(open_browser=True)
