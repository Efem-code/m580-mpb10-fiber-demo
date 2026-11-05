// M580 + ATV930 + SICK MPB10 demo dashboard (front end). Live data comes from /api/stream (server-sent events).
const $ = id => document.getElementById(id);
const GMAX = 10;            // vibration gauge full scale, mm/s
const WINDOW = 120;         // trend window, s
let cfg = {};
let hist = [];              // [t, v, vx, vy, vz, temp, speed]
let last = null;
let local = true;           // this browser runs on the presenter laptop

// ------------------------------------------------------------ tabs
document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  document.querySelectorAll("nav button").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("on", t.id === "tab-" + b.dataset.tab));
  try { localStorage.setItem("tab", b.dataset.tab); } catch (e) {}
  drawCharts();
});
try { const t = localStorage.getItem("tab"); if (t) document.querySelector(`nav button[data-tab=${t}]`)?.click(); } catch (e) {}

// ------------------------------------------------------------ helpers
const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  .then(r => r.json());
const fmt = (v, d = 2) => v == null ? "–" : v.toFixed(d);
const hhmmss = t => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const hex4 = v => v == null ? "–" : "16#" + v.toString(16).toUpperCase().padStart(4, "0");
const pct = (v, max) => v == null ? "0" : Math.max(0, Math.min(100, 100 * v / max)) + "%";

function polar(r, frac) {   // frac 0 = left, 1 = right, along the top half circle
  const a = Math.PI * (1 - frac);
  return [130 + r * Math.cos(a), 135 - r * Math.sin(a)];
}
function arc(f0, f1, color) {
  const [x0, y0] = polar(100, f0), [x1, y1] = polar(100, f1);
  return `<path d="M${x0} ${y0} A100 100 0 0 1 ${x1} ${y1}" stroke="${color}" stroke-width="22" fill="none"/>`;
}
function drawBands() {
  const f = v => Math.min(1, v / GMAX);
  $("gaugeBands").innerHTML =
    arc(0, f(cfg.zone_ab), "#2E9E45") + arc(f(cfg.zone_ab), f(cfg.zone_bc), "#9BCB6B") +
    arc(f(cfg.zone_bc), f(cfg.zone_cd), "#E7A51C") + arc(f(cfg.zone_cd), 1, "#C8322B");
}

// ------------------------------------------------------------ config: IP + setup tabs
const IPS = ["laptop_ip", "plc_host", "noc_host", "cra_host", "sig300_host", "atv_host", "ap_host", "cobot_host", "netmask", "gateway"];
const IP_DEFAULTS = { laptop_ip: "192.168.10.100", plc_host: "192.168.10.10", noc_host: "192.168.20.10",
  cra_host: "192.168.10.20", sig300_host: "192.168.20.20", atv_host: "192.168.20.30",
  ap_host: "192.168.20.1", cobot_host: "192.168.20.94", netmask: "255.255.255.0", gateway: "", cra_drop: 3 };
// true when two addresses share a subnet under the rig's mask
const ip32 = s => String(s || "").split(".").reduce((v, o) => v * 256 + (+o || 0), 0);
const sameNet = (a, b, mask) => ((ip32(a) & ip32(mask)) >>> 0) === ((ip32(b) & ip32(mask)) >>> 0);
const FIELDS = { "c-mode": "mode", "c-port": "plc_port", "c-unit": "unit_id", "c-base": "base", "c-cmd": "cmd_base",
  "c-asset": "asset", "c-ab": "zone_ab", "c-bc": "zone_bc", "c-cd": "zone_cd", "c-tw": "temp_warn",
  "c-max": "max_rpm", "c-nom": "nominal_rpm", "c-jog": "jog_rpm" };
const MAP = [
  [0, "Heartbeat (PLC counts up; frozen = stale)"],
  [1, "Sensor bits: b0 SIG300 OK · b1 MPB10 on S1 · b2 vib warning · b3 vib alarm · b4 shock · b5 temp warning"],
  [2, "Velocity RMS overall, mm/s × 100"], [3, "Velocity X, mm/s × 100 (−32768 = n/a)"], [4, "Velocity Y, mm/s × 100"],
  [5, "Velocity Z, mm/s × 100"], [6, "Temperature, °C × 10"], [7, "Shock counter"], [8, "MPB10 alarm word"],
  ["9–18", "Raw IO-Link process data, 20 bytes, high byte first"],
  [20, "Drive bits: b0 ATV930 OK · b1 ready · b2 running · b3 fault · b4 PLC allows dashboard · b5 watchdog OK · b6 reverse"],
  [21, "ETA status word"], [22, "Output speed, rpm"], [23, "Speed reference sent, rpm"], [24, "Motor current, A × 10"],
  [25, "Torque, %"], [26, "Motor thermal state, %"], [27, "Last fault code"], [28, "CMD word sent"],
  [29, "RIO / status bits: b0 drop OK · b4 xDemoEnable · b5 green · b6 amber · b7 red (the program’s stack light) · b8 ring closed · b9 ring mapped (b1–b3, b10: no panel, always 0)"],
  [30, "Accel ramp in use, s × 10 (ACC)"], [31, "Decel ramp in use, s × 10 (DEC)"],
];
const CMD_MAP = [[0, "Dashboard heartbeat (changes every 200 ms)"], [1, "b0 run · b1 stop · b2 fault reset · b3 reverse · b4 jog"],
  [2, "Speed request, rpm"], [3, "Accel ramp, s × 10"], [4, "Decel ramp, s × 10"]];

