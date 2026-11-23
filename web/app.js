/* JAAL front-end — vanilla JS, no external libraries (works offline / air-gapped). */
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtRs = n => "Rs " + Number(n || 0).toLocaleString("en-IN");

const ROLE_COLOR = {
  "Suspected orchestrator": "#ff3b5c", "Financial controller": "#ffb020", "Broker / intermediary": "#ff7a45",
  "Money mule": "#f5d742", "Field operative": "#b37feb", "Associate": "#5b8ff9",
  "Victim / complainant": "#36cfc9", "Probable victim (unreported)": "#2aa198",
  "Benign hub (likely)": "#8c8c8c", "Peripheral": "#3d4a5c",
};
const TYPE_COLOR = { P: "#5b8ff9", PH: "#36cfc9", AC: "#ffb020", ORG: "#ff7a45", VH: "#b37feb", CASE: "#ff3b5c",
  LOC: "#8c8c8c", SM: "#69c0ff", IMEI: "#f5d742", GRP: "#95de64", U: "#ff85c0" };
const TYPE_NAME = { P: "Person", PH: "Phone", AC: "Account", ORG: "Organisation", VH: "Vehicle", CASE: "Case",
  LOC: "Camera", SM: "Social handle", IMEI: "Handset", GRP: "Group", U: "Unattributed" };
const PALETTE = ["#5b8ff9","#ff7a45","#3ddc97","#f5d742","#b37feb","#36cfc9","#ff85c0","#ffb020","#69c0ff","#95de64","#ff3b5c","#a0d911","#9254de","#13c2c2"];
const PART_COLOR = { ppr: "#4cc3ff", betweenness: "#ff7a45", eigenvector: "#b37feb", disruption: "#ff3b5c", flags: "#ffb020" };
const PART_NAME = { ppr: "Case proximity", betweenness: "Brokerage", eigenvector: "Influence", disruption: "Disruption", flags: "Red flags" };

const S = { token: localStorage.getItem("jaal_token") || "tok-analyst", user: null, users: [], view: "person",
  colorBy: "role", noiOnly: false, selected: null, pg: null, tab: "targets", pathSel: null, highlight: null };

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "X-JAAL-Token": S.token, "Content-Type": "application/json", ...(opts.headers || {}) } });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), { status: r.status, body: j });
  return j;
}
const can = p => S.user && S.user.perms.includes(p);

