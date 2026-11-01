/*
 * SolarMax Onyx card — bundled with the SolarTouch LAN integration.
 *
 * Live summary tiles plus one interactive chart (solar, home, grid, battery
 * power and battery SOC) over a selectable time range. No external libraries.
 *
 *   type: custom:solarmax-onyx-card
 *   entry_id: <optional, defaults to the first inverter>
 *   title: <optional>
 *   theme: auto | dark | light        (default: auto = follow Home Assistant)
 *   range: 6h | 24h | 3d | 7d         (default: 24h)
 *   series: [solar, home, grid, battery, soc]   (initially visible)
 */

const CARD_VERSION = "1.0.0";
const DOMAIN = "solarmax_touch_lan";

const RANGES = {
  "6h": { hours: 6, period: "5minute" },
  "24h": { hours: 24, period: "5minute" },
  "3d": { hours: 72, period: "hour" },
  "7d": { hours: 168, period: "hour" },
};

// Chart series. "home" is derived: solar + grid import − export + battery discharge.
// Battery power sign convention: positive = discharging, negative = charging.
const SERIES = [
  { id: "solar", label: "Solar", key: "live_pv_total_power", axis: "w", color: "var(--smo-solar)" },
  { id: "home", label: "Home", axis: "w", color: "var(--smo-home)", derived: true },
  { id: "grid", label: "Grid", key: "grid_power", axis: "w", color: "var(--smo-grid)" },
  { id: "battery", label: "Battery", key: "battery_power", axis: "w", color: "var(--smo-battery)" },
  { id: "soc", label: "SOC", key: "battery_soc", axis: "pct", color: "var(--smo-soc)", dashed: true },
];

const STYLE = `
  :host {
    --smo-solar: #f5a524;
    --smo-home: #4f8cff;
    --smo-grid: #a78bfa;
    --smo-battery: #30a46c;
    --smo-soc: #2bb3c0;
    --smo-bad: #e5484d;
  }
  ha-card {
    overflow: hidden;
    background: var(--smo-bg);
    color: var(--smo-fg);
    font-family: var(--paper-font-body1_-_font-family, inherit);
  }
  .theme-auto {
    --smo-bg: var(--ha-card-background, var(--card-background-color, #fff));
    --smo-fg: var(--primary-text-color, #1c1c1e);
    --smo-muted: var(--secondary-text-color, #6b6b70);
    --smo-line: var(--divider-color, rgba(127,127,127,.2));
    --smo-tile: var(--secondary-background-color, rgba(127,127,127,.08));
  }
  .theme-dark {
    --smo-bg: #16181d;
    --smo-fg: #eceef2;
    --smo-muted: #9097a3;
    --smo-line: rgba(255,255,255,.09);
    --smo-tile: #1f2229;
  }
  .theme-light {
    --smo-bg: #ffffff;
    --smo-fg: #1c1d21;
    --smo-muted: #686d76;
    --smo-line: rgba(0,0,0,.08);
    --smo-tile: #f4f5f7;
  }
  .wrap { padding: 16px; }
  header { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
  header .title { font-size: 1.1rem; font-weight: 600; line-height: 1.2; }
  header .sub { font-size: .8rem; color: var(--smo-muted); }
  header .grow { flex: 1; min-width: 0; }
  .pill {
    font-size: .72rem; padding: 3px 9px; border-radius: 999px; white-space: nowrap;
    background: var(--smo-tile); color: var(--smo-muted); text-transform: capitalize;
  }
  .pill.error { color: var(--smo-bad); }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 8px; }
  .tile { background: var(--smo-tile); border-radius: 12px; padding: 10px 12px; min-width: 0; }
  .tile .label { font-size: .75rem; color: var(--smo-muted); display: flex; align-items: center; gap: 6px; }
  .tile .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
  .tile .value { font-size: 1.25rem; font-weight: 600; margin-top: 2px; font-variant-numeric: tabular-nums; }
  .tile .unit { font-size: .8rem; font-weight: 500; color: var(--smo-muted); margin-left: 2px; }
  .tile .note { font-size: .72rem; color: var(--smo-muted); margin-top: 1px; }
  .today { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 12px 2px 0; font-size: .8rem; color: var(--smo-muted); }
  .today b { color: var(--smo-fg); font-weight: 600; font-variant-numeric: tabular-nums; }
  .toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; margin: 16px 0 6px; }
  .chips, .ranges { display: flex; flex-wrap: wrap; gap: 6px; }
  button {
    font: inherit; font-size: .78rem; border: 1px solid var(--smo-line); background: transparent;
    color: var(--smo-fg); border-radius: 999px; padding: 4px 10px; cursor: pointer;
    display: inline-flex; align-items: center; gap: 6px;
  }
  button.off { color: var(--smo-muted); opacity: .55; }
  .ranges button.active { background: var(--smo-fg); color: var(--smo-bg); border-color: var(--smo-fg); }
  .swatch { width: 10px; height: 3px; border-radius: 2px; }
  .chart { position: relative; height: 220px; }
  .chart svg { width: 100%; height: 100%; display: block; overflow: visible; }
  .chart text { fill: var(--smo-muted); font-size: 10px; }
  .chart .grid line { stroke: var(--smo-line); }
  .chart .zero { stroke: var(--smo-muted); stroke-opacity: .4; }
  .chart .cross { stroke: var(--smo-muted); stroke-dasharray: 3 3; }
  .msg { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--smo-muted); font-size: .85rem; }
  .tip {
    position: absolute; pointer-events: none; background: var(--smo-bg); border: 1px solid var(--smo-line);
    border-radius: 8px; padding: 6px 8px; font-size: .75rem; box-shadow: 0 4px 14px rgba(0,0,0,.18);
    white-space: nowrap; z-index: 1;
  }
  .tip .t { color: var(--smo-muted); margin-bottom: 2px; }
  .tip .row { display: flex; align-items: center; gap: 6px; font-variant-numeric: tabular-nums; }
`;