function applyCfg(c) {
  cfg = c;
  if (c._local !== undefined) local = c._local;
  if (c._app) document.body.classList.add("app");   // the TV app: laptop-only parts hide
  for (const [id, k] of Object.entries(FIELDS)) $(id).value = c[k];
  $("c-allow").checked = !!c.allow_control;
  showLimits(c);
  for (const k of IPS) $("ip-" + k).value = c[k];
  $("ip-cra_drop").value = c.cra_drop;
  seqLoad(c);
  $("nocIp").textContent = $("nocIp2").textContent = c.noc_host;
  $("craIp").textContent = `${c.cra_host} · drop ${c.cra_drop}`;
  $("assetName").textContent = c.asset;
  $("plcIp").textContent = $("plcIp2").textContent = c.plc_host;
  $("sigIp").textContent = c.sig300_host;
  $("atvIp").textContent = c.atv_host;
  $("lnkSig").href = "http://" + c.sig300_host;
  $("lnkAtv").href = "http://" + c.atv_host;
  $("tText").textContent = `Warning at ${c.temp_warn} °C`;
  $("spSlider").max = c.max_rpm;
  const row = (o, t, base) => `<tr><td>${typeof o === "number" ? base + o : o.split("–").map(x => base + +x).join("–")}</td><td>${t}</td></tr>`;
  $("mapBody").innerHTML = MAP.map(([o, t]) => row(o, t, c.base)).join("");
  $("cmdMapBody").innerHTML = CMD_MAP.map(([o, t]) => row(o, t, c.cmd_base)).join("");
  showIpMsgs([], c._warnings || []);
  drawBands();
  drawCharts();
}
function showIpMsgs(errors, warns) {
  $("ipMsgs").innerHTML = errors.map(e => `<div class="msg bad">${e}</div>`).join("") + warns.map(w => `<div class="msg warn">${w}</div>`).join("");
}
async function saveCfg(body, note) {
  const r = await post("/api/config", body);
  if (r.errors && r.errors.length) { showIpMsgs(r.errors, r.warnings || []); note.textContent = "Not saved"; return false; }
  applyCfg(Object.assign({}, r.config, { _warnings: r.warnings }));
  note.textContent = "Saved";
  setTimeout(() => note.textContent = "", 2500);
  return true;
}
$("save").onclick = () => {
  const body = { allow_control: $("c-allow").checked };
  for (const [id, k] of Object.entries(FIELDS)) body[k] = $(id).value;
  if (body.allow_control && !cfg.allow_control &&
      !confirm("Allow the dashboard to start and stop the motor through the M580?\n\nMake sure the E-stop is wired to the ATV930 STO input and the motor is guarded.")) {
    $("c-allow").checked = false; return;
  }
  saveCfg(body, $("saved"));
};
$("clear").onclick = async () => { await post("/api/clear", {}); hist = []; drawCharts(); };
$("ipSave").onclick = () => {
  const b = { cra_drop: $("ip-cra_drop").value };
  IPS.forEach(k => b[k] = $("ip-" + k).value.trim());
  saveCfg(b, $("ipSaved"));
};
$("ipReset").onclick = () => {
  IPS.forEach(k => $("ip-" + k).value = IP_DEFAULTS[k]);
  $("ip-cra_drop").value = IP_DEFAULTS.cra_drop;
  $("ipSaved").textContent = "Defaults filled in: press Save";
};
$("ipPrint").onclick = () => window.print();
$("ipCopy").onclick = () => {
  const lines = ["IP plan: M580 + SICK MPB10 + ATV930 demo", "",
    "Control network",
    `  Laptop               ${cfg.laptop_ip}   (CPU service port ETH1)`,
    `  M580 CPU BMEP584040S ${cfg.plc_host}`,
    `  RIO drop BMECRA31210 ${cfg.cra_host}   (drop ${cfg.cra_drop}, rotary switches)`, "",
    "Device network",
    `  BMENOC0311           ${cfg.noc_host}   (backplane port ${sameNet(cfg.noc_host, cfg.plc_host, cfg.netmask) ? "enabled" : "disabled"})`,
    `  SICK SIG300          ${cfg.sig300_host}   (NOC ETH3)`,
    `  Altivar ATV930       ${cfg.atv_host}   (NOC ETH2)`,
    `  D-Link Wi-Fi AP      ${cfg.ap_host}   (LAN port to NOC ETH1, DHCP pool .150-.199)`,
    `  Lexium cobot         ${cfg.cobot_host}   (on the D-Link)`, "",
    `Subnet mask          ${cfg.netmask}`, `Gateway              ${cfg.gateway || "none"}`, "",
    `Modbus: status %MW${cfg.base}-${cfg.base + 31}, commands %MW${cfg.cmd_base}-${cfg.cmd_base + 4}, unit ID ${cfg.unit_id}`];
  navigator.clipboard.writeText(lines.join("\n")).then(() => { $("ipSaved").textContent = "Copied"; setTimeout(() => $("ipSaved").textContent = "", 2000); });
};
IPS.forEach(k => $("ip-" + k).addEventListener("input", e => {
  e.target.classList.toggle("bad", !(k === "gateway" && !e.target.value.trim()) && !/^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/.test(e.target.value.trim()));
}));

// ------------------------------------------------------------ sim + drive controls
let sigOk = true;
$("unb").oninput = e => post("/api/sim", { action: "unbalance", value: e.target.value / 100 });
$("heat").oninput = e => post("/api/sim", { action: "heat", value: e.target.value / 100 });
$("knock").onclick = () => post("/api/sim", { action: "knock" });
$("sig").onclick = () => post("/api/sim", { action: "sig", value: !sigOk });
$("swap").onclick = () => post("/api/sim", { action: "swap" });
let ringOk = true;
$("ring").onclick = () => post("/api/sim", { action: "ring", value: !ringOk });
let dropOk = true;
$("drop").onclick = () => post("/api/sim", { action: "drop", value: !dropOk });
$("reset").onclick = () => { $("unb").value = 0; $("heat").value = 0; post("/api/sim", { action: "reset" }); };

const drive = async (action, value) => {
  const r = await post("/api/drive", { action, value });
  if (r.error) $("ctlMsg").innerHTML = `<div class="msg bad">${r.error}</div>`;
};
let spTimer = null;
$("spSlider").oninput = e => {
  $("spVal").textContent = e.target.value;
  clearTimeout(spTimer);
  spTimer = setTimeout(() => drive("speed", e.target.value), 120);
};
$("dRun").onclick = () => drive("run_fwd");
$("dRunRev").onclick = () => drive("run_rev");
$("dStop").onclick = () => { jogEnd(); drive("stop"); };
$("dReset").onclick = () => drive("reset");
$("dTrip").onclick = () => drive("trip");
$("presets").onclick = e => {
  const v = e.target.dataset && e.target.dataset.rpm;
  if (v) { $("spSlider").value = v; $("spVal").textContent = v; drive("speed", v); }
};
$("rampApply").onclick = async () => {
  const r = await post("/api/drive", { action: "ramps", value: { accel: $("accIn").value, decel: $("decIn").value } });
  $("rampSaved").textContent = r.error || "Applied";
  setTimeout(() => $("rampSaved").textContent = "", 2500);
};

