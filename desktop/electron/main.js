const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");
const fs = require("fs");

const HOST = "127.0.0.1";
const PORT = 7840;

let mainWindow = null;
let pyProc = null;
let quitting = false;

function resourcesRoot() {
  // Packaged: extraResources land in process.resourcesPath
  // Dev: repo root is ../.. from desktop/electron
  if (app.isPackaged) return process.resourcesPath;
  return path.resolve(__dirname, "..", "..");
}

function pythonBin() {
  return process.env.AGENT_READOUT_PYTHON || "python3";
}

function waitForServer(url, tries = 60, delayMs = 250) {
  return new Promise((resolve, reject) => {
    let left = tries;
    const tick = () => {
      const req = http.get(url, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => {
        left -= 1;
        if (left <= 0) reject(new Error("Dashboard server did not start"));
        else setTimeout(tick, delayMs);
      });
    };
    tick();
  });
}

function startPython() {
  const root = resourcesRoot();
  const env = {
    ...process.env,
    PYTHONPATH: [root, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    AGENT_READOUT_ROOT: root,
  };

  // Ensure data/static resolve: run with cwd = resources root
  const args = ["-m", "agent_readout"];
  // Don't open system browser — Electron is the shell
  // Override via env consumed if we add it; for now server opens browser —
  // use a small runner script inline.
  const code = `
import os, sys
sys.path.insert(0, os.environ.get("AGENT_READOUT_ROOT", "."))
from agent_readout.server_app import run_browser
run_browser(open_browser=False, port=${PORT})
`;
  pyProc = spawn(pythonBin(), ["-c", code], {
    cwd: root,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });

  pyProc.stdout.on("data", (d) => process.stdout.write(`[py] ${d}`));
  pyProc.stderr.on("data", (d) => process.stderr.write(`[py] ${d}`));
  pyProc.on("exit", (code) => {
    pyProc = null;
    if (!quitting && code && code !== 0) {
      console.error("Python server exited", code);
    }
  });
}

function stopPython() {
  if (!pyProc) return;
  try {
    pyProc.kill("SIGTERM");
  } catch (_) {}
  pyProc = null;
}

async function createWindow() {
  startPython();
  const url = `http://${HOST}:${PORT}`;
  await waitForServer(url);

  mainWindow = new BrowserWindow({
    width: 1320,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: "#1a1a1c",
    title: "Agent Readout",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadURL(url);
  mainWindow.webContents.setWindowOpenHandler(({ url: target }) => {
    shell.openExternal(target);
    return { action: "deny" };
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  quitting = true;
  stopPython();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  quitting = true;
  stopPython();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