/* ======================================================================== GRAPH */
class Graph {
  constructor(canvas) {
    this.c = canvas; this.ctx = canvas.getContext("2d");
    this.nodes = []; this.edges = []; this.byId = {}; this.scale = 1; this.ox = 0; this.oy = 0;
    this.alpha = 0; this.hover = null; this.drag = null; this.pan = null;
    this.resize(); window.addEventListener("resize", () => this.resize());
    canvas.addEventListener("mousedown", e => this.down(e));
    window.addEventListener("mousemove", e => this.move(e));
    window.addEventListener("mouseup", e => this.up(e));
    canvas.addEventListener("wheel", e => this.wheel(e), { passive: false });
    this.loop = this.loop.bind(this); requestAnimationFrame(this.loop);
  }
  resize() {
    const r = this.c.getBoundingClientRect(), d = window.devicePixelRatio || 1;
    this.c.width = r.width * d; this.c.height = r.height * d; this.dpr = d; this.w = r.width; this.h = r.height; this.dirty = true;
  }
  set(nodes, edges, opts = {}) {
    const old = this.byId; this.byId = {};
    this.nodes = nodes.map((n, i) => {
      const o = old[n.id]; const a = (i / nodes.length) * Math.PI * 2, rr = 120 + (i % 7) * 25;
      const nn = { ...n, x: o ? o.x : Math.cos(a) * rr, y: o ? o.y : Math.sin(a) * rr, vx: 0, vy: 0 };
      this.byId[n.id] = nn; return nn;
    });
    this.edges = edges.filter(e => this.byId[e.u] && this.byId[e.v]).map(e => ({ ...e, s: this.byId[e.u], t: this.byId[e.v] }));
    this.maxW = Math.max(1, ...this.edges.map(e => e.weight || e.count || 1));
    this.radius = opts.radius; this.color = opts.color; this.label = opts.label;
    this.alpha = 1;
    const budget = performance.now() + 700;  // settle the layout up-front so it never depends on animation frames
    while (this.alpha > 0.03 && performance.now() < budget) this.tick();
    this.fit(); this.fitPending = false; this.dirty = true;
  }
  tick() {
    const N = this.nodes, k = 2400, L = 70;
    for (let i = 0; i < N.length; i++) {
      const a = N[i];
      for (let j = i + 1; j < N.length; j++) {
        const b = N[j]; let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy + 0.01;
        if (d2 > 250000) continue;
        const f = k / d2 * this.alpha; const d = Math.sqrt(d2);
        dx /= d; dy /= d; a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
      }
      a.vx -= a.x * 0.004 * this.alpha; a.vy -= a.y * 0.004 * this.alpha;
    }
    for (const e of this.edges) {
      const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01;
      const w = Math.log1p(e.weight || e.count || 1) / Math.log1p(this.maxW);
      const f = (d - L * (1.4 - w * 0.7)) * 0.03 * (0.3 + w) * this.alpha;
      const fx = dx / d * f, fy = dy / d * f;
      e.s.vx += fx; e.s.vy += fy; e.t.vx -= fx; e.t.vy -= fy;
    }
    for (const n of N) { if (n === this.drag) continue; n.vx *= 0.6; n.vy *= 0.6; n.x += n.vx; n.y += n.vy; }
    this.alpha *= 0.985; this.dirty = true;
  }
  fit() {
    if (!this.nodes.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of this.nodes) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const pad = 70; this.scale = Math.min((this.w - pad * 2) / (x1 - x0 + 1), (this.h - pad * 2 - 40) / (y1 - y0 + 1), 2.2);
    this.ox = this.w / 2 - (x0 + x1) / 2 * this.scale; this.oy = this.h / 2 + 15 - (y0 + y1) / 2 * this.scale; this.dirty = true;
  }
  loop() {
    if (this.alpha > 0.02) {
      for (let i = 0; i < 3; i++) this.tick();
      if (this.fitPending && this.alpha < 0.5) this.fit();
      if (this.alpha <= 0.02) { if (this.fitPending) this.fit(); this.fitPending = false; }
    }
    if (this.dirty) this.draw();
    requestAnimationFrame(this.loop);
  }
  toWorld(e) { const r = this.c.getBoundingClientRect(); return { x: (e.clientX - r.left - this.ox) / this.scale, y: (e.clientY - r.top - this.oy) / this.scale, sx: e.clientX - r.left, sy: e.clientY - r.top }; }
  pick(p) {
    let best = null, bd = Infinity;
    for (const n of this.nodes) { const d = Math.hypot(n.x - p.x, n.y - p.y), r = this.radius(n) + 3 / this.scale; if (d < r && d < bd) { best = n; bd = d; } }
    return best;
  }
  down(e) { const p = this.toWorld(e), n = this.pick(p); if (n) { this.drag = n; this.moved = false; } else this.pan = { x: e.clientX, y: e.clientY, ox: this.ox, oy: this.oy }; }
  move(e) {
    if (this.drag) { const p = this.toWorld(e); this.drag.x = p.x; this.drag.y = p.y; this.moved = true; this.dirty = true; return; }
    if (this.pan) { this.ox = this.pan.ox + e.clientX - this.pan.x; this.oy = this.pan.oy + e.clientY - this.pan.y; this.dirty = true; return; }
    if (e.target !== this.c) return;
    const p = this.toWorld(e), n = this.pick(p);
    if (n !== this.hover) { this.hover = n; this.dirty = true; }
    const tip = $("#tip");
    if (n) { tip.style.display = "block"; tip.style.left = (p.sx + 14) + "px"; tip.style.top = (p.sy + 10) + "px"; tip.innerHTML = this.onTip ? this.onTip(n) : esc(n.label); }
    else tip.style.display = "none";
  }
  up(e) { if (this.drag && !this.moved && this.onClick) this.onClick(this.drag); if (this.drag && this.moved) this.fitPending = false; this.drag = null; this.pan = null; }
  wheelStop() { this.fitPending = false; }
  wheel(e) {
    e.preventDefault(); this.fitPending = false; const p = this.toWorld(e), f = Math.exp(-e.deltaY * 0.0015);
    this.scale *= f; this.ox = p.sx - p.x * this.scale; this.oy = p.sy - p.y * this.scale; this.dirty = true;
  }
  draw() {
    this.dirty = false; const g = this.ctx, d = this.dpr;
    g.setTransform(d, 0, 0, d, 0, 0); g.clearRect(0, 0, this.w, this.h);
    g.translate(this.ox, this.oy); g.scale(this.scale, this.scale);
    const hl = S.highlight, sel = S.selected, nb = new Set();
    if (sel) for (const e of this.edges) { if (e.u === sel) nb.add(e.v); if (e.v === sel) nb.add(e.u); }
    const hlEdges = new Set();
    if (hl) for (let i = 0; i < hl.length - 1; i++) hlEdges.add([hl[i], hl[i + 1]].sort().join("|"));
    for (const e of this.edges) {
      const w = Math.log1p(e.weight || e.count || 1) / Math.log1p(this.maxW);
      const key = [e.u, e.v].sort().join("|"); const onPath = hlEdges.has(key);
      const focus = sel && (e.u === sel || e.v === sel);
      g.strokeStyle = onPath ? "#ffb020" : focus ? "rgba(76,195,255,.75)" : (sel || hl) ? "rgba(90,110,140,.10)" : "rgba(110,130,165,.28)";
      g.lineWidth = (onPath ? 3.5 : 0.6 + w * 2.6) / Math.sqrt(this.scale);
      g.beginPath(); g.moveTo(e.s.x, e.s.y); g.lineTo(e.t.x, e.t.y); g.stroke();
      if (e.channels && e.channels.transfers && (focus || onPath || (!sel && !hl))) {
        g.fillStyle = "rgba(255,176,32,.8)"; const mx = (e.s.x + e.t.x) / 2, my = (e.s.y + e.t.y) / 2;
        g.beginPath(); g.arc(mx, my, 1.8 / Math.sqrt(this.scale) + 0.8, 0, 7); g.fill();
      }
    }
    const hlSet = new Set(hl || []);
    for (const n of this.nodes) {
      const r = this.radius(n), dim = (sel && n.id !== sel && !nb.has(n.id)) || (hl && !hlSet.has(n.id) && n.id !== sel);
      g.globalAlpha = dim ? 0.22 : 1;
      g.fillStyle = this.color(n); g.beginPath(); g.arc(n.x, n.y, r, 0, 7); g.fill();
      if (n.accused) { g.strokeStyle = "#fff"; g.lineWidth = 1.4 / this.scale; g.stroke(); }
      if (n.ghost) { g.strokeStyle = "#ff3b5c"; g.setLineDash([3, 2]); g.lineWidth = 1.5 / this.scale; g.stroke(); g.setLineDash([]); }
      if (n.id === sel || hlSet.has(n.id)) { g.strokeStyle = "#ffb020"; g.lineWidth = 3 / this.scale; g.beginPath(); g.arc(n.x, n.y, r + 3 / this.scale, 0, 7); g.stroke(); }
      const showLabel = n.id === sel || n === this.hover || hlSet.has(n.id) || nb.has(n.id) || (!sel && !hl && this.label(n));
      if (showLabel) {
        g.font = `${(n.id === sel ? 12.5 : 11) / Math.min(this.scale, 1.6)}px -apple-system,Segoe UI,sans-serif`;
        g.fillStyle = dim ? "rgba(215,222,234,.3)" : "#e6ecf5"; g.textAlign = "center";
        g.fillText(n.label, n.x, n.y - r - 4 / this.scale);
      }
      g.globalAlpha = 1;
    }
  }
}