// ------------------------------------------------------------ demo sequence
let seqDraft = [], seqDirty = false;
const DIRS = { fwd: "Forward ▶", rev: "◀ Reverse", stop: "Stop" };
function seqRows() {
  $("seqBody").innerHTML = seqDraft.map((st, i) => `<tr data-i="${i}"><td>${i + 1}</td>
    <td><select data-k="dir">${Object.entries(DIRS).map(([k, t]) => `<option value="${k}"${k === st.dir ? " selected" : ""}>${t}</option>`).join("")}</select></td>
    <td><input data-k="rpm" type="number" min="0" step="10" value="${st.rpm}"${st.dir === "stop" ? " disabled" : ""}></td>
    <td><input data-k="hold" type="number" min="1" max="300" step="0.5" value="${st.hold}"></td>
    <td><button class="btn" data-del="${i}" title="Remove step">×</button></td></tr>`).join("");
}
function seqLoad(c) {
  if (seqDirty) return;
  seqDraft = JSON.parse(JSON.stringify(c.sequence || []));
  $("seqLoop").checked = !!c.seq_loop;
  seqRows();
}
$("seqBody").addEventListener("input", e => {
  const tr = e.target.closest("tr"), k = e.target.dataset.k;
  if (!tr || !k) return;
  const st = seqDraft[+tr.dataset.i];
  st[k] = k === "dir" ? e.target.value : +e.target.value;
  if (k === "dir") { if (st.dir === "stop") st.rpm = 0; else if (!st.rpm) st.rpm = 600; seqRows(); }
  seqDirty = true; $("seqSaved").textContent = "Not saved yet";
});
$("seqBody").addEventListener("click", e => {
  const i = e.target.dataset.del;
  if (i === undefined) return;
  seqDraft.splice(+i, 1); seqDirty = true; seqRows(); $("seqSaved").textContent = "Not saved yet";
});
$("seqLoop").onchange = () => { seqDirty = true; $("seqSaved").textContent = "Not saved yet"; };
$("seqAdd").onclick = () => {
  const lastRun = [...seqDraft].reverse().find(s => s.dir !== "stop");
  seqDraft.push({ dir: lastRun ? lastRun.dir : "fwd", rpm: 600, hold: 6 });
  seqDirty = true; seqRows(); $("seqSaved").textContent = "Not saved yet";
};
async function seqSave() {
  const r = await post("/api/drive", { action: "seq_save", value: { steps: seqDraft, loop: $("seqLoop").checked } });
  if (r.error) { $("seqSaved").textContent = r.error; return false; }
  seqDirty = false;
  applyCfg(await fetch("/api/config").then(r => r.json()));
  $("seqSaved").textContent = "Saved"; setTimeout(() => $("seqSaved").textContent = "", 2000);
  return true;
}
$("seqSave").onclick = seqSave;
$("seqPlay").onclick = async () => {
  if (seqDirty && !(await seqSave())) return;
  drive("seq_start");
};
$("seqStop").onclick = () => drive("seq_stop");

// jog: hold-to-run. The page repeats the request every 200 ms; the server drops it 0.5 s after the last one,
// so a lost mouse-up (or a closed page) stops the motor anyway.
let jogTimer = null, jogBtn = null;
function jogEnd() {
  if (!jogTimer) return;
  clearInterval(jogTimer); jogTimer = null;
  jogBtn.classList.remove("held"); jogBtn = null;
  drive("jog_stop");
}
[["jogFwd", "fwd"], ["jogRev", "rev"]].forEach(([id, dir]) => {
  const b = $(id);
  b.addEventListener("pointerdown", e => {
    if (b.disabled) return;
    e.preventDefault();
    try { b.setPointerCapture(e.pointerId); } catch (err) {}
    jogEnd(); jogBtn = b; b.classList.add("held");
    drive("jog", dir); jogTimer = setInterval(() => drive("jog", dir), 200);
  });
  ["pointerup", "pointercancel", "lostpointercapture"].forEach(ev => b.addEventListener(ev, jogEnd));
});
window.addEventListener("blur", jogEnd);
document.addEventListener("keydown", e => {   // space bar = STOP, wherever the presenter is
  if (e.code === "Space" && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); jogEnd(); drive("stop"); }
});

// ------------------------------------------------------------ render
const HEALTH = {
  good: ["GOOD", "Running smoothly"], warning: ["WARNING", "Plan maintenance"],
  alarm: ["ALARM", "Act now: risk of failure"], offline: ["NO DATA", "Sensor data not reaching the PLC"],
};

function render(s) {
  last = s;
  const m = s.mpb, L = s.links, d = s.drive;
  const pill = $("modePill");
  pill.className = "pill " + (s.mode === "sim" ? "sim" : "live");
  pill.textContent = s.mode === "sim" ? "SIMULATION" : `LIVE · M580 ${s.plc.host}` + (s.control.writing ? " · CONTROL ON" : "");
  $("simbar").style.display = s.mode === "sim" && local ? "" : "none";

  renderAlarms(s.alarms);
  const cm = s.mpb;
  $("cV").textContent = fmt(cm.v); $("cX").textContent = fmt(cm.vx); $("cY").textContent = fmt(cm.vy); $("cZ").textContent = fmt(cm.vz);
  $("cT").textContent = fmt(cm.temp, 1); $("cS").textContent = cm.shocks ?? "–";
  $("cHealth").textContent = s.health === "offline" ? "No sensor data" : "";

  // ---- machine tab
  const [label, text] = HEALTH[s.health];
  $("healthCard").className = "card health " + s.health;
  $("healthState").textContent = label;
  let why = text;
  if (s.health === "offline") why = !L.plc ? "Can’t reach the M580" : !L.fresh ? "PLC data is stale" : !L.eip ? "SIG300 not reachable: check its cable on NOC ETH3" : "MPB10 not on port S1";
  else if (m.temp_warn && s.health !== "alarm") why = "Temperature high";
  $("healthText").textContent = why;
  $("vVal").textContent = fmt(m.v);
  $("zoneText").textContent = s.zone ? `ISO 10816-3 zone ${s.zone}` : "ISO 10816-3 zone –";
  $("needle").setAttribute("transform", `rotate(${m.v == null ? -90 : -90 + 180 * Math.min(1, m.v / GMAX)} 130 135)`);
  $("needle").style.opacity = m.v == null ? .25 : 1;
  $("mSpeed").textContent = d.speed == null ? "–" : Math.round(d.speed);
  $("mSpeedBar").style.width = pct(d.speed == null ? null : Math.abs(d.speed), cfg.nominal_rpm * 1.1);
  $("mDrive").textContent = "ATV930 · " + d.state;
  $("tVal").textContent = fmt(m.temp, 1);
  $("tBar").style.width = pct(m.temp, 100);
  $("tMark").style.left = Math.min(100, cfg.temp_warn) + "%";
  const amax = Math.max(cfg.zone_cd * 1.4, ...[m.vx, m.vy, m.vz].filter(x => x != null));
  [["x", m.vx], ["y", m.vy], ["z", m.vz]].forEach(([a, val]) => {
    $("v" + a).textContent = val == null ? "–" : val.toFixed(2) + " mm/s";
    $("b" + a).style.width = pct(val, amax);
  });
  $("shocks").textContent = m.shocks ?? "–";
  $("events").innerHTML = (s.events || []).map(e => `<li class="ev-${e.level}"><time>${hhmmss(e.t)}</time><span>${e.text}</span></li>`).join("")
    || `<li><time></time><span class="sub">Nothing yet.</span></li>`;
  if (s.sim) {
    sigOk = s.sim.sig;
    $("sig").textContent = sigOk ? "Pull the SIG300 cable" : "Reconnect the SIG300";
    $("sig").className = "btn " + (sigOk ? "cut" : "orange");
    $("swap").disabled = s.sim.swapping;
    $("swap").textContent = s.sim.swapping ? "Swapping…" : "Swap the sensor";
    ringOk = s.sim.ring;
    $("ring").textContent = ringOk ? "Pull a ring fiber" : "Reconnect the ring fiber";
    $("ring").className = "btn " + (ringOk ? "" : "orange");
    dropOk = s.sim.drop;
    $("drop").textContent = dropOk ? "Pull both fibers" : "Reconnect both fibers";
    $("drop").className = "btn " + (dropOk ? "" : "orange");
  }

  renderDrive(s);
  renderVfd(s);
  renderNetwork(s);
  renderRacks(s);
  renderReach(s.reach || {});

  if ((m.v != null || d.speed != null) && (!hist.length || s.t - hist[hist.length - 1][0] >= 0.5)) {
    hist.push([s.t, m.v, m.vx, m.vy, m.vz, m.temp, d.speed, d.ref]);
    while (hist.length && hist[0][0] < s.t - 320) hist.shift();
  }
  drawCharts();
}

