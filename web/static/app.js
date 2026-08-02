let REPORT = null;
let costPeriod = "all";
const agentFilter = new Set(["claude", "codex", "pi"]);

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const MODEL_COLORS = [
  "#e5b567", // gold
  "#5b9fd4", // blue
  "#5fbf7f", // green
  "#6e6e73", // gray
  "#bf5af2", // purple
  "#ff9f0a", // orange
  "#ff6b6b",
  "#64d2ff",
];

function money(n, compact = false) {
  if (n == null || Number.isNaN(n)) return "—";
  if (compact) {
    if (Math.abs(n) >= 100) return `$${n.toFixed(1)}`;
    if (Math.abs(n) >= 10) return `$${n.toFixed(1)}`;
  }
  if (Math.abs(n) >= 100) return `$${Math.round(n)}`;
  if (Math.abs(n) >= 10) return `$${n.toFixed(2)}`;
  if (Math.abs(n) >= 1) return `$${n.toFixed(2)}`;
  if (Math.abs(n) === 0) return "$0";
  if (Math.abs(n) >= 0.01) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(3)}`;
}

function tokens(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function relTime(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 45) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  const d = Math.floor(s / 86400);
  if (d === 1) return "1d ago";
  if (d < 14) return `${d}d ago`;
  return `${Math.floor(d / 7)}w ago`;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/** Friendly labels: "Opus 4.8", "Fable 5", "GPT-5.5" */
function friendlyModel(id) {
  if (!id) return "Unknown";
  let s = String(id);
  s = s.replace(/^.*\//, "");
  s = s.replace(/^claude-/i, "");
  s = s.replace(/-\d{8}$/i, "");
  s = s.replace(/\[.*?\]/g, "");
  // fable-5 → Fable 5
  s = s.replace(/^fable-?(\d+(?:\.\d+)?)/i, "Fable $1");
  s = s.replace(/^opus-?(\d+(?:\.\d+)?)/i, "Opus $1");
  s = s.replace(/^sonnet-?(\d+(?:\.\d+)?)/i, "Sonnet $1");
  s = s.replace(/^haiku-?(\d+(?:\.\d+)?)/i, "Haiku $1");
  s = s.replace(/^gpt-?/i, "GPT-");
  s = s.replace(/^grok-?/i, "Grok ");
  // collapse leftover dashes in versiony bits
  s = s.replace(/-/g, " ");
  // Title-case short tokens if still lowercase family
  if (/^(opus|sonnet|haiku|fable)\b/i.test(s) && s === s.toLowerCase()) {
    s = s.replace(/^\w/, (c) => c.toUpperCase());
  }
  // GPT already handled; trim
  s = s.replace(/\s+/g, " ").trim();
  // Opus 4 8 → Opus 4.8 if looks like that
  s = s.replace(/^(Opus|Sonnet|Haiku|Fable)\s+(\d+)\s+(\d+)$/i, "$1 $2.$3");
  return s;
}

function colorForModel(id, index) {
  const s = String(id).toLowerCase();
  if (s.includes("fable")) return "#e5b567";
  if (s.includes("opus")) return index === 0 || s.includes("4-8") || s.includes("4.8") ? "#e5b567" : "#5fbf7f";
  if (s.includes("sonnet")) return "#5fbf7f";
  if (s.includes("haiku")) return "#6e6e73";
  if (s.includes("gpt") || s.includes("codex")) return "#5b9fd4";
  if (s.includes("grok")) return "#bf5af2";
  return MODEL_COLORS[index % MODEL_COLORS.length];
}

function filteredSessions() {
  if (!REPORT) return [];
  return REPORT.sessions.filter((s) => agentFilter.has(s.agent));
}

function setView(name) {
  $$(".sb-item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${name}`)?.classList.remove("hidden");
}

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Late night";
  if (h < 12) return "Morning push";
  if (h < 17) return "Afternoon push";
  if (h < 21) return "Evening push";
  return "Night push";
}

function userName() {
  // best-effort from home path sessions / nothing sensitive
  try {
    const home = REPORT.sessions.find((s) => s.cwd && s.cwd.includes("/Users/"))?.cwd || "";
    const m = home.match(/\/Users\/([^/]+)/);
    if (m) {
      const raw = m[1];
      return raw.charAt(0).toUpperCase() + raw.slice(1);
    }
  } catch {}
  return "";
}