const fmtPower = (w) => {
  if (w == null || Number.isNaN(w)) return ["—", ""];
  const a = Math.abs(w);
  return a >= 1000 ? [(w / 1000).toFixed(a >= 10000 ? 1 : 2), "kW"] : [Math.round(w).toString(), "W"];
};

class SolarMaxOnyxCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    if (config.range && !RANGES[config.range]) {
      throw new Error(`range must be one of ${Object.keys(RANGES).join(", ")}`);
    }
    this._config = { theme: "auto", range: "24h", ...config };
    this._range = this._config.range;
    this._visible = new Set(config.series || ["solar", "home", "grid", "battery", "soc"]);
    this._history = null;
    if (this._root) this._renderShell();
  }

  getCardSize() {
    return 6;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
      this._renderShell();
    }
    if (!this._inverter && !this._loadingInverter) this._loadInverter();
    this._renderLive();
  }

  connectedCallback() {
    this._timer = setInterval(() => this._loadHistory(), 5 * 60 * 1000);
    if (this._root) this._observeSize();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    if (this._resize) this._resize.disconnect();
  }

  // ── Data ──────────────────────────────────────────────────────────────────

  async _loadInverter() {
    this._loadingInverter = true;
    try {
      const inverters = await this._hass.callWS({ type: `${DOMAIN}/inverters` });
      const wanted = this._config.entry_id;
      this._inverter = (wanted ? inverters.find((i) => i.entry_id === wanted) : inverters[0]) || null;
      if (!this._inverter) this._error = wanted ? `No inverter with entry_id ${wanted}` : "No SolarTouch LAN inverter configured";
    } catch (err) {
      this._error = `Could not load inverter: ${err.message || err}`;
    }
    this._loadingInverter = false;
    this._renderShell();
    this._renderLive();
    this._loadHistory();
  }

  _entity(key) {
    return this._inverter && this._inverter.entities[key];
  }

  _num(key) {
    const id = this._entity(key);
    const st = id && this._hass.states[id];
    if (!st) return null;
    const v = parseFloat(st.state);
    return Number.isFinite(v) ? v : null;
  }

  async _loadHistory() {
    if (!this._inverter || !this._hass) return;
    const { hours, period } = RANGES[this._range];
    const end = new Date();
    const start = new Date(end.getTime() - hours * 3600 * 1000);
    const ids = SERIES.filter((s) => s.key && this._entity(s.key)).map((s) => this._entity(s.key));
    const token = (this._historyToken = Symbol());
    this._chartMessage("Loading…");
    try {
      const stats = await this._hass.callWS({
        type: "recorder/statistics_during_period",
        start_time: start.toISOString(),
        end_time: end.toISOString(),
        statistic_ids: ids,
        period,
        types: ["mean"],
      });
      if (token !== this._historyToken) return;
      this._history = this._buildSeries(stats, start, end);
    } catch (err) {
      if (token !== this._historyToken) return;
      this._history = null;
      this._chartMessage(`Could not load history: ${err.message || err}`);
      return;
    }
    this._renderChart();
  }

  _buildSeries(stats, start, end) {
    const toMs = (t) => (typeof t === "number" ? t : Date.parse(t));
    const byKey = {};
    const times = new Set();
    for (const s of SERIES) {
      if (!s.key) continue;
      const rows = stats[this._entity(s.key)] || [];
      const pts = new Map();
      for (const r of rows) {
        if (r.mean == null) continue;
        const t = toMs(r.start);
        pts.set(t, r.mean);
        times.add(t);
      }
      // Statistics lag by one period; append the live value so the chart reaches "now".
      const live = this._num(s.key);
      if (live != null) {
        pts.set(end.getTime(), live);
        times.add(end.getTime());
      }
      byKey[s.id] = pts;
    }
    const ts = [...times].filter((t) => t >= start.getTime()).sort((a, b) => a - b);
    const out = { start: start.getTime(), end: end.getTime(), ts, values: {} };
    for (const s of SERIES) {
      if (s.derived) continue;
      out.values[s.id] = ts.map((t) => (byKey[s.id] && byKey[s.id].has(t) ? byKey[s.id].get(t) : null));
    }
    out.values.home = ts.map((_, i) => this._home(out.values.solar[i], out.values.grid[i], out.values.battery[i]));
    return out;
  }

  // Home consumption = solar + grid import (+) / export (-) + battery discharge (+) / charging (-).
  _home(solar, grid, battery) {
    if (solar == null && grid == null && battery == null) return null;
    return Math.max(0, (solar || 0) + (grid || 0) + (battery || 0));
  }

  // ── Rendering ─────────────────────────────────────────────────────────────

  _renderShell() {
    if (!this._root) return;
    const theme = ["dark", "light"].includes(this._config.theme) ? this._config.theme : "auto";
    const title = this._config.title || (this._inverter && this._inverter.title) || "Solar inverter";
    this._root.innerHTML = `
      <style>${STYLE}</style>
      <ha-card class="theme-${theme}">
        <div class="wrap">
          <header>
            <div class="grow">
              <div class="title"></div>
              <div class="sub"></div>
            </div>
            <span class="pill status">—</span>
          </header>
          <div class="tiles"></div>
          <div class="today"></div>
          <div class="toolbar">
            <div class="chips"></div>
            <div class="ranges"></div>
          </div>
          <div class="chart"><svg></svg><div class="msg"></div></div>
        </div>
      </ha-card>`;
    this._root.querySelector(".title").textContent = title;
    this._root.querySelector(".sub").textContent = (this._inverter && this._inverter.model) || "";

    const chips = this._root.querySelector(".chips");
    for (const s of SERIES) {
      const b = document.createElement("button");
      b.innerHTML = `<span class="swatch" style="background:${s.color}"></span>${s.label}`;
      b.classList.toggle("off", !this._visible.has(s.id));
      b.addEventListener("click", () => {
        this._visible.has(s.id) ? this._visible.delete(s.id) : this._visible.add(s.id);
        b.classList.toggle("off", !this._visible.has(s.id));
        this._renderChart();
      });
      chips.appendChild(b);
    }
    const ranges = this._root.querySelector(".ranges");
    for (const r of Object.keys(RANGES)) {
      const b = document.createElement("button");
      b.textContent = r;
      b.classList.toggle("active", r === this._range);
      b.addEventListener("click", () => {
        this._range = r;
        ranges.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
        this._loadHistory();
      });
      ranges.appendChild(b);
    }
    this._observeSize();
    if (this._error) this._chartMessage(this._error);
    else if (this._history) this._renderChart();
  }

  _observeSize() {
    const chart = this._root && this._root.querySelector(".chart");
    if (!chart) return;
    if (this._resize) this._resize.disconnect();
    this._resize = new ResizeObserver(() => this._renderChart());
    this._resize.observe(chart);
  }

  _renderLive() {
    if (!this._root || !this._hass || !this._inverter) return;
    const solar = this._num("live_pv_total_power");
    const grid = this._num("grid_power");
    const battery = this._num("battery_power");
    const soc = this._num("battery_soc");
    const home = this._home(solar, grid, battery);

    const statusId = this._entity("connection_status");
    const status = statusId && this._hass.states[statusId] ? this._hass.states[statusId].state : "—";
    const pill = this._root.querySelector(".status");
    pill.textContent = status;
    pill.classList.toggle("error", status === "error");

    // Subtitle: model plus the inverter's own status (On-grid, Off-grid, Fault…).
    const invId = this._entity("inverter_status");
    const invState = invId && this._hass.states[invId] ? this._hass.states[invId].state : null;
    const shown = invState && !["unknown", "unavailable"].includes(invState) ? invState : null;
    this._root.querySelector(".sub").textContent = [this._inverter.model, shown].filter(Boolean).join(" · ");

    const tile = (color, label, w, note) => {
      const [v, u] = fmtPower(w);
      return `<div class="tile"><div class="label"><span class="dot" style="background:${color}"></span>${label}</div>
        <div class="value">${v}<span class="unit">${u}</span></div><div class="note">${note || "&nbsp;"}</div></div>`;
    };
    const gridNote = grid == null ? "" : grid > 5 ? "Importing" : grid < -5 ? "Exporting" : "Idle";
    // Positive battery power = discharging, negative = charging.
    const batNote = battery == null ? "" : battery > 5 ? "Discharging" : battery < -5 ? "Charging" : "Idle";
    const socTile = `<div class="tile"><div class="label"><span class="dot" style="background:var(--smo-soc)"></span>SOC</div>
      <div class="value">${soc == null ? "—" : Math.round(soc)}<span class="unit">%</span></div>
      <div class="note">${this._temps()}</div></div>`;
    this._root.querySelector(".tiles").innerHTML =
      tile("var(--smo-solar)", "Solar", solar) +
      tile("var(--smo-home)", "Home", home) +
      tile("var(--smo-grid)", "Grid", grid == null ? null : Math.abs(grid), gridNote) +
      tile("var(--smo-battery)", "Battery", battery == null ? null : Math.abs(battery), batNote) +
      socTile;

    const kwh = (key) => {
      const v = this._num(key);
      return v == null ? "—" : v.toFixed(1);
    };
    this._root.querySelector(".today").innerHTML =
      `<span>Today</span>` +
      `<span>Solar <b>${kwh("energy_produced_today")}</b> kWh</span>` +
      `<span>Import <b>${kwh("energy_import_today")}</b> kWh</span>` +
      `<span>Export <b>${kwh("energy_export_today")}</b> kWh</span>` +
      `<span>Charge <b>${kwh("battery_charge_energy_today")}</b> kWh</span>` +
      `<span>Discharge <b>${kwh("battery_discharge_energy_today")}</b> kWh</span>`;
  }

  _temps() {
    const b = this._num("battery_temperature");
    const i = this._num("inverter_internal_temperature");
    const parts = [];
    if (b != null) parts.push(`Batt ${Math.round(b)}°`);
    if (i != null) parts.push(`Inv ${Math.round(i)}°`);
    return parts.join(" · ") || "&nbsp;";
  }

  _chartMessage(text) {
    const msg = this._root && this._root.querySelector(".msg");
    if (!msg) return;
    msg.textContent = text || "";
    msg.style.display = text ? "flex" : "none";
    if (text) this._root.querySelector(".chart svg").innerHTML = "";
  }

  _renderChart() {
    if (!this._root || !this._history) return;
    const box = this._root.querySelector(".chart");
    const svg = box.querySelector("svg");
    const W = box.clientWidth;
    const H = box.clientHeight;
    if (!W || !H) return;
    const h = this._history;
    if (!h.ts.length) {
      this._chartMessage("No history yet for this range");
      return;
    }
    this._chartMessage("");

    const pad = { l: 44, r: 34, t: 8, b: 22 };
    const iw = W - pad.l - pad.r;
    const ih = H - pad.t - pad.b;
    const visible = SERIES.filter((s) => this._visible.has(s.id));

    let lo = 0;
    let hi = 0;
    for (const s of visible.filter((x) => x.axis === "w")) {
      for (const v of h.values[s.id]) {
        if (v == null) continue;
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
    }
    if (hi - lo < 100) hi = lo + 100;
    const step = niceStep((hi - lo) / 4);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;

    const x = (t) => pad.l + ((t - h.start) / (h.end - h.start)) * iw;
    const yW = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * ih;
    const yP = (v) => pad.t + (1 - v / 100) * ih;

    let out = `<g class="grid">`;
    for (let v = lo; v <= hi + 1e-9; v += step) {
      const yy = yW(v);
      const label = Math.abs(v) >= 1000 ? `${+(v / 1000).toFixed(1)}k` : `${Math.round(v)}`;
      out += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${yy}" y2="${yy}"/>`;
      out += `<text x="${pad.l - 6}" y="${yy + 3}" text-anchor="end">${label}</text>`;
    }
    out += `</g>`;
    if (lo < 0) out += `<line class="zero" x1="${pad.l}" x2="${W - pad.r}" y1="${yW(0)}" y2="${yW(0)}"/>`;
    if (this._visible.has("soc")) {
      for (const p of [0, 50, 100]) out += `<text x="${W - pad.r + 6}" y="${yP(p) + 3}">${p}%</text>`;
    }
    for (const t of timeTicks(h.start, h.end, Math.max(2, Math.floor(iw / 90)))) {
      out += `<text x="${x(t)}" y="${H - 6}" text-anchor="middle">${fmtTick(t, h.end - h.start)}</text>`;
    }
    for (const s of visible) {
      const y = s.axis === "pct" ? yP : yW;
      let d = "";
      let pen = false;
      h.ts.forEach((t, i) => {
        const v = h.values[s.id][i];
        if (v == null) {
          pen = false;
          return;
        }
        d += `${pen ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      });
      out += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.id === "home" ? 2.2 : 1.7}"
        stroke-linejoin="round" stroke-linecap="round" ${s.dashed ? 'stroke-dasharray="4 3"' : ""}/>`;
    }
    out += `<line class="cross" y1="${pad.t}" y2="${pad.t + ih}" visibility="hidden"/>`;
    out += `<rect class="hit" x="${pad.l}" y="${pad.t}" width="${iw}" height="${ih}" fill="transparent"/>`;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = out;
    this._bindTooltip(box, svg, x, visible);
  }

  _bindTooltip(box, svg, x, visible) {
    const h = this._history;
    const cross = svg.querySelector(".cross");
    let tip = box.querySelector(".tip");
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "tip";
      tip.style.display = "none";
      box.appendChild(tip);
    }
    const hide = () => {
      tip.style.display = "none";
      cross.setAttribute("visibility", "hidden");
    };
    const show = (clientX) => {
      const rect = svg.getBoundingClientRect();
      const px = clientX - rect.left;
      let best = 0;
      let bestD = Infinity;
      h.ts.forEach((t, i) => {
        const d = Math.abs(x(t) - px);
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      });
      const cx = x(h.ts[best]);
      cross.setAttribute("x1", cx);
      cross.setAttribute("x2", cx);
      cross.setAttribute("visibility", "visible");
      const rows = visible
        .map((s) => {
          const v = h.values[s.id][best];
          if (v == null) return "";
          const [n, u] = s.axis === "pct" ? [Math.round(v), "%"] : fmtPower(v);
          return `<div class="row"><span class="swatch" style="background:${s.color}"></span>${s.label} <b>${n}</b> ${u}</div>`;
        })
        .join("");
      tip.innerHTML = `<div class="t">${new Date(h.ts[best]).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}</div>${rows}`;
      tip.style.display = "block";
      const right = cx + 10;
      const left = right + tip.offsetWidth > rect.width ? cx - tip.offsetWidth - 10 : right;
      tip.style.left = `${Math.max(0, left)}px`;
      tip.style.top = "8px";
    };
    const hit = svg.querySelector(".hit");
    hit.addEventListener("mousemove", (e) => show(e.clientX));
    hit.addEventListener("mouseleave", hide);
    hit.addEventListener("touchstart", (e) => show(e.touches[0].clientX), { passive: true });
    hit.addEventListener("touchmove", (e) => show(e.touches[0].clientX), { passive: true });
    hit.addEventListener("touchend", hide);
  }
}

function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}

function timeTicks(start, end, count) {
  const spans = [1, 2, 3, 6, 12, 24, 48].map((hh) => hh * 3600 * 1000);
  const span = spans.find((s) => (end - start) / s <= count) || spans[spans.length - 1];
  const offset = new Date().getTimezoneOffset() * 60 * 1000;
  const ticks = [];
  for (let t = Math.ceil((start - offset) / span) * span + offset; t <= end; t += span) ticks.push(t);
  return ticks;
}

function fmtTick(t, span) {
  const d = new Date(t);
  if (span > 36 * 3600 * 1000 && d.getHours() === 0) {
    return d.toLocaleDateString([], { weekday: "short", day: "numeric" });
  }
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

if (!customElements.get("solarmax-onyx-card")) {
  customElements.define("solarmax-onyx-card", SolarMaxOnyxCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "solarmax-onyx-card",
    name: "SolarMax Onyx",
    description: "Live inverter summary and an interactive power / battery chart.",
    preview: true,
  });
  console.info(`%c SOLARMAX-ONYX-CARD %c ${CARD_VERSION} `, "background:#f5a524;color:#000", "");
}