const ETA_BITS = ["Ready to sw. on", "Switched on", "Op. enabled", "Fault", "Voltage", "Quick stop", "Sw. on disabled", "Warning",
  "", "Remote", "Target reached", "Limit", "", "", "Stop key", "Reverse"];
const CMD_BITS = ["Switch on", "Enable volt.", "Quick stop", "Enable op.", "", "", "", "Fault reset", "Halt", "", "", "Reverse", "", "", "", ""];
const bitsHtml = (v, names) => Array.from({ length: 16 }, (_, i) => 15 - i).map(b =>
  `<div class="${v != null && (v >> b) & 1 ? "on" : ""}" title="${names[b] || "bit " + b}">${b}<small>${names[b] ? names[b].split(" ")[0] : "·"}</small></div>`).join("");

function chip(id, on, kind) { $(id).className = "chip" + (on ? " on " + kind : ""); }

const ALM_STATES = ["Normal", "Active · not acknowledged", "Active · acknowledged", "Cleared · not acknowledged"];
let almLog = [];
const esc = t => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function renderAlarms(a) {
  const on = !!a;
  const badge = on ? a.unack || a.active : 0;
  $("almBadge").textContent = badge;
  $("almBadge").classList.toggle("show", badge > 0);
  $("almAckAll").disabled = !on || !a.unack;
  $("almClear").disabled = $("almCsv").disabled = !on || !a.log.length;
  if (!on) {
    $("almTitle").textContent = "Alarms";
    $("almRows").innerHTML = `<tr><td colspan="5" class="sub">No alarm data from the PLC yet.</td></tr>`;
    $("almLog").innerHTML = "";
    $("almNote").textContent = "";
    return;
  }
  almLog = a.log;
  const shown = a.items.filter(i => i.state);
  $("almTitle").textContent = shown.length ? `Alarms · ${a.active} active, ${a.unack} to acknowledge` : "Alarms · all clear";
  $("almRows").innerHTML = shown.map(i => `<tr class="s${i.state} p${i.prio}"><td><b>${esc(i.text)}</b></td>
    <td><span class="almprio p${i.prio}">${i.prio === 1 ? "Critical" : "Warning"}</span></td>
    <td><span class="almstate s${i.state}">${ALM_STATES[i.state]}</span></td>
    <td class="t">${i.raised || "–"}</td>
    <td>${i.state !== 2 ? `<button class="btn" data-ack="${i.id}">Acknowledge</button>` : ""}</td></tr>`).join("")
    || `<tr><td colspan="5" class="sub">No alarms.</td></tr>`;
  $("almLog").innerHTML = a.log.map(e => `<tr><td class="t">${e.at || "–"}</td><td>${esc(e.text)}</td><td>${e.type}</td></tr>`).join("")
    || `<tr><td colspan="3" class="sub">Nothing yet.</td></tr>`;
  $("almNote").textContent = !a.clock ? "The PLC isn't filling the alarm block yet: load the S06_Alarms section."
    : a.clock_set ? `PLC clock ${a.clock}.` : `The PLC's clock isn't set (it reads ${a.clock}), so timestamps will be wrong until it is.`;
}

$("almAckAll").onclick = () => post("/api/alarm", { action: "ack" });
$("almRows").onclick = e => { const n = e.target.dataset.ack; if (n) post("/api/alarm", { action: "ack", id: +n }); };
$("almClear").onclick = () => { if (confirm("Clear the alarm history?")) post("/api/alarm", { action: "clear" }); };
$("almCsv").onclick = () => {
  const rows = [["Time", "Alarm", "Event"], ...almLog.map(e => [e.at || "", e.text, e.type])];
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([rows.map(r => r.map(c => `"${c}"`).join(",")).join("\r\n")], { type: "text/csv" }));
  a.download = "m580-alarm-history.csv";
  a.click();
  URL.revokeObjectURL(a.href);
};

// ---- limits: dragging sends them to the PLC, and the page follows if a screen changes them
function limitLabels() {
  $("lWarnV").textContent = (+$("lWarn").value).toFixed(2);
  $("lAlarmV").textContent = (+$("lAlarm").value).toFixed(2);
  $("lTempV").textContent = (+$("lTemp").value).toFixed(0);
}
function showLimits(c) {
  if (["lWarn", "lAlarm", "lTemp"].some(id => document.activeElement === $(id))) return;
  $("lWarn").value = c.zone_bc;
  $("lAlarm").value = c.zone_cd;
  $("lTemp").value = c.temp_warn;
  limitLabels();
}
function sendLimits(e) {
  let w = +$("lWarn").value, a = +$("lAlarm").value;
  if (e.target === $("lWarn") && w > a) a = w;
  if (e.target === $("lAlarm") && a < w) w = a;
  $("lWarn").value = w; $("lAlarm").value = a;
  limitLabels();
  post("/api/config", { zone_ab: Math.min(cfg.zone_ab, w / 2), zone_bc: w, zone_cd: a, temp_warn: +$("lTemp").value })
    .then(r => { if (r.config) applyCfg(Object.assign({}, r.config)); });
  e.target.blur();
}
["lWarn", "lAlarm", "lTemp"].forEach(id => { $(id).oninput = limitLabels; $(id).onchange = sendLimits; });