/* ======================================================================== GRAPH VIEWS */
const G = new Graph($("#graph"));
G.onClick = n => openEntity(n.id);
G.onTip = n => {
  if (S.view === "person") return `<b>${esc(n.label)}</b><br><span class="muted">${esc(n.role)} · #${n.rank} · score ${n.score.toFixed(2)}</span>${n.city ? "<br>" + esc(n.city) : ""}`;
  return `<b>${esc(n.label)}</b><br><span class="muted">${TYPE_NAME[n.type] || n.type}${n.ghost ? " · suspected fake identity" : ""}</span>`;
};

function colorFor(n) {
  if (S.view === "kg") return TYPE_COLOR[n.type] || "#888";
  if (S.colorBy === "community") return PALETTE[n.community % PALETTE.length];
  if (S.colorBy === "city") return PALETTE[(["Mumbai","Hyderabad","Kolkata","Pune","Delhi"].indexOf(n.city) + 14) % 14] || "#666";
  return ROLE_COLOR[n.role] || "#5b8ff9";
}
function legend() {
  const L = $("#legend"); let items = [];
  if (S.view === "kg") items = Object.entries(TYPE_NAME).map(([k, v]) => [TYPE_COLOR[k], v]);
  else if (S.colorBy === "role") items = Object.entries(ROLE_COLOR);
  else if (S.colorBy === "city") items = ["Mumbai","Hyderabad","Kolkata","Pune","Delhi"].map((c, i) => [PALETTE[(i + 14) % 14], c]);
  else items = [["#888", "Louvain community (colour per cluster)"]];
  if (S.view === "kg") L.innerHTML = items.map(([c, t]) => `<span><i style="background:${c}"></i>${t}</span>`).join("") + `<span><i style="border:1.5px dashed #ff3b5c"></i>fake identity</span>`;
  else L.innerHTML = items.map(([a, b]) => S.colorBy === "role" ? `<span><i style="background:${b}"></i>${a}</span>` : `<span><i style="background:${a}"></i>${b}</span>`).join("") + `<span><i style="border:1.5px solid #fff"></i>FIR-accused</span><span>● on edge = money</span>`;
}

async function loadPersonGraph() {
  S.view = "person"; setSeg();
  if (!S.pg) S.pg = await api("/api/graph");
  let nodes = S.pg.nodes, ids;
  if (S.noiOnly) { nodes = nodes.filter(n => n.noi); }
  ids = new Set(nodes.map(n => n.id));
  const edges = S.pg.edges.filter(e => ids.has(e.u) && ids.has(e.v));
  G.set(nodes, edges, { radius: n => 4 + 15 * n.score, color: colorFor, label: n => n.rank <= 12 });
  $("#stats").textContent = `${nodes.length} people · ${edges.length} relationships`;
  legend();
}
async function loadKG(focus) {
  S.view = "kg"; setSeg();
  const d = await api("/api/kg" + (focus ? `?focus=${encodeURIComponent(focus)}&depth=2` : ""));
  const radius = n => n.type === "P" ? 5 + 10 * (n.score || 0) : n.type === "CASE" ? 9 : n.type === "ORG" ? 7 : n.type === "IMEI" ? 6 : 4;
  G.set(d.nodes, d.edges.map(e => ({ ...e, weight: e.count })), { radius, color: colorFor, label: n => n.type === "CASE" || n.type === "ORG" || n.type === "IMEI" || n.id === focus || (n.score || 0) > 0.45 });
  $("#stats").textContent = `${d.nodes.length} entities · ${d.edges.length} edges` + (focus ? ` · 2 hops around ${focus}` : "");
  legend();
}
function setSeg() { document.querySelectorAll("#viewseg button").forEach(b => b.classList.toggle("on", b.dataset.v === S.view)); }

/* ======================================================================== DOSSIER */
function evChips(evs) {
  return (evs || []).filter(e => e && e.doc).map(e => `<span class="evlink" data-doc="${esc(e.doc)}" data-loc="${esc(e.loc)}" title="${esc(e.text)}">📄 ${esc(e.doc.replace("firs/", ""))}:${esc(e.loc)}</span>`).join("");
}
function roleChip(r) { return `<span class="role" style="background:${ROLE_COLOR[r] || "#5b8ff9"}">${esc(r)}</span>`; }