function projectCounts(sessions) {
  const map = new Map();
  for (const s of sessions) {
    const p = s.project || "unknown";
    map.set(p, (map.get(p) || 0) + 1);
  }
  return [...map.entries()].sort((a, b) => b[1] - a[1]);
}

function agentBreakdown(sessions) {
  const map = { claude: 0, codex: 0, pi: 0 };
  for (const s of sessions) {
    if (map[s.agent] != null) map[s.agent]++;
    else map[s.agent] = 1;
  }
  return map;
}

function modelEntries(models) {
  return Object.entries(models || {}).sort((a, b) => (b[1].cost || 0) - (a[1].cost || 0));
}

function totalTokens(t = {}) {
  return (t.input || 0) + (t.output || 0) + (t.cacheRead || 0) + (t.cacheWrite || 0);
}

function renderOverview() {
  const sessions = filteredSessions();
  const all = REPORT.periods.all;
  const today = REPORT.periods.today;
  const name = userName();
  $("#greeting").textContent = name ? `${greeting()}, ${name}` : greeting();

  const projects = projectCounts(REPORT.sessions);
  const agents = agentBreakdown(REPORT.sessions);
  const topModel = modelEntries(all.models)[0];
  const topName = topModel ? friendlyModel(topModel[0]) : "—";

  $("#summary").innerHTML = `You have <b>${projects.length}</b> projects with agent activity across
    <b>${agents.claude || 0}</b> Claude,
    <b>${agents.codex || 0}</b> Codex, and
    <b>${agents.pi || 0}</b> pi sessions.
    Top spend: <span class="hl">${escapeHtml(topName)}</span>.`;

  $("#stat-row").innerHTML = [
    [projects.length, "Projects", "dot-blue"],
    [REPORT.sessionCount, "Sessions", "dot-blue"],
    [Object.keys(all.models || {}).length, "Models", "dot-purple"],
    [money(all.cost || 0, true), "Est. Cost", "dot-gold"],
  ]
    .map(
      ([n, l, d]) => `<div class="stat">
      <div class="n">${n}</div>
      <div class="l"><span class="dot ${d}"></span>${l}</div>
    </div>`
    )
    .join("");

  // model bars
  const entries = modelEntries(all.models).slice(0, 6);
  const max = Math.max(...entries.map(([, v]) => v.cost || 0), 0.0001);
  $("#model-bars").innerHTML = entries.length
    ? entries
        .map(([id, v], i) => {
          const pct = Math.max(2, Math.round((100 * (v.cost || 0)) / max));
          const color = colorForModel(id, i);
          return `<div class="mbar" title="${escapeHtml(id)}">
          <div class="mbar-name">${escapeHtml(friendlyModel(id))}</div>
          <div class="mbar-track"><div class="mbar-fill" style="width:${pct}%;background:${color}"></div></div>
          <div class="mbar-cost">${money(v.cost || 0, true)}</div>
        </div>`;
        })
        .join("")
    : `<div class="empty">No model usage</div>`;
  $("#model-total").textContent = `Total: ${money(all.cost || 0, true)}`;

  // recent sessions
  $("#overview-sessions").innerHTML = sessions.slice(0, 6).length
    ? sessions
        .slice(0, 6)
        .map((s) => {
          const title = (s.message_preview || "Session").replace(/\s+/g, " ");
          return `<div class="rrow">
          <span class="spark">✶</span>
          <div class="title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
          <div class="proj">${escapeHtml(s.project || "—")}</div>
          <div class="when">${escapeHtml(relTime(s.updated_at || s.started_at))}</div>
        </div>`;
        })
        .join("")
    : `<div class="empty">No sessions</div>`;

  // insight
  const week = REPORT.periods.week.cost || 0;
  const tCost = today.cost || 0;
  $("#insight-card").innerHTML = `<span class="ico">💡</span>
    <div class="body"><b>Today</b> ≈ ${money(tCost)} API-equivalent ·
    <b>This week</b> ≈ ${money(week)} ·
    Catalog pricing (subscription billing may differ).</div>`;

  // project chips
  $("#project-chips").innerHTML = projects
    .slice(0, 12)
    .map(
      ([p, c]) => `<span class="chip"><span class="c-dot"></span>${escapeHtml(p)} <span class="count">${c}</span></span>`
    )
    .join("") || `<span class="chip">No projects</span>`;

  // bottom cards
  const byAgent = Object.entries(agents).sort((a, b) => b[1] - a[1]);
  const topModels = entries.slice(0, 4);
  $("#bottom-cards").innerHTML = `
    <div class="mini-card">
      <h3>Top models <span>›</span></h3>
      <ul>${topModels
        .map(
          ([id, v]) =>
            `<li><span>${escapeHtml(friendlyModel(id))}</span><span>${money(v.cost || 0, true)}</span></li>`
        )
        .join("") || "<li>None</li>"}</ul>
    </div>
    <div class="mini-card">
      <h3>Agents <span>›</span></h3>
      <ul>${byAgent
        .map(
          ([a, n]) =>
            `<li><span class="agent-pill"><i style="background:${
              a === "claude" ? "#e0b48a" : a === "codex" ? "#7dd3fc" : "#d8a0ff"
            }"></i>${escapeHtml(a)}</span><span>${n}</span></li>`
        )
        .join("")}</ul>
    </div>
    <div class="mini-card">
      <h3>This week <span>›</span></h3>
      <ul>
        <li><span>Cost</span><span>${money(week, true)}</span></li>
        <li><span>Tokens</span><span>${tokens(totalTokens(REPORT.periods.week.tokens))}</span></li>
        <li><span>Models</span><span>${Object.keys(REPORT.periods.week.models || {}).length}</span></li>
      </ul>
    </div>
    <div class="mini-card">
      <h3>Projects <span>›</span></h3>
      <ul>${projects
        .slice(0, 4)
        .map(([p, c]) => `<li><span>${escapeHtml(p)}</span><span>${c}</span></li>`)
        .join("")}</ul>
      ${projects.length > 4 ? `<div class="more">+${projects.length - 4} more</div>` : ""}
    </div>`;

  drawActivityChart();
  drawCostChart();
}

function drawBarChart(canvas, values, color = "#5b9fd4", { round = true } = {}) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 400;
  const cssH = 110;
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const pad = { t: 8, b: 16, l: 2, r: 2 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  const max = Math.max(...values, 0.0001);
  const n = values.length;
  const gap = 2;
  const barW = Math.max(2, w / n - gap);

  // baseline
  ctx.strokeStyle = "rgba(255,255,255,0.06)";
  ctx.beginPath();
  ctx.moveTo(0, pad.t + h);
  ctx.lineTo(cssW, pad.t + h);
  ctx.stroke();

  values.forEach((v, i) => {
    const bh = (h * v) / max;
    const x = pad.l + i * (barW + gap);
    const y = pad.t + h - bh;
    ctx.fillStyle = v > 0 ? color : "rgba(255,255,255,0.04)";
    const rr = round ? Math.min(3, barW / 2) : 0;
    rounded(ctx, x, y, barW, Math.max(bh, v > 0 ? 1.5 : 0), rr);
    ctx.fill();
  });
}

function rounded(ctx, x, y, w, h, r) {
  if (h <= 0) return;
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, 0);
  ctx.arcTo(x, y + h, x, y, 0);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawActivityChart() {
  // session count per day from sessions list (approx)
  const days = REPORT.daily || [];
  // use token volume as activity proxy
  const vals = days.map((d) => totalTokens(d.tokens));
  drawBarChart($("#chart-activity"), vals, "#5b9fd4");
}

function drawCostChart() {
  const days = REPORT.daily || [];
  const vals = days.map((d) => d.cost || 0);
  drawBarChart($("#chart-cost"), vals, "#e5b567");
}

function renderSessions() {
  const q = ($("#session-q")?.value || "").toLowerCase().trim();
  let sessions = filteredSessions();
  if (q) {
    sessions = sessions.filter((s) => {
      const hay = [s.project, s.cwd, s.id, s.message_preview, ...(s.models || [])]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }
  $("#sessions-summary").textContent = `${sessions.length} sessions`;
  $("#sessions-list").innerHTML = sessions.length
    ? sessions
        .slice(0, 250)
        .map((s) => {
          return `<div class="srow">
          <div class="badge ${escapeHtml(s.agent)}">${escapeHtml(s.agent)}</div>
          <div>
            <div class="t">${escapeHtml(s.project || s.cwd || s.id.slice(0, 8))}</div>
            <div class="p">${escapeHtml(s.message_preview || "—")}</div>
            <div class="m">${escapeHtml((s.models || []).map(friendlyModel).join(", "))}</div>
          </div>
          <div class="right">
            <div class="c">${money(s.cost || 0)}</div>
            <div class="w">${escapeHtml(relTime(s.updated_at || s.started_at))}</div>
          </div>
          <div></div>
        </div>`;
        })
        .join("")
    : `<div class="empty">No matching sessions</div>`;
}

function renderCosts() {
  const data = REPORT.periods?.[costPeriod] || { cost: 0, tokens: {}, models: {} };
  const entries = modelEntries(data.models);
  const total = data.cost || 0.0001;

  $("#cost-stats").innerHTML = [
    [money(data.cost || 0, true), "Est. Cost", "dot-gold"],
    [tokens(totalTokens(data.tokens)), "Tokens", "dot-blue"],
    [entries.length, "Models", "dot-purple"],
    [money((data.cost || 0) / Math.max(entries.length, 1), true), "Avg / model", "dot-green"],
  ]
    .map(
      ([n, l, d]) => `<div class="stat"><div class="n">${n}</div><div class="l"><span class="dot ${d}"></span>${l}</div></div>`
    )
    .join("");

  $("#costs-body").innerHTML = entries.length
    ? entries
        .map(([id, v], i) => {
          const share = Math.round((100 * (v.cost || 0)) / total);
          const color = colorForModel(id, i);
          return `<tr>
          <td>
            <div style="display:flex;align-items:center;gap:8px">
              <span style="width:8px;height:8px;border-radius:50%;background:${color};flex:0 0 auto"></span>
              <span class="mono" title="${escapeHtml(id)}">${escapeHtml(friendlyModel(id))}</span>
            </div>
          </td>
          <td class="num">${money(v.cost || 0)}</td>
          <td class="num">${share}%</td>
          <td class="num">${tokens(v.input)}</td>
          <td class="num">${tokens(v.output)}</td>
          <td class="num">${tokens(v.cacheRead)}</td>
          <td class="num">${tokens(v.cacheWrite)}</td>
          <td><span class="pill ${escapeHtml(v.pricing || "")}">${escapeHtml(v.pricing || "?")}</span></td>
        </tr>`;
        })
        .join("")
    : `<tr><td colspan="8" class="empty">No usage in this period</td></tr>`;
}

function renderAll() {
  if (!REPORT) return;
  const when = new Date(REPORT.generatedAt);
  $("#meta").textContent = `${REPORT.sessionCount} sessions\n${REPORT.pricingModels} priced models\n${when.toLocaleTimeString()}`;
  renderOverview();
  renderSessions();
  renderCosts();
}

async function load(refresh = false) {
  const btn = $("#refresh");
  btn.disabled = true;
  btn.textContent = "Scanning…";
  try {
    const res = await fetch(`/api/report${refresh ? "?refresh=1" : ""}`);
    REPORT = await res.json();
    if (REPORT.error) throw new Error(REPORT.error);
    renderAll();
  } catch (e) {
    $("#meta").textContent = `Error: ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "↻ Refresh";
  }
}

function bind() {
  $$(".sb-item").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  $$("[data-goto]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.goto)));
  $("#refresh").addEventListener("click", () => load(true));
  $("#session-q")?.addEventListener("input", renderSessions);

  $$(".sb-check input").forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) agentFilter.add(input.dataset.agent);
      else agentFilter.delete(input.dataset.agent);
      renderOverview();
      renderSessions();
    });
  });

  $$("#cost-period button").forEach((btn) => {
    btn.addEventListener("click", () => {
      costPeriod = btn.dataset.period;
      $$("#cost-period button").forEach((b) => b.classList.toggle("on", b === btn));
      renderCosts();
    });
  });

  window.addEventListener("resize", () => {
    drawActivityChart();
    drawCostChart();
  });
}

bind();
load(false);