// ---- cobot: the Lexium Cobot controller page, with the sensor's live values above it
const COBOT = "http://127.0.0.1:8765/";
async function cobotOpen() {
  const local = ["127.0.0.1", "localhost"].includes(location.hostname);
  let up = false;
  if (local) { try { await fetch(COBOT, { mode: "no-cors", cache: "no-store" }); up = true; } catch (e) {} }
  $("cobotOff").style.display = up ? "none" : "";
  $("cobotFrame").style.display = up ? "" : "none";
  if (up && !$("cobotFrame").getAttribute("src")) $("cobotFrame").src = COBOT;
}
$("cobotRetry").onclick = cobotOpen;
document.querySelector("nav button[data-tab=cobot]").addEventListener("click", cobotOpen);

function renderDrive(s) {
  const d = s.drive, c = s.control;
  $("dState").textContent = d.state;
  $("dState").style.color = d.fault ? "var(--bad)" : d.running ? "var(--good)" : "var(--ink)";
  chip("cConn", d.healthy, "g"); chip("cReady", d.ready, "g"); chip("cRun", d.running, "g");
  chip("cFault", d.fault, "r"); chip("cRev", d.reverse, "y");
  $("dFault").textContent = d.fault ? `Last fault code ${d.fault_code}: look it up in the ATV930 fault list, fix the cause, then Fault reset.` : "";
  $("dSpeed").textContent = d.speed == null ? "–" : Math.round(d.speed);
  $("dRef").textContent = d.ref == null ? "" : `reference ${d.ref} rpm`;
  $("dSpeedBar").style.width = pct(d.speed == null ? null : Math.abs(d.speed), cfg.max_rpm * 1.1);
  $("dRefMark").style.left = pct(d.ref == null ? 0 : Math.abs(d.ref), cfg.max_rpm * 1.1);
  $("dCur").textContent = d.current == null ? "–" : d.current.toFixed(1) + " A";
  $("dCurBar").style.width = pct(d.current, 8);
  $("dTor").textContent = d.torque == null ? "–" : d.torque + " %";
  $("dTorBar").style.width = pct(d.torque, 150);
  $("dTh").textContent = d.thermal == null ? "–" : d.thermal + " %";
  $("dThBar").style.width = pct(d.thermal, 120);
  $("dVib").textContent = s.mpb.v == null ? "–" : s.mpb.v.toFixed(2) + " mm/s";
  $("dVibBar").style.width = pct(s.mpb.v, GMAX);
  $("dVibBar").style.background = s.health === "alarm" ? "var(--bad)" : s.health === "warning" ? "var(--warn)" : "var(--fiber)";
  $("etaHex").textContent = hex4(d.eta);
  $("etaBits").innerHTML = bitsHtml(d.eta, ETA_BITS);
  $("cmdHex").textContent = hex4(d.cmd);
  const cm = d.cmd == null ? "" : (d.cmd & 0x80) ? "fault reset" : (d.cmd & 0x0F) === 0x0F ? ((d.cmd & 0x800) ? "run reverse" : "run forward") : (d.cmd & 0x0F) === 0x07 ? "switched on, stop" : (d.cmd & 0x0F) === 0x06 ? "shutdown → ready" : "";
  $("cmdText").textContent = cm;
  $("cmdBits").innerHTML = bitsHtml(d.cmd, CMD_BITS);

  // the stack light the program works out (no lamps fitted on this board)
  const P = s.panel || {}, dropOk = s.links.drop;
  $("lRed").className = P.red ? "on r" : ""; $("lAmb").className = P.amber ? "on a" : ""; $("lGrn").className = P.green ? "on g" : "";
  $("pKey").innerHTML = !s.links.fresh ? "–" : P.enable ? `<span class="led g"></span>ON` : `<span class="led y"></span>OFF (xDemoEnable)`;
  $("pSrc").textContent = !d.running ? "–" : c.run ? "This dashboard" : "PLC / animation table";
  $("pDrop").innerHTML = !s.links.fresh ? "–" : !dropOk ? `<span class="led r"></span>Lost` :
    !P.ring_known ? `<span class="led g"></span>OK` : P.ring ? `<span class="led g"></span>OK · ring closed` : `<span class="led y"></span>OK · ring open`;

}

// ------------------------------------------------------------ VFD control tab
// ---- selectors: dashboard control here, and the two PLC enables through aEnableCmd at %MW1286
function sel(id, on, can) {
  $(id).textContent = on ? "ON" : "OFF";
  $(id).classList.toggle("on", on);
  $(id).disabled = !can;
}
$("selCtl").onclick = () => post("/api/config", { allow_control: !$("selCtl").classList.contains("on") })
  .then(r => { if (r.config) applyCfg(Object.assign({}, r.config)); });
$("selDash").onclick = () => drive("plc_enable", { which: "dash", on: !$("selDash").classList.contains("on") });
$("selDemo").onclick = () => drive("plc_enable", { which: "demo", on: !$("selDemo").classList.contains("on") });