async function openEntity(id) {
  S.selected = id; G.dirty = true;
  const box = $("#dossier");
  if (!can("entity")) {
    const n = (S.pg?.nodes || []).find(x => x.id === id);
    box.innerHTML = `<h2>${esc(n?.label || id)}</h2>${n ? roleChip(n.role) : ""}<div class="denied">Your role (<b>${S.user.role}</b>) cannot open full dossiers or source documents.<br>Access attempt was recorded in the audit trail.</div>`;
    api("/api/entity/" + encodeURIComponent(id)).catch(() => {});
    return;
  }
  box.innerHTML = `<div class="empty">Loading…</div>`;
  let d;
  try { d = await api("/api/entity/" + encodeURIComponent(id)); } catch (e) { box.innerHTML = `<div class="denied">${esc(e.message)}</div>`; return; }
  if (d.type !== "Person" && d.type !== "Unattributed phone") return renderArtefact(d);
  const sc = d.score, role = d.role;
  let h = `<div class="row"><h2>${esc(d.label)}</h2><span class="score">#${sc.rank} · ${sc.total.toFixed(3)}</span></div>
    <div>${roleChip(role.role)} ${d.ghost ? '<span class="role" style="background:#ff3b5c">FAKE IDENTITY</span>' : ""} ${d.noi ? '<span class="chip">network of interest</span>' : ""} <span class="chip">community ${d.community}</span></div>
    <p class="small">${esc(role.why)}</p>
    <div class="kv"><div>Aliases</div><div>${esc(d.aliases.join(", ") || "—")}</div><div>DOB</div><div>${esc(d.dob || "—")}</div>
    <div>City</div><div>${esc(d.city || "—")}</div><div>IDs</div><div>${esc(d.ids.join(", ") || "—")}</div>
    <div>Phones</div><div>${d.assets.phones.map(p => `<span class="pill">${esc(p)}</span>`).join(" ") || "—"}</div>
    <div>Accounts</div><div>${d.assets.accounts.map(p => `<span class="pill">…${esc(p.slice(-4))}</span>`).join(" ") || "—"}</div>
    <div>Vehicles</div><div>${d.assets.vehicles.map(p => `<span class="pill">${esc(p)}</span>`).join(" ") || "—"}</div>
    <div>Handles</div><div>${esc(d.assets.handles.join(", ") || "—")}</div>
    <div>Named in</div><div>${role.named_in.map(([c, r]) => `<span class="chip">${esc(c)} · ${esc(r)}</span>`).join("") || "never named in an FIR"}</div>
    <div>Money</div><div>in ${fmtRs(d.money.in)} · out ${fmtRs(d.money.out)}</div></div>
    <div class="sect"><h3>Why this priority score</h3>`;
  for (const k of Object.keys(sc.parts)) {
    const w = { ppr: .25, betweenness: .25, eigenvector: .15, disruption: .2, flags: .15 }[k];
    h += `<div class="bar"><span>${PART_NAME[k]}</span><div class="track"><div class="fill" style="width:${(sc.parts[k] / w * 100).toFixed(0)}%;background:${PART_COLOR[k]}"></div></div><span class="score">+${sc.parts[k].toFixed(3)}</span></div><div class="explain">${esc(sc.explain[k])}</div>`;
  }
  h += `</div><div class="sect"><h3>Activity timeline</h3><div id="tl"></div></div>`;
  if (d.inferences.length) h += `<div class="sect"><h3>Inferred attributions</h3>` + d.inferences.map(i => `<div class="card sev3"><b>☎ ${esc(i.phone)}</b> → ${esc(i.attributed_label)} <span class="muted">(confidence ${i.confidence})</span><div class="small">${esc(i.reason)}</div>${evChips(i.evidence)}</div>`).join("") + `</div>`;
  if (d.alerts.length) h += `<div class="sect"><h3>Alerts (${d.alerts.length})</h3>` + d.alerts.map(a => `<div class="card sev${a.severity}"><b>${esc(a.title)}</b><div class="small muted">${esc(a.detail)}</div>${evChips(a.evidence)}</div>`).join("") + `</div>`;
  h += `<div class="sect"><h3>Strongest connections</h3><table><tr><th>Person</th><th>Channels</th><th>Evidence</th></tr>` +
    d.connections.map(c => `<tr class="click" data-id="${esc(c.id)}"><td><b>${esc(c.label)}</b><br><span class="muted small">${esc(c.role)}</span></td><td>${Object.entries(c.channels).map(([k, v]) => `${k.replace("_", "-")} ×${v}`).join("<br>")}${c.amount ? "<br>" + fmtRs(c.amount) : ""}</td><td>${evChips(c.evidence.slice(0, 2))}</td></tr>`).join("") + `</table></div>`;
  h += `<div class="sect"><h3>Source records merged (${d.records.length})</h3>` + d.records.map(r => `<div class="card"><span class="chip">${esc(r.source)}</span> <b>${esc(r.name)}</b><br>${evChips([r.evidence])}</div>`).join("");
  if (d.merges.length) h += `<details><summary class="muted small">Why these records were merged (${d.merges.length} decisions)</summary>` + d.merges.map(m => `<div class="small" style="margin:6px 0"><span class="pill">${esc(m.a)}</span> ≡ <span class="pill">${esc(m.b)}</span> <b>${m.score}</b> <span class="muted">${esc(m.mode)}</span><br><span class="muted">${esc(m.why.join(" · "))}</span></div>`).join("") + `</details>`;
  h += `</div><div class="sect row"><button id="btnKG">Expand in knowledge graph</button><button id="btnPathFrom">Find path from here…</button></div>`;
  box.innerHTML = h;
  box.querySelectorAll("tr.click").forEach(tr => tr.onclick = ev => { if (!ev.target.classList.contains("evlink")) openEntity(tr.dataset.id); });
  $("#btnKG").onclick = () => loadKG(id);
  $("#btnPathFrom").onclick = () => { S.pathSel = id; $("#pathbox").innerHTML = `Path from <b>${esc(d.label)}</b>: click another person… <button class="ghost" id="pcancel">cancel</button>`; $("#pcancel").onclick = clearPath; };
  api("/api/timeline/" + encodeURIComponent(id)).then(renderTimeline).catch(() => {});
}
function renderArtefact(d) {
  const a = d.attrs || {};
  $("#dossier").innerHTML = `<h2>${esc(d.label)}</h2><span class="chip">${esc(d.type)}</span>${a.ghost ? ' <span class="role" style="background:#ff3b5c">FAKE IDENTITY</span>' : ""}
    <div class="kv">${Object.entries(a).filter(([k, v]) => !["id", "label", "type", "evidence"].includes(k) && typeof v !== "object").map(([k, v]) => `<div>${esc(k)}</div><div>${esc(v)}</div>`).join("")}</div>
    <div class="sect"><h3>Relationships</h3><table>${d.edges.map(e => `<tr class="click" data-id="${esc(e.u === d.id ? e.v : e.u)}"><td><b>${esc(e.type)}</b> ×${e.count}${e.amount ? " · " + fmtRs(e.amount) : ""}</td><td>${esc(e.u_label)} → ${esc(e.v_label)}</td><td>${evChips(e.evidence.slice(0, 2))}</td></tr>`).join("")}</table></div>`;
  $("#dossier").querySelectorAll("tr.click").forEach(tr => tr.onclick = ev => { if (!ev.target.classList.contains("evlink")) openEntity(tr.dataset.id); });
}
function renderTimeline(t) {
  const W = 360, H = 110, n = t.days.length, bw = W / n;
  const mc = Math.max(1, ...t.calls), mm = Math.max(1, ...t.money_in, ...t.money_out);
  let s = `<svg class="svgtl" viewBox="0 0 ${W} ${H}">`;
  t.days.forEach((d, i) => {
    const h1 = t.calls[i] / mc * 50; s += `<rect x="${i * bw}" y="${55 - h1}" width="${bw - 1}" height="${h1}" fill="#4cc3ff"><title>${d}: ${t.calls[i]} calls</title></rect>`;
    const hi = t.money_in[i] / mm * 40, ho = t.money_out[i] / mm * 40;
    if (hi) s += `<rect x="${i * bw}" y="${60}" width="${bw - 1}" height="${hi}" fill="#3ddc97"><title>${d}: in ${fmtRs(t.money_in[i])}</title></rect>`;
    if (ho) s += `<rect x="${i * bw + bw / 2}" y="${60}" width="${bw / 2}" height="${ho}" fill="#ff7a45"><title>${d}: out ${fmtRs(t.money_out[i])}</title></rect>`;
  });
  for (const e of t.events) { const i = t.days.indexOf(e.day); if (i >= 0) s += `<line x1="${i * bw}" x2="${i * bw}" y1="0" y2="${H}" stroke="#ff3b5c" stroke-dasharray="2,2"/><text x="${i * bw + 2}" y="9" font-size="7" fill="#ff8da1">${esc(e.label.slice(0, 3))}</text>`; }
  s += `</svg><div class="small muted"><span style="color:#4cc3ff">■</span> calls/day &nbsp; <span style="color:#3ddc97">■</span> money in &nbsp; <span style="color:#ff7a45">■</span> money out &nbsp; <span style="color:#ff3b5c">┆</span> FIR reported · ${esc(t.days[0])} → ${esc(t.days[n - 1])}</div>`;
  const el = $("#tl"); if (el) el.innerHTML = s;
}
function clearPath() { S.pathSel = null; S.highlight = null; $("#pathbox").innerHTML = ""; G.dirty = true; }
async function showPath(a, b) {
  const r = await api(`/api/path?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  S.pathSel = null; S.highlight = r.path; G.dirty = true;
  if (!r.path.length) { $("#pathbox").innerHTML = "No path found. <button class='ghost' id='pcancel'>clear</button>"; $("#pcancel").onclick = clearPath; return; }
  $("#pathbox").innerHTML = `Path: <b>${r.labels.map(esc).join(" → ")}</b> (${r.hops.length} hops) <button class="ghost" id="pcancel">clear</button>`;
  $("#pcancel").onclick = clearPath;
  $("#dossier").innerHTML = `<h2>Connection path</h2><p class="muted small">Strongest-tie path (edge cost = 1 / (1 + ln(1 + interactions))). Every hop is backed by records:</p>` +
    r.hops.map((h, i) => `<div class="card"><b>${i + 1}. ${esc(h.from_label)} → ${esc(h.to_label)}</b><div class="small">${Object.entries(h.channels).map(([k, v]) => `${k} ×${v}`).join(", ")}${h.amount ? " · " + fmtRs(h.amount) : ""}</div>${evChips(h.evidence)}</div>`).join("");
}
const _origClick = G.onClick;
G.onClick = n => { if (S.pathSel && S.view === "person") { showPath(S.pathSel, n.id); return; } _origClick(n); };

/* ======================================================================== DOCUMENT VIEWER */
document.addEventListener("click", async e => {
  const t = e.target.closest(".evlink"); if (!t) return;
  e.stopPropagation();
  try {
    const d = await api(`/api/doc/${encodeURIComponent(t.dataset.doc)}?loc=${encodeURIComponent(t.dataset.loc)}`);
    const ln = parseInt((t.dataset.loc || "L1").slice(1)); const isCsv = d.kind !== "fir";
    const from = isCsv ? Math.max(1, ln - 8) : 1, to = isCsv ? Math.min(d.lines.length, ln + 8) : d.lines.length;
    let body = isCsv ? `<span class="ln">     1 </span>${esc(d.lines[0])}\n<span class="ln">   ··· </span>\n` : "";
    for (let i = from; i <= to; i++) { if (isCsv && i === 1) continue; const L = `<span class="ln">${String(i).padStart(6)} </span>${esc(d.lines[i - 1])}`; body += i === ln ? `<span class="hl">${L}</span>` : L + "\n"; }
    $("#mtitle").textContent = d.title; $("#mmeta").textContent = `${d.id} · line ${ln} of ${d.lines.length} · sha256 ${d.sha256.slice(0, 24)}…`;
    $("#mbody").innerHTML = body; $("#modal").classList.remove("hidden");
    const hl = $("#mbody .hl"); if (hl) hl.scrollIntoView({ block: "center" });
  } catch (err) { alert(err.status === 403 ? `Your role (${S.user.role}) may not open source documents. The attempt was logged.` : err.message); }
});
$("#mclose").onclick = () => $("#modal").classList.add("hidden");
$("#modal").onclick = e => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); };

/* ======================================================================== LEFT TABS */
const TABS = {
  async targets(p) {
    const t = await api("/api/targets?n=30");
    const s = await api("/api/summary");
    const kp = s.key_players;
    p.innerHTML = `<h3>Priority targets</h3><p class="small muted">Composite of case proximity, brokerage, influence, disruption impact and red flags — click for the full explanation.</p>` +
      t.map(x => `<div class="card click" data-id="${esc(x.id)}"><div class="row"><span class="rank">#${x.rank}</span><b style="flex:1">${esc(x.label)}</b><span class="score">${x.score.toFixed(3)}</span></div>
      <div class="row" style="margin-top:3px">${roleChip(x.role)}<span class="small muted">${esc(x.city)}${x.accused ? " · FIR-accused" : ""}</span></div>
      <div class="minibar">${Object.entries(x.parts).map(([k, v]) => `<span title="${PART_NAME[k]} +${v.toFixed(3)}" style="width:${v * 100}%;background:${PART_COLOR[k]}"></span>`).join("")}</div></div>`).join("") +
      (kp ? `<div class="card"><h4>Key-player set (greedy)</h4><div class="small">Removing <b>${kp.set.map(x => esc(x.label)).join(", ")}</b> leaves ${(kp.set[kp.set.length - 1].efficiency_left * 100).toFixed(0)}% of the network-of-interest's communication efficiency and splits it into ${kp.fragments_after} fragments.</div></div>` : "");
    p.querySelectorAll(".card.click").forEach(c => c.onclick = () => openEntity(c.dataset.id));
  },
  async alerts(p) {
    const a = await api("/api/alerts");
    const types = [...new Set(a.map(x => x.type))];
    p.innerHTML = `<h3>Behavioural alerts (${a.length})</h3><div id="afilt">${["all", ...types].map(t => `<span class="chip" data-t="${t}">${t}</span>`).join("")}</div><div id="alist" style="margin-top:8px"></div>`;
    const draw = t => {
      $("#alist").innerHTML = a.filter(x => t === "all" ? x.severity >= 2 : x.type === t).map(x => `<div class="card sev${x.severity}"><h4>${esc(x.title)}</h4><div class="small muted">${esc(x.detail)}</div><div>${x.entities.map((id, j) => `<span class="chip ent" data-id="${esc(id)}">${esc((x.entity_labels || [])[j] || id)}</span>`).join("")}</div>${evChips(x.evidence)}</div>`).join("") + (t === "all" ? `<p class="small muted">Showing severity ≥ 2. Pick a type to see all.</p>` : "");
      p.querySelectorAll(".chip.ent").forEach(c => c.onclick = () => openEntity(c.dataset.id));
    };
    p.querySelectorAll("#afilt .chip").forEach(c => c.onclick = () => draw(c.dataset.t)); draw("all");
  },
  async cases(p) {
    const d = await api("/api/cases");
    p.innerHTML = `<h3>Cross-case links</h3><p class="small muted">Structural path between case suspects (benign hubs & victims excluded) + modus-operandi overlap + narrative similarity.</p>` +
      d.links.map((l, i) => `<div class="card click" data-i="${i}"><div class="row"><b>${esc(l.a)} ⇄ ${esc(l.b)}</b><span class="strength-${l.strength}">${l.strength} · ${l.score}</span></div><div class="small">${esc(l.explain)}</div>${l.mo_overlap.length ? `<div>${l.mo_overlap.map(m => `<span class="chip">${esc(m)}</span>`).join("")}</div>` : ""}</div>`).join("") +
      `<h3 style="margin-top:14px">Cases</h3>` + d.cases.map(c => `<div class="card"><div class="row"><b>${esc(c.case_no)}</b><span class="evlink" data-doc="${esc(c.doc)}" data-loc="L1">📄 FIR</span></div><div class="small muted">${esc(c.station)} · ${esc(c.date)} · ${esc(c.sections)}</div><div>${c.mo.map(m => `<span class="chip">${esc(m)}</span>`).join("")}</div><div class="small" style="margin-top:4px">${c.people.map(x => `<span class="chip ent" data-id="${esc(x.id)}" title="${esc(x.why)}">${esc(x.label)}</span>`).join("")}</div></div>`).join("");
    p.querySelectorAll(".card.click").forEach(c => c.onclick = () => { const l = d.links[c.dataset.i]; if (S.view !== "person") loadPersonGraph(); S.highlight = l.path; S.selected = null; G.dirty = true; $("#pathbox").innerHTML = `${esc(l.a)} ⇄ ${esc(l.b)}: <b>${l.path_labels.map(esc).join(" → ")}</b> <button class="ghost" id="pcancel">clear</button>`; $("#pcancel").onclick = clearPath; });
    p.querySelectorAll(".chip.ent").forEach(c => c.onclick = e => { e.stopPropagation(); openEntity(c.dataset.id); });
  },
  async links(p) {
    const d = await api("/api/links");
    p.innerHTML = `<h3>Burner / fake-identity attributions</h3>` + d.inferences.map(i => `<div class="card sev3"><b>☎ ${esc(i.phone)}</b><div class="small">${esc(i.reason)}</div><span class="chip ent" data-id="${esc(i.attributed_to)}">→ ${esc(i.attributed_label)}</span>${evChips(i.evidence.slice(0, 3))}</div>`).join("") +
      `<h3 style="margin-top:14px">Predicted hidden links</h3><p class="small muted">Adamic–Adar on the network of interest: pairs with no direct record but many shared (rare) contacts — leads for CDR/bank requisitions.</p>` +
      d.predicted.map(x => `<div class="card click" data-a="${esc(x.a)}" data-b="${esc(x.b)}"><div class="row"><b>${esc(x.a_label)} ⋯ ${esc(x.b_label)}</b><span class="score">${x.score}</span></div><div class="small muted">${esc(x.explain)}</div></div>`).join("");
    p.querySelectorAll(".chip.ent").forEach(c => c.onclick = () => openEntity(c.dataset.id));
    p.querySelectorAll(".card.click").forEach(c => c.onclick = () => { S.highlight = [c.dataset.a, c.dataset.b]; S.selected = null; G.dirty = true; });
  },
  async er(p) {
    const d = await api("/api/er/review");
    const dec = can("er_decide");
    p.innerHTML = `<h3>Entity resolution</h3><p class="small">${d.merges.length} automatic merges (score ≥ 0.95). ${d.queue.length} candidate pairs need a human decision (0.65–0.95).${dec ? "" : " <span class='muted'>Only supervisors can decide.</span>"}</p>` +
      d.queue.map(x => `<div class="card sev2"><b>${esc(x.a_name)}</b> <span class="chip">${esc(x.a_src)}</span> ≟ <b>${esc(x.b_name)}</b> <span class="chip">${esc(x.b_src)}</span><div class="small muted">score ${x.score}: ${esc(x.why.join(" · "))}</div>${dec ? `<div class="row" style="margin-top:6px;justify-content:flex-start;gap:6px"><button class="primary" data-k="${esc(x.key)}" data-d="accept">Same person</button><button class="danger" data-k="${esc(x.key)}" data-d="reject">Different</button></div>` : ""}</div>`).join("") +
      (Object.keys(d.decisions).length ? `<h3>Decisions on record</h3>` + Object.entries(d.decisions).map(([k, v]) => `<div class="small"><span class="pill">${esc(v)}</span> ${esc(k)}</div>`).join("") : "") +
      `<details style="margin-top:10px"><summary class="small muted">Sample automatic merges</summary>${d.merges.slice(0, 40).map(m => `<div class="small" style="margin:5px 0"><span class="pill">${esc(m.a)}</span> ≡ <span class="pill">${esc(m.b)}</span> <b>${m.score}</b><br><span class="muted">${esc(m.why.join(" · "))}</span></div>`).join("")}</details>`;
    p.querySelectorAll("button[data-k]").forEach(b => b.onclick = async () => { b.disabled = true; await api("/api/er/decide", { method: "POST", body: JSON.stringify({ key: b.dataset.k, decision: b.dataset.d }) }); S.pg = null; await refreshAll(); });
  },
  async ingest(p) {
    if (!can("ingest")) { p.innerHTML = `<div class="denied">Your role cannot ingest new records.</div>`; return; }
    p.innerHTML = `<h3>Ingest a new FIR</h3><p class="small muted">Paste unstructured FIR text. JAAL extracts people, aliases, phones, accounts and vehicles, re-runs entity resolution and the full analysis, and reports any links to existing cases.</p>
      <button id="sample" class="ghost">Load sample FIR (Thane courier arrest)</button><textarea id="firtext" placeholder="FIR No: ..."></textarea>
      <div class="row"><button id="submitfir" class="primary">Ingest & re-analyse</button><span id="ingres" class="small"></span></div><div id="ingout"></div>`;
    $("#sample").onclick = () => $("#firtext").value = SAMPLE_FIR;
    $("#submitfir").onclick = async () => {
      $("#ingres").textContent = "Analysing…";
      try {
        const r = await api("/api/ingest/fir", { method: "POST", body: JSON.stringify({ text: $("#firtext").value }) });
        $("#ingres").textContent = `Ingested ${r.case} → ${r.file}`;
        $("#ingout").innerHTML = `<h3 style="margin-top:12px">Links from ${esc(r.case)}</h3>` + r.links.map(l => `<div class="card"><div class="row"><b>${esc(l.a)} ⇄ ${esc(l.b)}</b><span class="strength-${l.strength}">${l.strength} · ${l.score}</span></div><div class="small">${esc(l.explain)}</div></div>`).join("");
        S.pg = null; loadPersonGraph(); loadChain();
      } catch (e) { $("#ingres").textContent = "Error: " + e.message; }
    };
  },
  async audit(p) {
    if (!can("audit")) { p.innerHTML = `<div class="denied">Only supervisors and auditors can read the audit trail.<br>(This attempt has been logged.)</div>`; api("/api/audit").catch(() => {}); return; }
    const [v, rows] = await Promise.all([api("/api/audit/verify"), api("/api/audit?n=150")]);
    p.innerHTML = `<h3>Audit trail</h3><div class="card ${v.ok ? "" : "sev3"}"><b>${v.ok ? "✔ Hash chain intact" : "✖ Chain broken at entry " + v.broken_at}</b><div class="small muted">${v.entries} entries · ${v.ok ? "head " + v.head.slice(0, 20) + "…" : esc(v.reason)}</div></div>
      <table><tr><th>#</th><th>When / who</th><th>Action</th></tr>${rows.map(r => `<tr><td class="muted">${r.seq}</td><td>${esc(r.ts.slice(11))}<br><span class="muted">${esc(r.user)} (${esc(r.role)})</span></td><td><b class="${r.outcome === "denied" ? "strength-strong" : ""}">${esc(r.action)}</b> ${r.outcome === "denied" ? "<b class='strength-strong'>DENIED</b>" : ""}<br><span class="muted small">${esc(r.target)}</span><br><span class="pill" title="${esc(r.hash)}">${esc(r.hash.slice(0, 12))}</span></td></tr>`).join("")}</table>`;
  },
};
const SAMPLE_FIR = `FIRST INFORMATION REPORT
FIR No: THN/2026/0611    Police Station: Bhiwandi, Thane    State: Maharashtra
Date of Report: 2026-09-27    Date of Occurrence: 2026-09-26
Sections: BNS 318(4), 61(2); IT Act 66D

Complainant: PSI V. Kadam, Bhiwandi PS (suo motu)

Brief facts:
Acting on a tip-off a courier was intercepted at Bhiwandi carrying 38 pre-activated SIM cards and
a diary with "customer lists" of senior citizens. The accused disclosed that he delivers SIM cards to
Mumbai and is paid in cash. He receives instructions only by phone from a person he knows as 'Seth',
who calls from mobile no. 70045 98812. He has never met 'Seth' in person.

Accused:
1. Salim Qureshi, s/o Rafiq Qureshi, aged 31, r/o Bhiwandi, Thane, Mob: 9967011020 (arrested)

Investigating Officer: PSI V. Kadam
`;