function renderVfd(s) {
  const d = s.drive, c = s.control, L = s.links, P = s.panel || {}, live = s.mode === "live";
  // speed
  $("vSpeed").textContent = d.speed == null ? "–" : Math.abs(Math.round(d.speed));
  $("vDir").textContent = d.speed == null || Math.abs(d.speed) < 1 ? "" : d.speed < 0 ? "◀ REVERSE" : "FORWARD ▶";
  $("vSpeedBar").style.width = pct(d.speed == null ? null : Math.abs(d.speed), cfg.max_rpm * 1.1);
  $("vRefMark").style.left = pct(d.ref == null ? 0 : Math.abs(d.ref), cfg.max_rpm * 1.1);
  $("vRef").textContent = d.ref == null ? "–" : `Reference sent by the PLC: ${d.ref} rpm · bar = actual, marker = reference`;
  const presets = [300, 600, 900, 1200, 1500].filter(v => v <= c.max_rpm);
  const ph = presets.map(v => `<button class="btn${v === c.speed ? " on" : ""}" data-rpm="${v}">${v}</button>`).join("");
  if ($("presets").dataset.h !== ph) { $("presets").innerHTML = ph; $("presets").dataset.h = ph; }
  // ramps
  if (document.activeElement !== $("accIn")) $("accIn").value = c.accel_s;
  if (document.activeElement !== $("decIn")) $("decIn").value = c.decel_s;
  $("vRamps").textContent = d.accel == null ? "–" : `In use by the PLC: accel ${d.accel.toFixed(1)} s · decel ${d.decel.toFixed(1)} s`;

  // permissives, in the order we'd check them
  const stoOff = d.eta != null && d.state !== "Switch on disabled";
  const perms = [
    [L.fresh, "M580 connected and running"],
    [d.healthy, "ATV930 on EtherNet/IP (via the BMENOC0311)"],
    [!d.fault, d.fault ? `Drive fault${d.fault_code ? " " + d.fault_code : ""}: fix, then Fault reset (the keypad shows which)` : "No drive fault"],
    [stoOff, "Power stage ready (STO / E-stop released)"],
    [!!P.enable, "Demo enable ON in the PLC (xDemoEnable)"],
    [c.allowed, "Dashboard control on (Setup tab)"],
    [!live || d.plc_allows, "PLC permission xDashboardAllowed"],
    [!live || d.watchdog_ok, "PLC watchdog sees this dashboard"],
    [local, cfg._app ? "This is the presenter’s screen" : "This is the presenter’s laptop"],
  ];
  const blocking = perms.filter(x => !x[0]).length;
  sel("selCtl", c.allowed, local);
  sel("selDash", !!d.plc_allows, local && c.allowed && live && L.fresh);
  sel("selDemo", !!P.enable, local && c.allowed && live && L.fresh);
  $("perm").innerHTML = perms.map(([ok, t]) => `<li class="${ok ? "" : "no"}"><span class="led ${ok ? "g" : "r"}"></span>${t}</li>`).join("");
  $("vReady").textContent = blocking ? `NOT READY · ${blocking} to fix` : d.running ? "RUNNING" : "READY";
  $("vReady").style.color = blocking ? "var(--bad)" : "var(--good)";

  // who is in control
  $("vSrc").innerHTML = !d.running ? "Motor stopped." : `In control: <b>${c.jog ? "this dashboard (jog)" : c.run ? "this dashboard" : "PLC / animation table"}</b>`;

  // what the buttons can do right now, and why not
  let msg = "", can = c.allowed && local;
  if (!local) msg = `<div class="msg warn">View-only screen: control the demo from the presenter’s laptop.</div>`;
  else if (!c.allowed) msg = `<div class="msg warn">Drive control from the dashboard is off. Turn it on in Setup (live mode only).</div>`;
  else if (live && !d.healthy) { msg = `<div class="msg bad">No EtherNet/IP connection to the ATV930.</div>`; }
  else if (L.fresh && !P.enable) msg = `<div class="msg warn"><code>xDemoEnable</code> is OFF in the PLC: turn on Demo enable in Ready to run, above.</div>`;
  else if (live && !d.plc_allows) msg = `<div class="msg warn">The PLC has not enabled dashboard control: turn on PLC permission in Ready to run, above.</div>`;
  else if (live && !d.watchdog_ok) msg = `<div class="msg warn">PLC watchdog doesn’t see the dashboard heartbeat yet: check the command block address.</div>`;
  else if (d.fault) msg = `<div class="msg bad">Drive in fault: fix the cause, then Fault reset.</div>`;
  else if (c.jog) msg = `<div class="msg ok">Jogging ${c.jog_rev ? "reverse" : "forward"} at ${Math.min(c.jog_rpm, c.max_rpm)} rpm. Let go to stop.</div>`;
  else msg = c.run ? `<div class="msg ok">Run ${c.reverse ? "reverse" : "forward"} requested at ${c.speed} rpm. Space bar = STOP.</div>`
                   : `<div class="msg ok">Ready. Pick a speed, then Start forward or reverse. Space bar = STOP.</div>`;
  if ($("ctlMsg").dataset.msg !== msg) { $("ctlMsg").innerHTML = msg; $("ctlMsg").dataset.msg = msg; }
  $("spSlider").disabled = !can;
  $("presets").querySelectorAll("button").forEach(b => b.disabled = !can);
  $("rampApply").disabled = !can;
  $("dStop").disabled = !local;
  $("dReset").disabled = !can;
  $("dRun").disabled = $("dRunRev").disabled = !can || d.fault || c.run;   // change direction: Stop first
  ["jogFwd", "jogRev"].forEach(id => $(id).disabled = !can || d.fault || c.run || (d.running && !c.jog));
  $("jogTxt").textContent = `hold to run at ${Math.min(c.jog_rpm, c.max_rpm)} rpm`;
  $("dTrip").style.display = s.mode === "sim" && local ? "" : "none";
  if (document.activeElement !== $("spSlider")) { $("spSlider").value = c.speed; $("spVal").textContent = c.speed; }

  // demo sequence
  const q = s.prog || { on: false };
  if (q.on) {
    ["dRun", "dRunRev", "jogFwd", "jogRev", "spSlider"].forEach(id => $(id).disabled = true);
    $("presets").querySelectorAll("button").forEach(b => b.disabled = true);
    const st = q.step, what = st.dir === "stop" ? "stop" : `${st.dir === "rev" ? "reverse" : "forward"} ${Math.min(st.rpm, c.max_rpm)} rpm`;
    const sm = `<div class="msg ok"><b>Sequence running</b> · step ${q.i + 1} of ${q.n}: ${what} · ` +
      (q.wait ? "stopping before changing direction…" : `${Math.ceil(q.left)} s left`) + ` · cycle ${q.cycle}</div>`;
    if ($("seqMsg").dataset.m !== sm) { $("seqMsg").innerHTML = sm; $("seqMsg").dataset.m = sm; }
    const cm = `<div class="msg ok">Demo sequence in control. STOP or the space bar ends it.</div>`;
    if ($("ctlMsg").dataset.msg !== cm) { $("ctlMsg").innerHTML = cm; $("ctlMsg").dataset.msg = cm; }
  } else if ($("seqMsg").dataset.m) { $("seqMsg").innerHTML = ""; $("seqMsg").dataset.m = ""; }
  $("seqBody").querySelectorAll("tr").forEach((tr, i) => {
    tr.classList.toggle("cur", q.on && i === q.i);
    tr.querySelectorAll("input,select,button").forEach(x => x.disabled = q.on || !local || (x.dataset.k === "rpm" && seqDraft[i] && seqDraft[i].dir === "stop"));
  });
  ["seqAdd", "seqSave", "seqLoop"].forEach(id => $(id).disabled = q.on || !local);
  $("seqPlay").disabled = q.on || !can || d.fault;
  $("seqStop").disabled = !q.on || !local;
}

function setNode(id, state, text) {
  const n = $(id);
  n.className = "node " + state;
  n.querySelector(".st").textContent = (state === "ok" ? "● " : state === "bad" ? "✕ " : "") + text;
}
function setLink(id, up) { $(id).classList.toggle("down", !up); }

function renderNetwork(s) {
  const L = s.links, m = s.mpb, d = s.drive;
  // the cable to the SIG300 is proven only by its EtherNet/IP connection
  const path = L.eip, known = L.fresh;
  const plcState = L.plc ? (L.fresh ? "ok" : "bad") : "bad", plcText = L.plc ? (L.fresh ? "Running" : "Heartbeat stopped") : "No response";
  setNode("n-plc", plcState, plcText); setNode("n-plc2", plcState, plcText); setNode("n-plc3", plcState, plcText);
  // the NOC is proven by any device it scans; with neither connected it can't be told apart from a cabling fault
  const nocOk = L.eip || L.atv;
  ["n-noc", "n-noc2"].forEach(id => setNode(id, !known ? "" : nocOk ? "ok" : "", !known ? "–" : nocOk ? "Scanning" : "Check module"));
  setLink("l-bp", !known || nocOk); setLink("l-bp2", !known || nocOk);
  setLink("l-sig", !known || path);
  setNode("n-sig", !known ? "" : path ? "ok" : "bad", !known ? "–" : path ? "Connected" : "Unreachable");
  setLink("l-iol", !path || L.iolink);
  setNode("n-mpb", !path ? "" : L.iolink ? "ok" : "bad", !path ? "–" : L.iolink ? "Sending data" : "Not detected");
  $("l-sig").querySelector("small").innerHTML = !known || path ? "EtherNet/IP · NOC ETH3<br>Cat6 copper" : "NO DATA PAST HERE<br>check the cable";
  // drive path
  setLink("l-atv", !known || L.atv);
  setNode("n-atv", !known ? "" : L.atv ? (d.fault ? "bad" : "ok") : "bad", !known ? "–" : !L.atv ? "Unreachable" : d.fault ? "Fault" : d.running ? "Running" : "Ready");
  setLink("l-mot", true);
  // remote I/O path: CPU -> NRP -> two fibers -> NRP -> CRA; only the drop's health and the ring status
  // come back, so with the drop lost the NRPs say "check" rather than guess which piece failed
  const P = s.panel || {};
  setLink("l-cu1", !known || L.drop); setLink("l-cu2", !known || L.drop);
  ["n-nrp1", "n-nrp2"].forEach(id => setNode(id, !known ? "" : L.drop ? "ok" : "", !known ? "–" : L.drop ? "Passing data" : "Check link LEDs"));
  setLink("l-ring", !known || L.drop);
  $("l-ring").classList.toggle("half", known && L.drop && P.ring_known && !P.ring);
  $("l-ring").querySelector("small").innerHTML = !known || !L.drop ? (known ? "NO DATA PAST HERE<br>check both fibers" : "RIO main ring<br>2 fibers")
    : P.ring_known && !P.ring ? "RING OPEN<br>running on 1 fiber" : "RIO main ring<br>2 fibers";
  setNode("n-cra", !known ? "" : L.drop ? "ok" : "bad", !known ? "–" : L.drop ? "Drop OK" : "Drop lost");
  setNode("n-mot", !L.atv ? "" : d.running ? "ok" : "", !L.atv ? "–" : d.running ? `${Math.round(d.speed)} rpm` : "Stopped");
  // SIG300 card + raw bytes
  $("p1led").className = "led " + (L.iolink ? "g" : path ? "r" : "");
  $("p1dev").textContent = L.iolink ? "SICK MPB10" : path ? "No device" : "–";
  $("p1sub").textContent = L.iolink ? `IO-Link · ${fmt(m.v)} mm/s · ${fmt(m.temp, 1)} °C` : path ? "Plug in a sensor" : "SIG300 unreachable";
  $("kvEip").innerHTML = known ? (path ? `<span class="led g"></span>Connected` : `<span class="led r"></span>Lost`) : "–";
  $("kvIol").innerHTML = path ? (L.iolink ? `<span class="led g"></span>Operate` : `<span class="led r"></span>No device`) : "–";
  const raw = m.raw, groups = [0, 0, 3, 3, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 3, 3, 3, 3, 3, 3];
  $("hex").innerHTML = Array.from({ length: 20 }, (_, i) =>
    `<div class="${raw ? "g" + groups[i] : ""}">${raw ? raw[i].toString(16).toUpperCase().padStart(2, "0") : "··"}<small>${i}</small></div>`).join("");
  $("dAlarm").textContent = m.alarm_word == null ? "–" : hex4(m.alarm_word) +
    ` (${[m.warn && "warning", m.alarm && "alarm", m.shock && "shock", m.temp_warn && "temp"].filter(Boolean).join(", ") || "no alarms"})`;
  $("dV").textContent = m.v == null ? "–" : `${m.v.toFixed(2)} mm/s`;
  $("dT").textContent = m.temp == null ? "–" : `${m.temp.toFixed(1)} °C`;
  $("dHb").textContent = s.plc.heartbeat ?? "–";
  $("dRate").textContent = s.plc.rate;
  $("dErr").textContent = s.plc.errors + (s.plc.error ? ` · ${s.plc.error}` : "");
  $("rawNote").textContent = s.mode === "sim"
    ? "Simulation: byte layout is illustrative. Live mode shows the real bytes the M580 received from the SIG300."
    : "Live bytes as copied by the M580 from the SIG300 input assembly.";
}

function renderRacks(s) {   // the board drawing at the top of the network tab
  const L = s.links, P = s.panel || {}, known = L.fresh, live = known && L.drop;
  const G = "#2E9E45", R = "#C8322B", OFF = "#8A8F94", CH = "#3A3B39", FB = "#1F9CAD";
  const fill = (id, c) => $(id).setAttribute("fill", c);
  fill("rk-cpu", L.plc ? (L.fresh ? G : R) : R);
  fill("rk-noc", !known ? OFF : L.eip || L.atv ? G : R);
  fill("rk-cra", !known ? OFF : L.drop ? G : R);
  const cable = (id, ok) => { $(id).setAttribute("stroke", ok ? (id.startsWith("rk-r") ? FB : CH) : R); $(id).setAttribute("stroke-dasharray", ok ? "" : "7 5"); };
  // which fiber is out isn't reported, so an open ring is drawn on the ETH3 side
  const ringOpen = live && P.ring_known && !P.ring;
  cable("rk-r2", !known || L.drop);
  cable("rk-r3", !known || (L.drop && !ringOpen));
  $("rk-ringt").textContent = !known ? "RIO ring over fiber" : !L.drop ? "RIO ring: drop lost" : ringOpen ? "RIO ring OPEN: 1 fiber" : "RIO ring: 2 fibers";
  $("rk-ringt").setAttribute("fill", !known ? "#146F7C" : !L.drop ? R : ringOpen ? "#9A6100" : G);
  cable("rk-atv", !known || L.atv);
  cable("rk-sig", !known || L.eip);
  $("rk-atvt").textContent = !known ? "" : L.atv ? "· connected" : "· no connection";
  $("rk-sigt").textContent = !known ? "" : L.eip ? "· connected" : "· no data";
  $("rk-dropn").textContent = `drop ${cfg.cra_drop ?? 1}`;
  $("rk-dropt").textContent = !known ? "" : L.drop ? "· drop OK" : "· drop lost";
  // the program's stack-light state: no lamps on this board, so it doesn't depend on the drop
  fill("rk-lg", known && P.green ? "#2EBD4E" : "#2f4a35");
  fill("rk-la", known && P.amber ? "#F5A623" : "#5a4c30");
  fill("rk-lr", known && P.red ? "#E53935" : "#5a3a38");
}