async function showTab(t) {
  S.tab = t; document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
  const p = $("#panel"); p.innerHTML = `<div class="empty">Loading…</div>`;
  try { await TABS[t](p); } catch (e) { p.innerHTML = `<div class="denied">${e.status === 403 ? `Your role (<b>${esc(S.user.role)}</b>) has no access to this view.<br>The attempt was recorded in the audit trail.` : esc(e.message)}</div>`; }
}

/* ======================================================================== CHROME */
async function loadChain() {
  const b = $("#chain");
  try { const v = await api("/api/audit/verify"); b.className = "badge " + (v.ok ? "ok" : "bad"); b.textContent = v.ok ? `audit chain ✔ ${v.entries}` : "audit chain ✖"; }
  catch { b.className = "badge"; b.textContent = "audit: restricted"; }
}
async function refreshAll() {
  S.pg = null; S.selected = null; S.highlight = null;
  $("#dossier").innerHTML = `<div class="empty">Select a person in the graph or a target on the left to open their dossier.</div>`;
  if (can("graph")) await loadPersonGraph(); else { G.set([], []); $("#stats").textContent = ""; $("#legend").innerHTML = ""; }
  await showTab(can("targets") ? (S.tab === "audit" && !can("audit") ? "targets" : S.tab) : "audit");
  loadChain();
}
async function init() {
  S.users = await api("/api/users");
  const sel = $("#user");
  sel.innerHTML = S.users.map(u => `<option value="${u.token}">${esc(u.name)} — ${u.role}</option>`).join("");
  sel.value = S.token;
  const setUser = async () => {
    S.token = sel.value; localStorage.setItem("jaal_token", S.token); S.user = S.users.find(u => u.token === S.token);
    $("#rolebar").innerHTML = `Signed in as <b>${esc(S.user.name)}</b> · role <b>${esc(S.user.role)}</b> — ${esc(S.user.desc)}`;
    await refreshAll();
  };
  sel.onchange = setUser;
  document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => showTab(b.dataset.tab));
  document.querySelectorAll("#viewseg button").forEach(b => b.onclick = () => b.dataset.v === "person" ? loadPersonGraph() : loadKG(S.selected && S.selected.startsWith("P:") ? S.selected : null));
  $("#colorby").onchange = e => { S.colorBy = e.target.value; G.dirty = true; legend(); };
  $("#noi").onchange = e => { S.noiOnly = e.target.checked; loadPersonGraph(); };
  $("#fit").onclick = () => G.fit();
  let tmr; $("#q").oninput = e => {
    clearTimeout(tmr); const v = e.target.value.trim(); const box = $("#qres");
    if (v.length < 2) { box.style.display = "none"; return; }
    tmr = setTimeout(async () => {
      try { const r = await api("/api/search?q=" + encodeURIComponent(v));
        box.innerHTML = r.map(x => `<div data-id="${esc(x.id)}">${esc(x.label)} <small>${esc(x.type)}</small></div>`).join("") || `<div class="muted">No match</div>`;
        box.style.display = "block"; box.querySelectorAll("div[data-id]").forEach(d => d.onclick = () => { box.style.display = "none"; openEntity(d.dataset.id); });
      } catch (err) { box.innerHTML = `<div class="muted">${esc(err.message)}</div>`; box.style.display = "block"; }
    }, 180);
  };
  document.addEventListener("click", e => { if (!e.target.closest(".search")) $("#qres").style.display = "none"; });
  // deep links, e.g. #user=tok-supervisor&tab=alerts&select=P:vikram-rathod&view=kg&path=P:a,P:b
  const H = Object.fromEntries(new URLSearchParams(location.hash.slice(1)));
  if (H.user) { sel.value = H.user; }
  if (H.tab) S.tab = H.tab;
  await setUser();
  if (H.noi) { $("#noi").checked = true; S.noiOnly = true; await loadPersonGraph(); }
  if (H.color) { $("#colorby").value = H.color; S.colorBy = H.color; G.dirty = true; legend(); }
  if (H.view === "kg") await loadKG(H.select || null);
  if (H.select) await openEntity(H.select);
  if (H.path) { const [a, b] = H.path.split(","); await showPath(a, b); }
  if (H.caselink !== undefined) { const d = await api("/api/cases"); const l = d.links[+H.caselink]; S.highlight = l.path; G.dirty = true; $("#pathbox").innerHTML = `${esc(l.a)} ⇄ ${esc(l.b)}: <b>${l.path_labels.map(esc).join(" → ")}</b>`; }
  if (H.doc) { const el = document.createElement("span"); el.className = "evlink"; el.dataset.doc = H.doc; el.dataset.loc = H.loc || "L1"; document.body.appendChild(el); el.click(); el.remove(); }
  if (H.scroll) setTimeout(() => { const t = document.querySelector(H.scroll); if (t) t.scrollIntoView(); }, 300);
  document.body.dataset.ready = "1";
}
init();