function renderReach(r) {
  for (const k of ["plc_host", "noc_host", "cra_host", "sig300_host", "atv_host", "ap_host"]) {
    const x = r[k];
    const field = ["noc_host", "sig300_host", "atv_host", "ap_host"].includes(k);
    if (field && x && !x.ok && !sameNet(cfg[k], cfg.laptop_ip, cfg.netmask)) {
      $("r-" + k).innerHTML = `<span class="led"></span>Not from here (device network: the PLC’s health bits show it)`; continue;
    }
    $("r-" + k).innerHTML = !x ? "checking…" : x.ok ? `<span class="led g"></span>Yes · ${x.ms} ms (TCP ${x.port})`
      : `<span class="led r"></span>No answer on TCP ${x.port}`;
  }
  const mine = r.local || [];
  const match = mine.includes(cfg.laptop_ip);
  $("r-laptop").innerHTML = !mine.length ? "–" : (match ? `<span class="led g"></span>This laptop has ${cfg.laptop_ip}`
    : `<span class="led y"></span>This laptop has ${mine.join(", ")}`);
}

// ------------------------------------------------------------ charts
function chart(canvasId, series, left, right, limits, marks = []) {
  const c = $(canvasId);
  const W = c.clientWidth, H = 300, dpr = window.devicePixelRatio || 1;
  if (!W) return;
  c.width = W * dpr; c.height = H * dpr;
  const g = c.getContext("2d");
  g.scale(dpr, dpr);
  const pl = 48, pr = 52, pt = 10, pb = 26, w = W - pl - pr, h = H - pt - pb;
  const now = last ? last.t : Date.now() / 1000, t0 = now - WINDOW;
  const pts = hist.filter(p => p[0] >= t0);
  const X = t => pl + (t - t0) / WINDOW * w;
  const Y = (v, ax) => pt + h - (v - ax.min) / (ax.max - ax.min) * h;
  g.font = "12px Segoe UI, Arial"; g.fillStyle = "#5F646A"; g.strokeStyle = "#ECEEF0"; g.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pt + h * i / 4;
    g.beginPath(); g.moveTo(pl, y); g.lineTo(pl + w, y); g.stroke();
    g.textAlign = "right"; g.fillText((left.max - (left.max - left.min) * i / 4).toFixed(left.dp ?? 1), pl - 6, y + 4);
    g.textAlign = "left"; g.fillText(Math.round(right.max - (right.max - right.min) * i / 4) + right.unit, pl + w + 6, y + 4);
  }
  g.textAlign = "center";
  for (let s = 0; s <= WINDOW; s += 30) g.fillText(s === WINDOW ? "now" : `−${WINDOW - s}s`, X(t0 + s), H - 6);
  for (const [lim, col] of limits) {
    if (lim >= left.max) continue;
    g.strokeStyle = col; g.setLineDash([6, 5]); g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(pl, Y(lim, left)); g.lineTo(pl + w, Y(lim, left)); g.stroke();
  }
  // speed-change markers: dashed line + label, labels staggered so close ones don't overlap
  g.font = "600 11px Segoe UI, Arial"; g.textAlign = "left";
  let row = 0, lastX = -1e9;
  for (const [t, label] of marks) {
    if (t < t0 || t > now) continue;
    const x = X(t);
    row = x - lastX < 70 ? (row + 1) % 3 : 0; lastX = x;
    g.strokeStyle = "rgba(240,106,42,.75)"; g.setLineDash([4, 4]); g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(x, pt); g.lineTo(x, pt + h); g.stroke();
    const tw = g.measureText(label).width, ty = pt + 4 + row * 15;
    g.fillStyle = "rgba(255,255,255,.9)"; g.fillRect(x + 3, ty, tw + 6, 14);
    g.fillStyle = "#B84A14"; g.fillText(label, x + 6, ty + 11);
  }
  g.font = "12px Segoe UI, Arial"; g.fillStyle = "#5F646A";
  g.setLineDash([]);
  for (const [idx, ax, col, width] of series) {
    g.strokeStyle = col; g.lineWidth = width; g.beginPath();
    let on = false, prevT = null;
    for (const p of pts) {
      if (p[idx] == null) { on = false; continue; }
      if (prevT != null && p[0] - prevT > 2) on = false;   // gap = data was missing
      const x = X(p[0]), y = Y(Math.max(ax.min, Math.min(ax.max, p[idx])), ax);
      on ? g.lineTo(x, y) : g.moveTo(x, y);
      on = true; prevT = p[0];
    }
    g.stroke();
  }
}
function speedAxis(pts) {   // 0..max, or -max..max once the motor has run in reverse
  const top = Math.ceil(Math.max(cfg.max_rpm || 1500, ...pts.map(p => Math.abs(p[6] || 0))) * 1.1 / 200) * 200;
  return { min: pts.some(p => (p[6] || 0) < -1 || (p[7] || 0) < 0) ? -top : 0, max: top, dp: 0, unit: "" };
}
function drawCharts() {
  const pts = hist.filter(p => last && p[0] >= last.t - WINDOW);
  const vax = { min: 0, max: Math.max((cfg.zone_cd || 4.5) * 1.5, ...pts.map(p => (p[1] || 0) * 1.1)), dp: 1 };
  if ($("tab-machine").classList.contains("on"))
    chart("trend", [[5, { min: 0, max: 100 }, "#F06A2A", 2], [1, vax, "#1F9CAD", 3]], vax, { min: 0, max: 100, unit: "°" },
      [[cfg.zone_bc, "#D98A00"], [cfg.zone_cd, "#C8322B"]]);
  if ($("tab-vfd").classList.contains("on")) {
    const sax = speedAxis(pts);
    chart("vTrend", [[7, sax, "#9AA0A6", 2], [6, sax, "#5B5BD6", 3]], sax, sax, [], (last && last.marks) || []);
  }
  if ($("tab-drive").classList.contains("on")) {
    const sax = speedAxis(pts);
    chart("dTrend", [[6, sax, "#5B5BD6", 2.5], [1, vax, "#1F9CAD", 3]], vax, Object.assign({ unit: "" }, sax), [[cfg.zone_cd, "#C8322B"]], (last && last.marks) || []);
  }
}
window.addEventListener("resize", drawCharts);

// ------------------------------------------------------------ start
(async () => {
  applyCfg(await fetch("/api/config").then(r => r.json()));
  hist = await fetch("/api/history").then(r => r.json());
  const es = new EventSource("/api/stream");
  es.onmessage = e => render(JSON.parse(e.data));
  es.onerror = () => { $("modePill").textContent = "DASHBOARD SERVER STOPPED"; $("modePill").className = "pill"; };
})();
