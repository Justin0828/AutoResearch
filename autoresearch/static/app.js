"use strict";
// AutoResearch frontend. Four pages behind a hash router: Chat, Inbox, Research, System.
// No build step, no dependencies. UI copy is English; research content stays as written.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {
  page: "chat", ds: null, dsData: null, shift: null, streaming: "",
  overview: null, cands: [], reviews: [], activity: [], notes: [], unread: 0,
  inboxView: "cands", sysView: "status", editing: null, inboxStale: false, dsList: [],
  mode: null, prep: null, requests: [], readingView: "now", chainTarget: null, reading: null, paperOpen: null,
  evolutions: null,
};

async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(data.error || r.statusText); e.status = r.status; e.data = data; throw e; }
  return data;
}

function toast(msg, level = "info", ms = 4200) {
  const el = document.createElement("div");
  el.className = `toast ${level}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}
const fail = (err) => toast(err.message || String(err), "error", 7000);

// ---------------------------------------------------------------- labels
const KIND = { assumption: "Assumption", hypothesis: "Hypothesis", question: "Question", uncertainty: "Uncertainty", insight: "Insight", revision: "Revision", resolve: "Close question" };
const TYPE = { question: "Question", assumption: "Assumption", hypothesis: "Hypothesis", uncertainty: "Uncertainty", insight: "Insight", evolution: "Evolution", evidence: "Evidence", "dead-end": "Dead end", decision: "Decision", candidate: "Candidate", review: "Re-examination", paper: "Paper", grounding: "Ground check", discussion: "Discussion" };
const U_STATUS = { open: "Open", reduced: "Reduced", resolved: "Resolved", withdrawn: "Withdrawn" };
const Q_STATUS = { open: "Open", answered: "Answered", decided: "Decided", merged: "Merged", withdrawn: "Withdrawn" };
const Q_CLOSED = ["answered", "decided", "merged"];
const RESOLUTION = { answered: "Answered — something we have now answers it", decided: "Decided — it was a choice, and you've made it", merged: "Merged — it's the same question as another one" };
const MATURITY = { vague: "Vague", scoped: "Scoped", formalized: "Formalized" };
const FOCUSABLE = ["question", "assumption", "hypothesis", "uncertainty", "insight", "evolution"];
const CURATABLE = ["question", "assumption", "hypothesis", "uncertainty", "insight"];   // can be withdrawn / un-accepted
const FIRM = { hunch: "Hunch", working: "Working", settled: "Settled" };
const ORIGIN = { human: "you", ai: "AI", unclear: "unclear" };
const H_STATUS = { proposed: "Proposed", investigating: "Investigating", supported: "Supported", refuted: "Refuted", inconclusive: "Inconclusive", abandoned: "Abandoned" };
const A_STATUS = { unexamined: "Unexamined", examined: "Examined", promoted: "Promoted", retired: "Retired", invalidated: "Invalidated" };
const T_STATUS = { queued: "Queued", running: "Running", done: "Done", failed: "Failed", interrupted: "Interrupted", cancelled: "Cancelled", blocked_on_human: "Waiting on you" };
const T_KIND = { discuss_turn: "Reply", distill: "Distill", lit_search: "Search", read_paper: "Read", assess: "Assess", contradiction_scan: "Contradiction scan", grounding: "Ground check", evolve: "Evolve", tidy: "Tidy up" };
const STANCE = { support: "Supports", contradict: "Contradicts", neutral: "Neutral" };
const VERDICT = { novel: "No prior work found", prior_work: "Already done", contradicted: "Directly contradicted", mixed: "Mixed" };
const MODE = { discussion: "Discussion mode", validation: "Validation mode" };
const PAUSE = { quota_5h: "5h limit", quota_7d: "weekly limit", cutoff: "cut off", manual: "paused", crash: "crash", normal: "normal" };
const FIELDS = {
  assumption: [["relied_on_by", "Supports"], ["derived_from", "Derived from insight"]],
  hypothesis: [["falsifier", "Refuted if"], ["validation", "How it will be tested"], ["confidence", "Confidence"]],
  question: [["maturity", "Maturity"]],
  uncertainty: [["importance", "Importance"]],
  insight: [["firmness", "Firmness"], ["basis", "Grounded in"], ["basis_note", "Grounding note"], ["informs", "Informs"], ["change_mind", "Would change if"]],
};
const fmtv = (v) => Array.isArray(v) ? v.join(", ") : (v ?? "");

// Every object id on screen is a link to its page (DESIGN §5.15.4). Works on already-escaped text.
const ID_RE = /(^|[^\w#/=".-])((?:DS|DEC|IN|GR|RQ|EV|Q|A|H|U|E|P|D|C|R)\d{3,})(?![\w])/g;
function idHref(id) {
  if (/^P\d/.test(id)) return `#/reading/${id}`;
  if (/^DS\d/.test(id)) return `#/chat/${id}`;
  return `#/obj/${id}`;
}
const linkIds = (html) => String(html).replace(ID_RE, (_, pre, id) => `${pre}<a class="idlink" href="${idHref(id)}">${id}</a>`);
const idl = (v) => linkIds(esc(fmtv(v)));
const fmtTs = (ts) => (ts || "").replace("T", " ").slice(0, 16);

// ---------------------------------------------------------------- tiny markdown (escape first, then mark up)
function md(src) {
  const lines = esc(src || "").split("\n");
  const out = [];
  let list = null, para = [], code = null, table = null;
  const inline = (t) => t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(ID_RE, (_, pre, id) => `${pre}<a class="idlink" href="${idHref(id)}">${id}</a>`);
  const flush = () => {
    if (para.length) { out.push(`<p>${inline(para.join("<br>"))}</p>`); para = []; }
    if (list) { out.push(`</${list}>`); list = null; }
    if (table) { out.push(`<table>${table.join("")}</table>`); table = null; }
  };
  for (const l of lines) {
    if (code !== null) {
      if (/^```/.test(l)) { out.push(`<pre>${code.join("\n")}</pre>`); code = null; } else code.push(l);
      continue;
    }
    if (/^```/.test(l)) { flush(); code = []; continue; }
    let m;
    if ((m = l.match(/^(#{1,5})\s+(.*)$/))) { const h = Math.min(m[1].length + 2, 5); flush(); out.push(`<h${h}>${inline(m[2])}</h${h}>`); continue; }
    if (/^\s*\|.*\|\s*$/.test(l)) {
      if (!table) { flush(); table = []; }
      if (/^\s*\|[\s:|-]+\|\s*$/.test(l)) continue;
      table.push("<tr>" + l.trim().slice(1, -1).split("|").map((c) => `<td>${inline(c.trim())}</td>`).join("") + "</tr>");
      continue;
    }
    if ((m = l.match(/^\s*([-*]|\d+\.)\s+(.*)$/))) {
      const kind = /\d/.test(m[1]) ? "ol" : "ul";
      if (para.length || table) { const L = list; list = null; flush(); list = L; }
      if (list !== kind) { if (list) out.push(`</${list}>`); out.push(`<${kind}>`); list = kind; }
      out.push(`<li>${inline(m[2])}</li>`); continue;
    }
    if (!l.trim()) { flush(); continue; }
    if (list || table) flush();
    para.push(l);
  }
  if (code !== null) out.push(`<pre>${code.join("\n")}</pre>`);
  flush();
  return out.join("");
}
const firstLine = (b) => (b || "").split("\n").find((l) => l.trim() && !l.startsWith("#")) || "";
const mainText = (b) => (b || "").split("\n## ")[0].trim();

const tmr = {};
const soon = (key, fn, ms = 150) => { clearTimeout(tmr[key]); tmr[key] = setTimeout(fn, ms); };

// ---------------------------------------------------------------- router
function route() {
  const [, page = "chat", arg] = (location.hash || "#/chat").split("/");
  S.page = ["chat", "inbox", "research", "evolution", "reading", "system", "obj"].includes(page) ? page : "chat";
  $$(".page").forEach((p) => p.classList.toggle("on", p.id === "page-" + S.page));
  $$(".rail nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === (S.page === "obj" ? "research" : S.page)));
  if (S.page === "obj") { if (arg) renderObject(arg); $("#page-obj").scrollTop = 0; }
  if (S.page === "chat") {
    if (arg && arg !== S.ds) { S.ds = arg; S.dsData = null; S.streaming = ""; refreshDs(); }
    loadDiscussions();
  }
  if (S.page === "inbox") renderInbox();
  if (S.page === "research") loadOverview();
  if (S.page === "evolution") renderEvolution(arg);
  if (S.page === "reading") {
    if (arg && /^[HA]\d+$/.test(arg)) { S.readingView = "chain"; S.chainTarget = arg; }
    if (arg && /^P\d+$/.test(arg)) { S.readingView = "papers"; S.paperOpen = arg; }
    renderReading();
  }
  if (S.page === "system") { S.unread = 0; renderBadges(); renderSystem(); }
}
window.addEventListener("hashchange", route);
const go = (hash) => { if (location.hash !== hash) location.hash = hash; else route(); };

// ---------------------------------------------------------------- dialog
function ask(title, bodyHtml, okLabel = "Confirm", opts = {}) {
  return new Promise((resolve) => {
    const dlg = $("#dlg");
    dlg.classList.toggle("wide", !!opts.wide);
    $("#dlg-title").textContent = title;
    $("#dlg-body").innerHTML = bodyHtml;
    $("#dlg-ok").textContent = okLabel;
    dlg.onclose = () => {
      if (dlg.returnValue !== "ok") return resolve(null);
      const out = {};
      $$("[name]", $("#dlg-body")).forEach((el) => {
        if (el.type === "checkbox") { out[el.name] = out[el.name] || []; if (el.checked) out[el.name].push(el.value); }
        else out[el.name] = el.value;
      });
      resolve(out);
    };
    dlg.returnValue = "";
    dlg.showModal();
    const f = $("textarea, input", $("#dlg-body"));
    if (f) f.focus();
  });
}
const field = (label, name, value = "", hint = "", rows = 0) =>
  `<label>${label}${hint ? ` <span class="hint">— ${hint}</span>` : ""}</label>` +
  (rows ? `<textarea name="${name}" rows="${rows}">${esc(value)}</textarea>` : `<input name="${name}" value="${esc(value)}">`);
const select = (label, name, opts, cur) =>
  `<label>${label}</label><select name="${name}">${Object.entries(opts).map(([k, n]) =>
    `<option value="${k}" ${k === cur ? "selected" : ""}>${n}</option>`).join("")}</select>`;

// ================================================================ CHAT
// List order (DESIGN §5.15.5): pinned → groups (collapsible, remembered) → ungrouped; newest activity first within each.
const lastAct = (d) => d.last_ts || d.created || "";
function dsItem(d) {
  return `<li data-id="${d.id}" class="${d.id === S.ds ? "sel" : ""} ${d.status === "closed" ? "closed" : ""}">
      <span class="t">${d.pinned ? `<span class="pin" title="Pinned">●</span>` : ""}${esc(d.title)}</span>
      <span class="m">${d.focus ? `<span class="ftag" title="Focused on ${esc(d.focus)}">${esc(d.focus)}</span>` : ""}${d.turns} turn${d.turns === 1 ? "" : "s"}${d.status === "closed" ? " · ended" : ""}
        ${d.awaiting_reply ? `<span class="await">· awaiting reply</span>` : ""}</span>
      <button class="ds-more" data-edit="${d.id}" title="Rename, group, pin" aria-label="Organize">⋯</button>
    </li>`;
}
async function loadDiscussions() {
  const list = await api("GET", "/api/discussions");
  S.dsList = list;
  const ul = $("#ds-list");
  const recent = (xs) => xs.slice().sort((a, b) => lastAct(b).localeCompare(lastAct(a)));
  const pinned = recent(list.filter((d) => d.pinned));
  const groups = {};
  list.filter((d) => !d.pinned && d.group).forEach((d) => (groups[d.group] = groups[d.group] || []).push(d));
  const loose = recent(list.filter((d) => !d.pinned && !d.group));
  const names = Object.keys(groups).sort((a, b) => lastAct(recent(groups[b])[0]).localeCompare(lastAct(recent(groups[a])[0])));
  const html = [];
  if (pinned.length) html.push(`<li class="grp-h static"><span>Pinned</span></li>`, ...pinned.map(dsItem));
  for (const g of names) {
    const open = fold.get("grp." + g, true);
    html.push(`<li class="grp-h" data-grp="${esc(g)}"><span class="caret-i">${open ? "▾" : "▸"}</span><span class="gname">${esc(g)}</span><b>${groups[g].length}</b>
      <button class="ds-more" data-grp-edit="${esc(g)}" title="Rename or ungroup" aria-label="Rename group">⋯</button></li>`);
    if (open) html.push(...recent(groups[g]).map(dsItem));
  }
  if (loose.length && (pinned.length || names.length)) html.push(`<li class="grp-h static"><span>Other</span></li>`);
  html.push(...loose.map(dsItem));
  ul.innerHTML = html.join("") || `<li class="empty">No discussions yet.</li>`;
  $$("li[data-id]", ul).forEach((li) => li.onclick = (e) => { if (!e.target.closest("[data-edit]")) go(`#/chat/${li.dataset.id}`); });
  $$("[data-edit]", ul).forEach((b) => b.onclick = () => organizeDs(list.find((d) => d.id === b.dataset.edit)));
  $$("li[data-grp]", ul).forEach((li) => li.onclick = (e) => {
    if (e.target.closest("[data-grp-edit]")) return;
    fold.set("grp." + li.dataset.grp, !fold.get("grp." + li.dataset.grp, true));
    loadDiscussions();
  });
  $$("[data-grp-edit]", ul).forEach((b) => b.onclick = () => renameGroup(b.dataset.grpEdit));
  if (!S.ds && list.length && S.page === "chat") go(`#/chat/${recent(list)[0].id}`);
  if (!list.length) renderTurns();
}

async function organizeDs(d) {
  const groups = [...new Set(S.dsList.map((x) => x.group).filter(Boolean))];
  const r = await ask(`Organize ${d.id}`, field("Title", "title", d.title, "only the label changes; the transcript keeps its history") +
    `<label>Group <span class="hint">— a label; leave empty for none</span></label><input name="group" list="grp-list" value="${esc(d.group || "")}">
     <datalist id="grp-list">${groups.map((g) => `<option value="${esc(g)}">`).join("")}</datalist>
     <div class="checks"><label><input type="checkbox" name="pinned" value="1" ${d.pinned ? "checked" : ""}><span>Pin to the top</span></label></div>`, "Save");
  if (!r) return;
  try {
    await api("POST", `/api/discussions/${d.id}/meta`, { title: r.title, group: r.group, pinned: (r.pinned || []).length > 0 });
  } catch (err) { fail(err); }
  loadDiscussions(); if (d.id === S.ds) refreshDs();
}

async function renameGroup(g) {
  const r = await ask(`Group “${g}”`, `<p>A group is just a label on its discussions. Renaming relabels them all; an empty name ungroups them.</p>` +
    field("New name", "to", g), "Save");
  if (!r) return;
  try { await api("POST", "/api/discussion-groups/rename", { from: g, to: r.to }); } catch (err) { fail(err); }
  loadDiscussions();
}

async function refreshDs() {
  if (!S.ds) return renderTurns();
  let d;
  try { d = await api("GET", `/api/discussions/${S.ds}`); } catch (e) { S.ds = null; S.dsData = null; return renderTurns(); }
  S.dsData = d;
  if (d.streaming && !S.streaming) S.streaming = d.streaming;
  $("#ds-title").textContent = d.meta.title;
  $("#ds-meta").innerHTML = (d.meta.focus ? `Focused on <a class="idlink" href="#/obj/${esc(d.meta.focus)}">${esc(d.meta.focus)}</a> · ` : "") +
    `${esc(d.meta.id)} · ${d.turns.length} turns · ` +
    (d.summary.covers_through ? `summary through turn ${d.summary.covers_through}` : "not distilled yet") +
    (d.meta.status === "closed" ? " · ended" : "");
  const open = d.meta.status === "open";
  input.disabled = !open;
  input.placeholder = open ? "Share a thought…" : "This discussion has ended.";
  updateSend();
  $("#distill-btn").disabled = !open || !d.undistilled_human;
  $("#close-ds-btn").disabled = !open;
  $("#summary-box").classList.toggle("hidden", !d.summary.body);
  $("#summary-body").innerHTML = md(d.summary.body);
  renderTurns();
}

function renderTurns() {
  const box = $("#turns");
  const d = S.dsData;
  if (!S.ds || !d) {
    $("#ds-title").textContent = "No discussion selected";
    $("#ds-meta").textContent = "";
    box.innerHTML = `<div class="empty-state"><span class="mark">✦</span><h2>Start a discussion</h2>
      <p>Bring a rough idea. The partner will push back, point at weak premises, and check the literature when it matters.</p>
      <button class="btn" id="empty-new">New discussion</button></div>`;
    $("#empty-new").onclick = newDiscussion;
    return;
  }
  const html = d.turns.map((t) => t.role === "human"
    ? `<div class="turn human"><div class="who"><span class="ts">${fmtTs(t.ts)}</span>You</div><div class="bubble md">${md(t.text)}</div></div>`
    : `<div class="turn ai"><div class="who">AI <span class="ts">turn ${t.n} · ${fmtTs(t.ts)}</span></div><div class="md">${md(t.text)}</div></div>`);
  const last = d.turns[d.turns.length - 1];
  if (last && last.role === "human") html.push(`<div id="stream-slot"></div>`);
  const distilling = d.tasks.find((t) => t.kind === "distill");
  if (distilling) html.push(`<div class="turn-note"><span class="tag insight">Distilling</span><span class="thinking">Extracting research content into the Inbox</span></div>`);
  if (!d.turns.length) html.push(d.meta.focus
    ? `<div class="empty-state"><h2>${esc(d.meta.title)}</h2><p>This discussion is about <a class="idlink" href="#/obj/${esc(d.meta.focus)}">${esc(d.meta.focus)}</a>. The AI sees its full text, its history and everything linked to it. The goal is to get it clear and precise — not necessarily to move it up a maturity level. When the wording gets sharper, distilling proposes a revision.</p></div>`
    : `<div class="empty-state"><h2>${esc(d.meta.title)}</h2><p>Write your first message below. The title is only a label — the AI starts replying once you send something.</p></div>`);
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  box.innerHTML = html.join("");
  renderStream();
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function renderStream() {
  const slot = $("#stream-slot");
  if (!slot || !S.dsData) return;
  const busy = S.dsData.tasks.find((t) => t.kind === "discuss_turn");
  if (S.streaming) {
    slot.innerHTML = `<div class="turn ai"><div class="who">AI</div><div class="md caret">${md(S.streaming)}</div></div>`;
  } else {
    const paused = S.shift && S.shift.state === "paused";
    const msg = paused ? "The shift is paused. It will reply once work resumes — a handoff has been saved."
      : busy ? (busy.status === "running" ? `<span class="thinking">Reading the briefing</span>` : `<span class="thinking">Queued</span>`)
        : `<span class="thinking">Waiting to be dispatched</span>`;
    slot.innerHTML = `<div class="turn ai pending"><div class="who">AI</div><div class="md">${msg}</div></div>`;
  }
  const box = $("#turns");
  if (box.scrollHeight - box.scrollTop - box.clientHeight < 200) box.scrollTop = box.scrollHeight;
}

const input = $("#input");
function updateSend() { $("#send").disabled = input.disabled || !input.value.trim(); }
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 240) + "px";
  updateSend();
});
input.onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#composer").requestSubmit(); };
$("#composer").onsubmit = async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || !S.ds) return;
  $("#send").disabled = true;
  try {
    await api("POST", `/api/discussions/${S.ds}/messages`, { text });
    input.value = ""; input.style.height = "auto"; S.streaming = "";
    await refreshDs();
    $("#turns").scrollTop = $("#turns").scrollHeight;
  } catch (err) { fail(err); }
  updateSend();
};

async function newDiscussion() {
  const f = await ask("New discussion",
    field("Title", "title", "", "a label for the list; not sent to the AI") +
    field("First message", "text", "", "optional — you can also write it in the chat", 4), "Create");
  if (!f) return;
  try {
    const r = await api("POST", "/api/discussions", { title: f.title || (f.text || "").slice(0, 40) || "Untitled" });
    if (f.text && f.text.trim()) await api("POST", `/api/discussions/${r.id}/messages`, { text: f.text.trim() });
    go(`#/chat/${r.id}`);
    setTimeout(() => input.focus(), 50);
  } catch (err) { fail(err); }
}
$("#new-ds").onclick = newDiscussion;
$("#distill-btn").onclick = async () => {
  try {
    const r = await api("POST", `/api/discussions/${S.ds}/distill`);
    toast(r.note ? "Nothing new to distill, or a distill is already queued." : "Distill queued — results will land in the Inbox.");
    refreshDs();
  } catch (err) { fail(err); }
};
$("#close-ds-btn").onclick = async () => {
  const r = await ask("End this discussion?", `<p>Anything not yet distilled will be distilled one last time. The transcript stays in the research state.</p>`, "End discussion");
  if (!r) return;
  try { await api("POST", `/api/discussions/${S.ds}/status`, { status: "closed" }); } catch (err) { fail(err); }
  refreshDs(); loadDiscussions();
};

// ================================================================ INBOX
async function loadInbox() {
  const [cands, revs, reqs] = await Promise.all([api("GET", "/api/candidates"), api("GET", "/api/reviews"), api("GET", "/api/requests")]);
  S.cands = cands; S.reviews = revs; S.requests = reqs;
  renderBadges();
  if (S.page === "inbox") renderInbox();
}

function renderBadges() {
  const pc = S.cands.filter((c) => c.status === "pending").length;
  const pr = S.reviews.filter((r) => r.status === "open").length;
  const rq = S.requests.filter((r) => r.status === "open").length;
  $("#inbox-badge").textContent = pc + pr + rq || "";
  $("#seg-requests").textContent = rq || "";
  $("#seg-cands").textContent = pc || "";
  $("#seg-reviews").textContent = pr || "";
  $("#sys-badge").textContent = S.unread || "";
  $("#seg-notes").textContent = S.unread || "";
}

$$("#inbox-seg button").forEach((b) => b.onclick = () => { S.inboxView = b.dataset.v; S.editing = null; renderInbox(); });

// ---------------------------------------------------------------- candidate editing (inline, per kind)
// Each kind only shows the fields it needs. `req` marks what accepting it will require.
const CHOICES = {
  confidence: { low: "Low", medium: "Medium", high: "High" },
  maturity: { vague: "Vague", scoped: "Scoped", formalized: "Formalized" },
  importance: { low: "Low", medium: "Medium", high: "High" },
  firmness: { hunch: "Hunch", working: "Working", settled: "Settled" },
  origin: { human: "Me", ai: "The AI", unclear: "Unclear" },
};
const KIND_SPEC = {
  assumption: {
    hint: "Relied on — used to prune directions or support other reasoning — but no test is arranged.",
    fields: [
      { k: "relied_on_by", label: "Supports", type: "ids", req: true, help: "What depends on it" },
      { k: "derived_from", label: "Derived from insight", type: "id", help: "If a hunch is what we're pruning with" },
    ],
  },
  hypothesis: {
    hint: "A test has been arranged. Must say what result would refute it.",
    fields: [
      { k: "falsifier", label: "Refuted if", type: "text", req: true },
      { k: "validation", label: "How it will be tested", type: "text", req: true },
      { k: "confidence", label: "Confidence", type: "choice" },
    ],
  },
  question: {
    hint: "Something still to be clarified.",
    fields: [{ k: "maturity", label: "Maturity", type: "choice", req: true },
      { k: "parent", label: "Sub-question of", type: "id", help: "Its parent question in the tree" }],
  },
  uncertainty: {
    hint: "An unknown that discounts our conclusions and can't be removed for now.",
    fields: [{ k: "importance", label: "Importance", type: "choice", req: true }],
  },
  insight: {
    hint: "Something we've come to think. Can be a feel — but say where it comes from.",
    fields: [
      { k: "firmness", label: "Firmness", type: "choice", req: true },
      { k: "basis", label: "Grounded in", type: "ids", help: "Ids in the state" },
      { k: "basis_note", label: "Grounding note", type: "text", help: "Or a source outside the state" },
      { k: "informs", label: "Informs", type: "ids" },
      { k: "change_mind", label: "Would change if", type: "text" },
      { k: "supersedes", label: "Merges", type: "ids", help: "Two or more active insights this one replaces" },
    ],
  },
};
const REJECT_REASONS = ["Duplicate of ", "Already covered by ", "Not research content", "Wrong framing", "Just a test"];

function knownIds() {
  const o = S.overview || {};
  const out = [];
  for (const key of ["questions", "assumptions", "hypotheses", "insights", "uncertainties", "evidence", "dead_ends", "papers"]) {
    for (const x of o[key] || []) out.push([x.id, firstLine(x.body || x.title || "").slice(0, 60)]);
  }
  for (const d of S.dsList || []) out.push([d.id, d.title]);
  return out;
}

function tokenInput(k, values, single) {
  const known = new Set(knownIds().map(([id]) => id));
  const chip = (v) => `<span class="chip ${known.has(v) ? "" : "bad"}" title="${known.has(v) ? "" : "Not found in the research state"}">${esc(v)}<button type="button" data-rm="${esc(v)}" aria-label="Remove">×</button></span>`;
  return `<div class="tokens" data-k="${k}" data-single="${single ? 1 : 0}">${values.map(chip).join("")}
    <input list="ids-list" placeholder="${values.length && single ? "" : "Add id…"}" ${values.length && single ? "hidden" : ""}></div>`;
}

function choiceInput(k, cur, opts) {
  return `<div class="choice" data-k="${k}">${Object.entries(opts).map(([v, n]) =>
    `<button type="button" data-v="${v}" class="${v === cur ? "on" : ""}">${n}</button>`).join("")}</div>`;
}

function editForm(c, draft) {
  const spec = KIND_SPEC[draft.kind];
  const row = (label, req, body, help) => `<div class="frow"><div class="flabel">${label}${req ? `<i class="req">required</i>` : ""}${help ? `<span>${help}</span>` : ""}</div><div class="fbody">${body}</div></div>`;
  const fieldHtml = (f) => {
    const v = draft[f.k];
    let body;
    if (f.type === "ids") body = tokenInput(f.k, Array.isArray(v) ? v : (v ? String(v).split(/\s*,\s*/).filter(Boolean) : []), false);
    else if (f.type === "id") body = tokenInput(f.k, v ? [v] : [], true);
    else if (f.type === "choice") body = choiceInput(f.k, v, CHOICES[f.k]);
    else body = `<textarea data-k="${f.k}" rows="2">${esc(v || "")}</textarea>`;
    return row(f.label, f.req, body, f.help);
  };
  const insightReq = draft.kind === "insight" ? `<div class="fnote">Needs “Grounded in” or a grounding note — at least one.</div>` : "";
  return `<div class="card editing" data-id="${c.id}">
    <div class="card-top"><span class="id">${c.id}</span><span>editing · from ${c.source} · turn ${fmtv(c.turns)}</span></div>
    <div class="kinds">${Object.entries(KIND).filter(([k]) => !["revision", "resolve"].includes(k)).map(([k, n]) => `<button type="button" data-kind="${k}" class="tag ${k} ${k === draft.kind ? "on" : ""}">${n}</button>`).join("")}</div>
    <div class="khint">${spec.hint}</div>
    <textarea class="stmt" data-k="statement" rows="2" placeholder="Statement">${esc(draft.statement || "")}</textarea>
    <div class="form">
      ${spec.fields.map(fieldHtml).join("")}${insightReq}
      ${row("Raised first by", false, choiceInput("origin", draft.origin, CHOICES.origin))}
      ${draft.origin === "unclear" ? row("Origin note", true, `<textarea data-k="origin_note" rows="2">${esc(draft.origin_note || "")}</textarea>`) : ""}
      ${row("Why", false, `<textarea data-k="rationale" rows="3">${esc(draft.rationale || "")}</textarea>`)}
    </div>
    <div class="ferr" hidden></div>
    <div class="acts">
      <button class="btn small" data-act="save-accept" ${draft.origin === "unclear" ? `disabled title="Pick who raised it first to accept"` : ""}>Save & accept</button>
      <button class="btn ghost small" data-act="save">Save</button>
      <button class="btn ghost small" data-act="cancel">Cancel</button>
      <span class="kbd">Ctrl+Enter save · Esc cancel</span>
    </div>
  </div>`;
}

function rejectForm(c, draft) {
  return `<div class="card editing" data-id="${c.id}">
    <div class="card-top"><span class="tag ${c.kind}">${KIND[c.kind]}</span><span class="id">${c.id}</span><span>rejecting</span></div>
    <div class="statement md">${md(c.statement)}</div>
    <div class="flabel" style="margin-top:14px">Reason<i class="req">required</i><span>kept, so future distills won't propose this again</span></div>
    <div class="quick">${REJECT_REASONS.map((r) => `<button type="button" data-q="${esc(r)}">${esc(r.trim())}${r.endsWith(" ") ? "…" : ""}</button>`).join("")}</div>
    <textarea data-k="reason" rows="3" placeholder="Why doesn't this belong in the research state?">${esc(draft.reason || "")}</textarea>
    <div class="ferr" hidden></div>
    <div class="acts">
      <button class="btn small danger-solid" data-act="confirm-reject">Reject</button>
      <button class="btn ghost small" data-act="cancel">Cancel</button>
      <span class="kbd">Ctrl+Enter reject · Esc cancel</span>
    </div>
  </div>`;
}

function acceptForm(c) {
  return `<div class="card editing" data-id="${c.id}">
    <div class="card-top"><span class="tag ${c.kind}">${KIND[c.kind]}</span><span class="id">${c.id}</span><span class="tag warn">Origin needs your call</span></div>
    <div class="statement md">${md(c.statement)}</div>
    <div class="fnote" style="margin-top:12px">${esc(c.origin_note || "The distiller couldn't tell who raised this first.")}</div>
    <div class="flabel" style="margin-top:12px">Who raised it first?</div>
    <div class="acts" style="margin-top:8px">
      <button class="btn small" data-act="accept-as" data-origin="human">Me — accept</button>
      <button class="btn small" data-act="accept-as" data-origin="ai">The AI — accept</button>
      <button class="btn ghost small" data-act="cancel">Cancel</button>
    </div>
  </div>`;
}

function candCard(c) {
  const ed = S.editing && S.editing.id === c.id ? S.editing : null;
  if (ed && ed.mode === "edit") return editForm(c, ed.draft);
  if (ed && ed.mode === "reject") return rejectForm(c, ed.draft);
  if (ed && ed.mode === "accept") return acceptForm(c);
  const pending = c.status === "pending";
  if (c.kind === "revision") return revisionCard(c);
  if (c.kind === "resolve") return resolveCard(c);
  const fields = [...(FIELDS[c.kind] || []), ["parent", "Sub-question of"], ["supersedes", "Merges"]].filter(([k]) => c[k] && fmtv(c[k]))
    .map(([k, n]) => `<dt>${n}</dt><dd>${k === "firmness" ? esc(FIRM[c[k]] || c[k]) : idl(c[k])}</dd>`).join("");
  const state = c.status === "accepted" ? `<span class="tag plain">Accepted → ${idl(c.promoted_to)}</span>`
    : c.status === "rejected" ? `<span class="tag plain">Rejected</span>` : "";
  return `<div class="card ${pending ? "" : "dim"} ${c.origin === "unclear" && pending ? "flagged" : ""}" data-id="${c.id}">
    <div class="card-top"><span class="tag ${c.kind}">${KIND[c.kind]}</span>${c.supersedes ? `<span class="tag plain" title="Accepting replaces ${esc(fmtv(c.supersedes))} with this one">Merge</span>` : ""}<span class="id">${c.id}</span>
      <span>${srcLine(c)} · proposed by ${ORIGIN[c.origin] || c.origin}</span>
      ${c.origin === "unclear" && pending ? `<span class="tag warn">Origin needs your call</span>` : ""}${state}</div>
    <div class="statement md">${md(c.statement)}</div>
    ${fields || c.relates_to || c.origin_note ? `<dl>${fields}
      ${c.relates_to ? `<dt>Related to</dt><dd>${idl(c.relates_to)}</dd>` : ""}
      ${c.origin_note ? `<dt>Origin note</dt><dd>${esc(c.origin_note)}</dd>` : ""}</dl>` : ""}
    <details class="why"><summary>Why this was proposed</summary><div class="md">${md(c.rationale)}</div></details>
    ${c.decision_note ? `<details class="why" open><summary>Your note</summary><div class="md">${md(c.decision_note)}</div></details>` : ""}
    ${pending ? `<div class="acts"><button class="btn small" data-act="accept">Accept</button>
      <button class="btn ghost small" data-act="accept-discuss" title="Accept, then open a discussion focused on the new object">Accept & discuss</button>
      <button class="btn ghost small" data-act="edit">Edit</button>
      <button class="btn ghost small danger" data-act="reject">Reject</button></div>` : ""}
  </div>`;
}

const srcLine = (c) => /^T\d/.test(c.source || "") ? `from tidy-up ${esc(c.source)}` : `from ${idl(c.source)} · turn ${fmtv(c.turns)}`;

// Closing a question (§5.16.1): answered / decided / merged. Nothing is deleted; it can be reopened.
function resolveCard(c) {
  const pending = c.status === "pending";
  const state = c.status === "accepted" ? `<span class="tag plain">Accepted → ${idl(c.promoted_to)} ${esc(c.resolution)}</span>`
    : c.status === "rejected" ? `<span class="tag plain">Rejected</span>` : "";
  const refs = (c.refs || []).filter((r) => r.status === "pending");
  const how = c.resolution === "answered" ? `<dt>Answered by</dt><dd>${idl(c.answered_by)}${refs.length ? ` <span class="meta">(${refs.map((r) => r.id).join(", ")} still pending — accepted together)</span>` : ""}</dd>`
    : c.resolution === "merged" ? `<dt>Merge into</dt><dd>${idl(c.merged_into)}</dd>` : `<dt>Suggested decision</dt><dd>You write the final wording when confirming</dd>`;
  return `<div class="card ${pending ? "" : "dim"} ${c.stale && pending ? "flagged" : ""}" data-id="${c.id}">
    <div class="card-top"><span class="tag hypothesis">${KIND.resolve}</span><span class="id">${c.id}</span>
      <span>closes ${idl(c.target)} as <b>${esc(c.resolution)}</b> · ${srcLine(c)} · proposed by ${ORIGIN[c.origin] || c.origin}</span>
      ${c.stale && pending ? `<span class="tag warn">${esc(c.target)} is already ${esc(c.target_status || "gone")}</span>` : ""}${state}</div>
    <div class="statement md">${md(c.statement)}</div>
    <dl>${how}</dl>
    <details class="why"><summary>Why it can be closed</summary><div class="md">${md(c.rationale)}</div></details>
    ${c.decision_note ? `<details class="why" open><summary>Your note</summary><div class="md">${md(c.decision_note)}</div></details>` : ""}
    ${pending ? `<div class="acts"><button class="btn small" data-act="close-q" ${c.stale ? "disabled" : ""}>Review & close…</button>
      <a class="btn ghost small" href="#/obj/${c.target}">Open ${c.target}</a>
      <button class="btn ghost small danger" data-act="reject">Reject</button></div>` : ""}
  </div>`;
}

async function acceptResolve(c) {
  const refs = (c.refs || []).filter((r) => r.status === "pending");
  const refCands = refs.map((r) => S.cands.find((x) => x.id === r.id) || r);
  let tq = {};
  try { tq = await api("GET", `/api/objects/${c.target}/links`); } catch (err) { /* shown below */ }
  const html = `<div class="flabel">${c.target}${tq.statement ? "" : " (not found)"}</div><div class="md small cur">${md(tq.statement || "")}</div>` +
    `<p>Close as <b>${esc(c.resolution)}</b>. Nothing is deleted; ${c.target} fades in the tree and the AI sees it as one line with this conclusion. You can reopen it.</p>` +
    (c.resolution === "merged" ? `<p>Merge into ${idl(c.merged_into)} — its discussions and links will show up there.</p>` : "") +
    (c.resolution === "answered" && refCands.length ? `<div class="banner info"><strong>Accepted together.</strong> The answer is still a pending candidate; confirming accepts it first, then closes ${c.target} with the object it becomes.` +
      refCands.map((r) => `<div class="card" style="margin-top:8px"><div class="card-top"><span class="tag ${r.kind}">${KIND[r.kind] || r.kind}</span><span class="id">${r.id}</span></div><div class="md small">${md(r.statement || "")}</div>
        ${r.origin === "unclear" ? select(`Who raised ${r.id} first?`, "origin_" + r.id, { human: "Me", ai: "The AI" }, "human") : ""}</div>`).join("") + `</div>` : "") +
    (c.resolution === "decided" ? field("The decision", "decision", c.statement, "what you chose, why, and what would make you revisit it — recorded as a research decision", 6) : "") +
    (c.origin === "unclear" ? select("Who proposed closing it?", "origin", { human: "Me", ai: "The AI" }, "human") : "") +
    `<details class="why"><summary>Why it can be closed</summary><div class="md small">${md(c.rationale)}</div></details>`;
  const r = await ask(`Close ${c.target} — ${c.id}`, html, refCands.length ? "Accept both & close" : "Close question", { wide: true });
  if (!r) return;
  try {
    for (const ref of refCands) {
      const x = await api("POST", `/api/candidates/${ref.id}/accept`, r["origin_" + ref.id] ? { origin: r["origin_" + ref.id] } : {});
      toast(`Accepted ${ref.id} as ${x.id}.`);
    }
    const body = {};
    if (r.decision !== undefined) body.decision = r.decision;
    if (r.origin) body.origin = r.origin;
    await api("POST", `/api/candidates/${c.id}/accept`, body);
    toast(`${c.target} closed as ${c.resolution}.`);
  } catch (err) { fail(err); }
  after();
}

// Merging insights (§5.16.3): you pick the firmness; grounding and "informs" are the union of the merged ones.
async function acceptMerge(c) {
  if (!S.overview) await loadOverview();
  const olds = (c.supersedes || []).map((id) => (S.overview.insights || []).find((i) => i.id === id) || { id, body: "", firmness: "" });
  const html = `<p>Accepting creates one new insight and marks ${esc(fmtv(c.supersedes))} as merged into it. Nothing is withdrawn — assumptions derived from them still stand.</p>` +
    olds.map((i) => `<div class="card"><div class="card-top"><span class="id">${i.id}</span>${i.firmness ? `<span class="tag insight">${FIRM[i.firmness] || i.firmness}</span>` : ""}</div><div class="md small">${md(mainText(i.body).replace(/（来由见候选 C\d+。?）/g, ""))}</div></div>`).join("") +
    `<div class="flabel" style="margin-top:12px">Merged</div><textarea name="statement" rows="5">${esc(c.statement)}</textarea>` +
    firmSel(c.firmness || "hunch") +
    (c.origin === "unclear" ? select("Who raised it first?", "origin", { human: "Me", ai: "The AI" }, "human") : "") +
    `<details class="why"><summary>Why merge</summary><div class="md small">${md(c.rationale)}</div></details>`;
  const r = await ask(`Merge ${fmtv(c.supersedes)} — ${c.id}`, html, "Merge", { wide: true });
  if (!r) return;
  const changes = { firmness: r.firmness };
  if ((r.statement || "").trim() !== (c.statement || "").trim()) changes.statement = r.statement;
  try {
    const x = await api("POST", `/api/candidates/${c.id}/accept`, { changes, ...(r.origin ? { origin: r.origin } : {}) });
    toast(`Merged into ${x.id}.`);
    after();
    return x.id;
  } catch (err) { fail(err); }
  after();
}

// A revision changes an existing object in place: same id, version +1, old wording kept (§5.15.3)
function revisionCard(c) {
  const pending = c.status === "pending";
  const state = c.status === "accepted" ? `<span class="tag plain">Accepted → ${idl(c.promoted_to)}</span>`
    : c.status === "rejected" ? `<span class="tag plain">Rejected</span>` : "";
  return `<div class="card ${pending ? "" : "dim"} ${c.stale && pending ? "flagged" : ""}" data-id="${c.id}">
    <div class="card-top"><span class="tag plain">Revision</span><span class="id">${c.id}</span>
      <span>revises ${idl(c.target)}${c.target_type ? ` (${TYPE[c.target_type] || c.target_type})` : ""} · drafted on v${esc(c.base_revision)} · ${srcLine(c)} · proposed by ${ORIGIN[c.origin] || c.origin}</span>
      ${c.stale && pending ? `<span class="tag warn" title="Another revision of ${c.target} was accepted after this was drafted">Drafted on an old version (now v${c.target_revision})</span>` : ""}${state}</div>
    <div class="statement md">${md(c.statement)}</div>
    ${c.maturity ? `<dl><dt>Suggested maturity</dt><dd>${esc(MATURITY[c.maturity] || c.maturity)} — you decide when confirming</dd></dl>` : ""}
    <details class="why"><summary>Why this revision</summary><div class="md">${md(c.rationale)}</div></details>
    ${c.decision_note ? `<details class="why" open><summary>Your note</summary><div class="md">${md(c.decision_note)}</div></details>` : ""}
    ${pending ? `<div class="acts"><button class="btn small" data-act="revise">Compare & accept…</button>
      <a class="btn ghost small" href="#/obj/${c.target}">Open ${c.target}</a>
      <button class="btn ghost small danger" data-act="reject">Reject</button></div>` : ""}
  </div>`;
}

function reviewCard(r) {
  const open = r.status === "open";
  const ev = { hypothesis_refuted: "was refuted", assumption_invalidated: "was invalidated", insight_withdrawn: "was withdrawn" }[r.event] || r.event;
  return `<div class="card ${open ? "flagged" : "dim"}" data-review="${r.id}">
    <div class="card-top"><span class="tag warn">Re-examine</span><span class="id">${idl(r.target)}</span>
      <span>because ${idl(r.trigger)} ${ev} · distance ${r.depth}</span>
      ${open ? "" : `<span class="tag plain">${r.status === "resolved" ? "Adjusted" : "Not affected"}</span>`}</div>
    <div class="statement small md">${md(r.body)}</div>
    ${open ? `<div class="acts"><button class="btn small" data-act="resolved">I adjusted it</button>
      <button class="btn ghost small" data-act="dismissed">Not affected</button>
      <button class="btn ghost small" data-act="goto">Open ${r.target}</button></div>` : ""}
  </div>`;
}

function paperRef(p) {
  if (!p) return "";
  const href = p.arxiv ? `https://arxiv.org/abs/${p.arxiv}` : p.doi ? `https://doi.org/${p.doi}` : p.url;
  const lbl = p.arxiv ? `arXiv:${p.arxiv}` : p.doi ? `doi:${p.doi}` : "link";
  return href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(lbl)}</a>` : "";
}

function requestCard(r) {
  const p = r.paper_info || {}, t = r.task_info;
  const open = r.status === "open";
  return `<div class="card ${open ? "flagged" : "dim"}" data-request="${r.id}">
    <div class="card-top"><span class="tag warn">Paper</span><span class="id">${r.id}</span><span>${esc(p.id)} · ${paperRef(p)}</span>
      ${open ? (r.blocking ? `<span class="tag warn">A task is waiting on it</span>` : "") : `<span class="tag plain">${r.status === "fulfilled" ? "Uploaded" : "Not available"}</span>`}</div>
    <div class="statement"><strong>${esc(p.title || "")}</strong>${p.year ? ` <span class="meta">(${esc(p.year)}${p.venue ? ", " + esc(p.venue) : ""})</span>` : ""}</div>
    <div class="md small">${md(r.body.replace(/^## .*\n/m, ""))}</div>
    ${t ? `<div class="meta" style="margin-top:6px">Requested by ${t.id} · ${esc(T_KIND[t.kind] || t.kind)} · ${esc(t.goal)}</div>` : ""}
    ${open ? `<div class="acts"><label class="btn small" style="cursor:pointer">Upload PDF<input type="file" accept="application/pdf,.pdf" data-act="upload" hidden></label>
      <button class="btn ghost small" data-act="dismiss">Can't get it</button></div>` : ""}
  </div>`;
}

function bindRequests(body) {
  $$("[data-request] input[data-act=upload]", body).forEach((inp) => inp.onchange = async () => {
    const id = inp.closest(".card").dataset.request, f = inp.files[0];
    if (!f) return;
    toast(`Uploading ${f.name}…`);
    try {
      const r = await fetch(`/api/requests/${id}/upload`, { method: "POST", headers: { "Content-Type": "application/pdf" }, body: f });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || r.statusText);
      toast(`${id}: indexed ${d.anchors} paragraphs${d.resumed ? `, resumed ${d.resumed} task(s)` : ""}.`);
    } catch (e) { fail(e); }
    loadInbox();
  });
  $$("[data-request] [data-act=dismiss]", body).forEach((b) => b.onclick = async () => {
    const id = b.closest(".card").dataset.request;
    const f = await ask(`${id}: can't get it`, field("Why not?", "reason", "", "the waiting task is told to work from the abstract", 3), "Save");
    if (!f) return;
    try { await api("POST", `/api/requests/${id}/dismiss`, f); } catch (e) { fail(e); }
    loadInbox();
  });
}

function renderInbox(force) {
  // Live updates must not wipe a form you're typing in; they're applied when you finish.
  if (S.editing && !force) { S.inboxStale = true; return; }
  S.inboxStale = false;
  $$("#inbox-seg button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.inboxView));
  const body = $("#inbox-body");
  if (S.inboxView === "cands") {
    const pend = S.cands.filter((c) => c.status === "pending");
    body.innerHTML = pend.map(candCard).join("") ||
      `<div class="empty">No candidates waiting. Distill a discussion and its research content lands here.</div>`;
  } else if (S.inboxView === "reviews") {
    const open = S.reviews.filter((r) => r.status === "open");
    body.innerHTML = (open.length ? `<p class="lede" style="margin:0 0 16px">These objects are linked to something that was overturned. Nothing was changed automatically — decide for each one.</p>` : "") +
      (open.map(reviewCard).join("") || `<div class="empty">Nothing to re-examine. When a hypothesis is refuted or an assumption invalidated, everything linked to it shows up here.</div>`);
  } else if (S.inboxView === "requests") {
    const open = S.requests.filter((r) => r.status === "open");
    const done = S.requests.filter((r) => r.status !== "open").reverse();
    body.innerHTML = (open.length ? `<p class="lede" style="margin:0 0 16px">The AI decided these papers are worth reading in full but can't get them. Fetch the PDF through your library access and upload it here — the suspended task picks up where it stopped.</p>` : "") +
      (open.map(requestCard).join("") || `<div class="empty">No papers waiting. When the AI needs a paywalled paper, it asks here instead of guessing.</div>`) +
      (done.length ? `<h2 style="margin:28px 0 12px">Handled</h2>` + done.map(requestCard).join("") : "");
    bindRequests(body);
  } else {
    const done = S.cands.filter((c) => c.status !== "pending").reverse();
    const revs = S.reviews.filter((r) => r.status !== "open").reverse();
    body.innerHTML = ((done.length ? `<h2 style="margin:0 0 12px">Decided candidates</h2>` + done.map(candCard).join("") : "") +
      (revs.length ? `<h2 style="margin:28px 0 12px">Resolved re-examinations</h2>` + revs.map(reviewCard).join("") : "")) ||
      `<div class="empty">No history yet.</div>`;
  }
  $("#ids-list").innerHTML = knownIds().map(([id, t]) => `<option value="${esc(id)}">${esc(t)}</option>`).join("");
  $$(".card[data-id]:not(.editing) button[data-act]", body).forEach((b) => b.onclick = () =>
    candAct(b.dataset.act, S.cands.find((c) => c.id === b.closest(".card").dataset.id)));
  const ed = $(".card.editing", body);
  if (ed) bindEditor(ed);
  $$(".card[data-review] button", body).forEach((b) => b.onclick = () => reviewAct(b.dataset.act, S.reviews.find((r) => r.id === b.closest(".card").dataset.review)));
}

function openEditor(c, mode) {
  const draft = mode === "edit"
    ? Object.fromEntries(["kind", "statement", "rationale", "origin", "origin_note", "relied_on_by", "derived_from",
      "falsifier", "validation", "confidence", "maturity", "parent", "importance", "firmness", "basis", "basis_note", "informs", "change_mind", "supersedes"]
      .map((k) => [k, c[k] ?? (["relied_on_by", "basis", "informs", "supersedes"].includes(k) ? [] : "")]))
    : { reason: "" };
  S.editing = { id: c.id, mode, draft };
  renderInbox(true);
}
function closeEditor() {
  S.editing = null;
  renderInbox(true);
}

// Read the live form back into the draft (so switching kind keeps what you typed).
function readForm(card) {
  const d = S.editing.draft;
  $$("textarea[data-k]", card).forEach((t) => { d[t.dataset.k] = t.value; });
  $$(".tokens", card).forEach((t) => {
    const vals = $$(".chip", t).map((c) => c.firstChild.textContent);
    const typed = $("input", t).value.trim();
    if (typed) vals.push(...typed.split(/[\s,]+/).filter(Boolean));
    d[t.dataset.k] = t.dataset.single === "1" ? (vals[0] || "") : [...new Set(vals)];
  });
  return d;
}

function showErr(card, msg) {
  const e = $(".ferr", card);
  e.textContent = msg;
  e.hidden = !msg;
}

function validateDraft(d) {
  const spec = KIND_SPEC[d.kind];
  const miss = spec.fields.filter((f) => f.req && !(Array.isArray(d[f.k]) ? d[f.k].length : String(d[f.k] || "").trim())).map((f) => f.label);
  if (!String(d.statement || "").trim()) miss.unshift("Statement");
  if (d.kind === "insight" && !(d.basis || []).length && !String(d.basis_note || "").trim() && !(d.supersedes || []).length) miss.push("Grounded in or a grounding note");
  if (d.origin === "unclear" && !String(d.origin_note || "").trim()) miss.push("Origin note");
  return miss.length ? `Still needed: ${miss.join(", ")}.` : "";
}

function bindEditor(card) {
  const ed = S.editing;
  const rerender = () => { readForm(card); renderInbox(true); };
  $$("[data-kind]", card).forEach((b) => b.onclick = () => { readForm(card); ed.draft.kind = b.dataset.kind; renderInbox(true); });
  $$(".choice", card).forEach((g) => $$("button", g).forEach((b) => b.onclick = () => {
    readForm(card);
    ed.draft[g.dataset.k] = ed.draft[g.dataset.k] === b.dataset.v && g.dataset.k !== "origin" ? "" : b.dataset.v;
    renderInbox(true);
  }));
  $$(".tokens", card).forEach((t) => {
    const inp = $("input", t);
    t.onclick = (e) => { if (e.target === t) inp.focus(); };
    inp.onkeydown = (e) => {
      if ((e.key === "Enter" || e.key === "," || e.key === " ") && inp.value.trim()) { e.preventDefault(); rerender(); }
      if (e.key === "Backspace" && !inp.value) { const last = $$(".chip", t).pop(); if (last) { last.remove(); rerender(); } }
    };
    inp.onchange = () => { if (inp.value.trim()) rerender(); };   // picked from the suggestion list
    $$("[data-rm]", t).forEach((b) => b.onclick = () => { b.parentElement.remove(); rerender(); });
  });
  $$(".quick button", card).forEach((b) => b.onclick = () => {
    const ta = $('textarea[data-k="reason"]', card);
    ta.value = (ta.value.trim() ? ta.value.trim() + "; " : "") + b.dataset.q;
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  });
  const submit = async (act, origin) => {
    try {
      if (act === "cancel") return closeEditor();
      if (act === "accept-as") {
        const r = await api("POST", `/api/candidates/${ed.id}/accept`, { origin });
        toast(`Accepted as ${r.id}.`);
      } else if (act === "confirm-reject") {
        const reason = $('textarea[data-k="reason"]', card).value.trim();
        if (!reason) return showErr(card, "Write a reason — it's what stops future distills from proposing this again.");
        await api("POST", `/api/candidates/${ed.id}/reject`, { reason });
        toast(`Rejected ${ed.id}.`);
      } else {
        const d = readForm(card);
        const err = validateDraft(d);
        if (err && act === "save-accept") return showErr(card, err);
        const fields = ["kind", "statement", "rationale", "origin", "origin_note", ...KIND_SPEC[d.kind].fields.map((f) => f.k)];
        const changes = Object.fromEntries(fields.map((k) => [k, Array.isArray(d[k]) ? d[k].join(", ") : (d[k] ?? "")]));
        await api("POST", `/api/candidates/${ed.id}/update`, { changes });
        if (act === "save-accept") {
          const r = await api("POST", `/api/candidates/${ed.id}/accept`, {});
          toast(`Saved and accepted as ${r.id}.`);
        } else toast(err ? `Saved. Before accepting: ${err.replace("Still needed: ", "")}` : "Saved. Still waiting for you to accept it.");
      }
      S.editing = null;
      await loadInbox();
      renderInbox(true);
    } catch (e) { showErr(card, e.message); }
  };
  $$("button[data-act]", card).forEach((b) => b.onclick = () => submit(b.dataset.act, b.dataset.origin));
  card.onkeydown = (e) => {
    if (e.key === "Escape") { e.preventDefault(); closeEditor(); }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submit(ed.mode === "reject" ? "confirm-reject" : "save");
    }
  };
  const first = ed.mode === "reject" ? $('textarea[data-k="reason"]', card) : null;
  if (first) first.focus();
}

async function candAct(act, c) {
  if (act === "edit") return openEditor(c, "edit");
  if (act === "reject") return openEditor(c, "reject");
  if (act === "revise") { await reviewRevision(c.id); return loadInbox(); }
  if (act === "close-q") return acceptResolve(c);
  if ((act === "accept" || act === "accept-discuss") && c.supersedes && c.supersedes.length) {
    const id = await acceptMerge(c);
    if (id && act === "accept-discuss") await discussThis(id);
    return;
  }
  if (act === "accept-discuss") {
    if (c.origin === "unclear") { toast("Pick who raised it first (Accept), then discuss it from its page.", "warn"); return openEditor(c, "accept"); }
    try {
      const r = await api("POST", `/api/candidates/${c.id}/accept`, {});
      toast(`Accepted as ${r.id}.`);
      await discussThis(r.id);
    } catch (err) { fail(err); openEditor(c, "edit"); return; }
    return loadInbox();
  }
  if (act === "accept") {
    if (c.origin === "unclear") return openEditor(c, "accept");
    try {
      const r = await api("POST", `/api/candidates/${c.id}/accept`, {});
      toast(`Accepted as ${r.id}.`);
    } catch (err) { fail(err); openEditor(c, "edit"); return; }
    loadInbox();
  }
}

async function reviewAct(act, r) {
  if (act === "goto") return go(`#/obj/${r.target}`);
  const f = await ask(act === "resolved" ? `${r.target}: adjusted` : `${r.target}: not affected`,
    field(act === "resolved" ? "What did you change?" : "Why does it still stand?", "note", "", "kept as a record", 3), "Save");
  if (!f) return;
  try { await api("POST", `/api/reviews/${r.id}/resolve`, { status: act, note: f.note }); } catch (err) { fail(err); }
  loadInbox();
}

// ================================================================ RESEARCH
async function loadOverview() {
  const o = await api("GET", "/api/overview");
  S.overview = o;
  $("#project-title").textContent = o.project.title || "Untitled project";
  $("#project-lede").textContent = firstLine(o.project.body);
  if (o.mode) renderMode(o.mode);
  if (S.page === "research") renderResearch();
}

const dl = (rows) => {
  const r = rows.filter(([, v]) => v && fmtv(v));
  return r.length ? `<dl>${r.map(([k, v]) => `<dt>${k}</dt><dd>${idl(v)}</dd>`).join("")}</dl>` : "";
};
const section = (id, title, count, desc, content, action = "") => `
  <section class="rsec" id="${id}">
    <div class="rsec-head"><div><h2>${title}<span class="count">${count ?? ""}</span></h2>${desc ? `<p>${desc}</p>` : ""}</div>${action}</div>
    ${content}
  </section>`;

const inBatch = (id) => (S.mode && (S.mode.batch || []).includes(id)) ? `<span class="tag insight">In validation</span>` : "";

// Collapsed/expanded state per browser. Storage can throw (private mode, blocked site data): fall back to the default.
const fold = {
  get: (k, def) => { try { const v = localStorage.getItem("ar.fold." + k); return v === null ? def : v === "1"; } catch { return def; } },
  set: (k, v) => { try { localStorage.setItem("ar.fold." + k, v ? "1" : "0"); } catch { /* private mode */ } },
};
const bindFolds = (root) => $$("details[data-fold]", root).forEach((d) => d.ontoggle = () => fold.set(d.dataset.fold, d.open));

function statusTag(x) {
  const t = x.type;
  if (t === "evolution") return `<span class="tag insight">Evolution</span><span class="tag plain">${TRIGGER[x.trigger] || esc(x.trigger || "")}</span>`;
  if (t === "question") return `<span class="tag question">${MATURITY[x.maturity] || x.maturity}</span>` +
    (x.status && x.status !== "open" && x.status !== "withdrawn" ? `<span class="tag ${x.status === "merged" ? "plain" : "hypothesis"}">${Q_STATUS[x.status] || esc(x.status)}</span>` : "") +
    (x.active === "true" || x.active === true ? `<span class="tag insight" title="You marked it as something you're thinking about now">Active</span>` : "");
  if (t === "assumption") return `<span class="tag assumption">${A_STATUS[x.status] || x.status}</span>${x.fragile === "true" || x.fragile === true ? `<span class="tag warn">Fragile</span>` : ""}`;
  if (t === "hypothesis") return `<span class="tag hypothesis">${H_STATUS[x.status] || x.status}</span>`;
  if (t === "uncertainty") return `<span class="tag uncertainty">${esc(x.importance)} importance</span>${x.status !== "open" ? `<span class="tag plain">${U_STATUS[x.status] || x.status}</span>` : ""}`;
  if (t === "insight") return `<span class="tag insight">${FIRM[x.firmness] || x.firmness}</span>${x.starred === "true" && x.status === "active" ? `<span class="tag insight" title="Starred: the AI sees it in full">★</span>` : ""}${x.status !== "active" ? `<span class="tag plain">${x.status === "superseded" ? "Superseded by " + x.superseded_by : "Abandoned"}</span>` : ""}`;
  return x.status ? `<span class="tag plain">${esc(x.status)}</span>` : "";
}

// Compact row: id, type tag, first line, link counts. The page behind it holds everything else (§5.15.4).
function orow(x, extra = "") {
  const c = x.counts || {};
  const n = (k, one, many) => c[k] ? `${c[k]} ${c[k] === 1 ? one : many}` : "";
  const bits = [c.revision > 1 ? `v${c.revision}` : "", n("evidence", "evidence", "evidence"), n("discussions", "discussion", "discussions"),
    n("related", "link", "links"), n("candidates", "pending", "pending"), n("reviews", "to re-examine", "to re-examine")].filter(Boolean);
  return `<a class="orow ${x.withdrawn ? "dim" : ""}" href="#/obj/${x.id}"><span class="id">${x.id}</span>
    <span class="tags">${statusTag(x)}${extra}${inBatch(x.id)}${x.withdrawn ? `<span class="tag plain">Withdrawn</span>` : ""}</span>
    <span class="t">${esc(firstLine(mainText(x.body)))}</span><span class="cnt">${bits.join(" · ")}</span></a>`;
}
const olist = (xs, extra) => `<div class="olist">${xs.map((x) => orow(x, extra ? extra(x) : "")).join("")}</div>`;
function listWithFolded(key, live, folded, empty, foldedTitle = "Withdrawn", extra) {
  return (live.length ? olist(live, extra) : `<div class="empty">${empty}</div>`) +
    (folded.length ? `<details class="history" data-fold="research.${key}" ${fold.get("research." + key, false) ? "open" : ""}><summary>${foldedTitle} · ${folded.length}</summary>${olist(folded, extra)}</details>` : "");
}

function renderResearch() {
  const o = S.overview;
  if (!o) return;
  const openReviews = (o.reviews || []).length;
  const top = (o.validation.errors.length ? `<div class="banner"><strong>The research state has ${o.validation.errors.length} validation error(s).</strong><br>${o.validation.errors.map(esc).join("<br>")}</div>` : "") +
    (openReviews ? `<div class="banner warn"><strong>${openReviews} object(s) need re-examination</strong> because something they are linked to was overturned. <a href="#/inbox" id="to-reviews">Open the Inbox →</a></div>` : "");
  const tree = o.tree || { nodes: {}, unplaced: [], withdrawn: [], active: [], open: [] };
  const q = treeHtml(tree);

  const ord = { settled: 0, working: 1, hunch: 2 };
  const insAll = o.insights || [];
  const starred = (x) => x.starred === "true" || x.starred === true;
  const ins = insAll.filter((x) => x.status === "active").sort((a, b) => starred(b) - starred(a) || ord[a.firmness] - ord[b.firmness]);
  const nStar = ins.filter(starred).length;
  const insHints = (ins.length && !nStar && !hint.seen("star") ? `<div class="banner info" data-hint="star"><strong>Star the insights that matter most.</strong>
      Only starred insights reach the AI in full now; the others appear as one line each (it can still open them). <div class="acts"><button class="btn ghost small" data-dismiss="star">Got it</button></div></div>` : "") +
    (nStar > 5 ? `<div class="banner warn"><strong>${nStar} insights are starred.</strong> When everything is starred, nothing is.</div>` : "");
  const insRows = ins.length ? `<div class="olist">${ins.map((x) => `<div class="starrow"><button class="star ${starred(x) ? "on" : ""}" data-istar="${x.id}" data-on="${starred(x) ? 1 : 0}" title="${starred(x) ? "Starred — click to unstar" : "Star it: the AI sees starred insights in full"}">${starred(x) ? "★" : "☆"}</button>${orow(x)}</div>`).join("")}</div>`
    : `<div class="empty">No insights yet. An insight can be just a feel for the problem — as long as you say where it comes from.</div>`;
  const insFolded = insAll.filter((x) => x.status !== "active");
  const insHtml = insHints + insRows + (insFolded.length ? `<details class="history" data-fold="research.ins" ${fold.get("research.ins", false) ? "open" : ""}><summary>How our understanding changed · ${insFolded.length}</summary>${olist(insFolded)}</details>` : "");

  const as = o.assumptions.slice().sort((a, b) => (a.status !== "unexamined") - (b.status !== "unexamined"));
  const asHtml = listWithFolded("a", as.filter((x) => !x.withdrawn), as.filter((x) => x.withdrawn), "No assumptions recorded.");
  const hs = o.hypotheses;
  const hsHtml = listWithFolded("h", hs.filter((x) => !x.withdrawn), hs.filter((x) => x.withdrawn), "No hypotheses yet.");
  const us = o.uncertainties;
  const usLive = us.filter((x) => !["resolved", "withdrawn"].includes(x.status));
  const usHtml = listWithFolded("u", usLive, us.filter((x) => ["resolved", "withdrawn"].includes(x.status)), "No open uncertainties.", "Resolved or withdrawn");
  const de = o.dead_ends.length ? olist(o.dead_ends) : `<div class="empty">No closed directions yet.</div>`;

  const bias = o.bias.length ? `<div class="card"><table class="t"><tr><th>Raised by</th><th>Hypotheses</th><th>With a verdict</th><th>Refuted</th><th>Refutation rate</th></tr>
    ${o.bias.map((b) => `<tr><td>${b.origin === "disputed" ? "Disputed origin (not counted)" : ORIGIN[b.origin] || b.origin}</td><td>${b.total}</td><td>${b.resolved}</td><td>${b.refuted}</td><td>${b.refute_rate == null ? "—" : Math.round(b.refute_rate * 100) + "%"}</td></tr>`).join("")}</table></div>`
    : `<div class="empty">No hypotheses yet.</div>`;

  $("#research-body").innerHTML = top + convergeBanners(tree, ins.length, o.tidy) +
    section("sec-question", "Research questions", tree.open.length, "The question tree. Closed questions fade and fold away with their answer on the node. ★ marks what you're thinking about now — the AI sees those in full and the rest as one line each.", q,
      `<button class="btn ghost small" data-act="tidy" title="Ask the AI to propose merging overlapping insights and closing answered or duplicate questions">Tidy up</button>`) +
    section("sec-insights", "Understanding", ins.length, "What we've come to think so far. Understanding, not evidence — it can be a feel, but it must say where it comes from.", insHtml,
      `<span class="acts-inline">${ins.length >= 2 ? `<button class="btn ghost small" data-act="merge-insights" title="Pick two or more insights and write the merged one yourself">Merge…</button>` : ""}<button class="btn small" data-act="new-insight">Add insight</button></span>`) +
    section("sec-assumptions", "Assumptions", as.filter((x) => !x.withdrawn).length, "Premises the research relies on. Unexamined ones come first — they're the dangerous ones.", asHtml) +
    section("sec-hypotheses", "Hypotheses", hs.filter((x) => !x.withdrawn).length, "Claims with an arranged test. Status only changes with evidence attached.", hsHtml) +
    section("sec-uncertainties", "Uncertainties", usLive.length, "Unknowns that discount our conclusions.", usHtml) +
    section("sec-deadends", "Dead ends", o.dead_ends.length, "Closed directions. Every session is shown all of them before exploring anything new.", de) +
    section("sec-bias", "Bias check", "", "Origin is hidden from the AI when it judges evidence. Masking is never perfect — if your hypotheses are refuted far less often than the AI's, either you're right more often, or the masking leaks.", bias);

  const tr = $("#to-reviews");
  if (tr) tr.onclick = () => { S.inboxView = "reviews"; };
  bindFolds($("#research-body"));
  const nb = $('[data-act="new-insight"]', $("#research-body"));
  if (nb) nb.onclick = newInsight;
  bindTree($("#research-body"));
  $$("[data-istar]", $("#research-body")).forEach((b) => b.onclick = () => starInsight(b.dataset.istar, b.dataset.on !== "1"));
  $$('[data-act="merge-insights"]', $("#research-body")).forEach((b) => b.onclick = () => mergeInsights([]));
  spyChips();
}

// ---------------------------------------------------------------- question tree (DESIGN §5.16)
// Remembered per browser; storage may throw (private mode), so every access is guarded.
const hint = {
  seen: (k) => { try { return localStorage.getItem("ar.hint." + k) === "1"; } catch { return false; } },
  done: (k) => { try { localStorage.setItem("ar.hint." + k, "1"); } catch { /* private mode */ } },
};

function answerLinks(n) {
  if (n.status === "answered") return `answered by ${(n.answered_by || []).map((i) => linkIds(esc(i))).join(", ")}`;
  if (n.status === "decided") return `decided in ${linkIds(esc(n.decided_by))}`;
  if (n.status === "merged") return `merged into ${linkIds(esc(n.merged_into))}`;
  return "";
}

function qnode(t, id, depth = 0) {
  const n = t.nodes[id];
  if (!n) return "";
  const closed = Q_CLOSED.includes(n.status);
  const kids = n.children.filter((k) => t.nodes[k] && !t.nodes[k].withdrawn);
  const open = fold.get("tree." + id, !closed);
  const hang = [n.insights.length ? `${n.insights.length} insight${n.insights.length === 1 ? "" : "s"}` : "",
    n.hypotheses.length ? `${n.hypotheses.length} hypothes${n.hypotheses.length === 1 ? "is" : "es"}` : "",
    n.merged_from.length ? `absorbed ${n.merged_from.join(", ")}` : ""].filter(Boolean).join(" · ");
  const star = n.main ? `<span class="star main" title="The main question">◆</span>`
    : n.status === "open" ? `<button class="star ${n.active ? "on" : ""}" data-star="${id}" data-on="${n.active ? 1 : 0}" title="${n.active ? "Active — click to unmark" : "Mark as something you're thinking about now"}">${n.active ? "★" : "☆"}</button>`
      : `<span class="star"></span>`;
  return `<div class="qnode ${closed ? "closed" : ""}" style="--d:${depth}">
    <div class="qrow">
      ${kids.length ? `<button class="caret-b" data-tree="${id}" aria-label="${open ? "Collapse" : "Expand"}">${open ? "▾" : "▸"}</button>` : `<span class="caret-b"></span>`}
      ${star}
      <a class="qmain" href="#/obj/${id}"><span class="id">${id}</span>
        <span class="tags"><span class="tag question">${MATURITY[n.maturity] || esc(n.maturity)}</span>${closed ? `<span class="tag ${n.status === "merged" ? "plain" : "hypothesis"}">${Q_STATUS[n.status]}</span>` : ""}${inBatch(id)}</span>
        <span class="t">${esc(n.text)}</span></a>
      <span class="cnt">${closed ? `<span class="ans">${answerLinks(n)}</span>${hang ? " · " : ""}` : ""}${hang}${kids.length && !open ? ` · ${kids.length} sub-question${kids.length === 1 ? "" : "s"}` : ""}</span>
    </div>
    ${kids.length && open ? `<div class="qkids">${kids.map((k) => qnode(t, k, depth + 1)).join("")}</div>` : ""}
  </div>`;
}

function treeHtml(t) {
  const root = t.main ? `<div class="qtree">${qnode(t, t.main)}</div>` : `<div class="empty">No main question.</div>`;
  const loose = t.unplaced.map((id) => {
    const n = t.nodes[id];
    return `<div class="qtree unplaced-row">${qnode(t, id)}
      ${n.suggest_parent ? `<div class="suggest"><span class="meta">Its only related question is ${linkIds(esc(n.suggest_parent))}.</span>
        <button class="btn ghost small" data-adopt="${id}" data-parent="${esc(n.suggest_parent)}">Put under ${esc(n.suggest_parent)}</button></div>` : ""}</div>`;
  }).join("");
  const unplaced = t.unplaced.length ? `<h3 class="sub">Not placed yet <span class="count">${t.unplaced.length}</span></h3>
    <p class="meta">These have no parent question. Put each under the question it belongs to — nothing is moved automatically.</p>${loose}` : "";
  const wd = t.withdrawn.length ? `<details class="history" data-fold="research.qwd" ${fold.get("research.qwd", false) ? "open" : ""}><summary>Withdrawn · ${t.withdrawn.length}</summary>
    <div class="olist">${t.withdrawn.map((id) => `<a class="orow dim" href="#/obj/${id}"><span class="id">${id}</span><span class="tags"><span class="tag plain">Withdrawn</span></span><span class="t">${esc(t.nodes[id].text)}</span><span class="cnt"></span></a>`).join("")}</div></details>` : "";
  return root + unplaced + wd;
}

function convergeBanners(t, activeInsights, tidy) {
  const out = [];
  const others = t.open.filter((id) => id !== t.main);
  if (others.length && !t.active.length && !hint.seen("active")) out.push(`<div class="banner info" data-hint="active"><strong>Mark what you're thinking about now.</strong>
    Since this update, only the main question and questions you mark active (☆ → ★) reach the AI in full; the others appear as one line each, so each discussion stays focused.
    <div class="acts"><button class="btn ghost small" data-dismiss="active">Got it</button></div></div>`);
  if (t.active.length > 3) out.push(`<div class="banner warn"><strong>${t.active.length} questions are active.</strong> When everything is active, nothing is — consider unmarking the ones you aren't working on this week.</div>`);
  if (activeInsights > 12 || t.open.length > 8) out.push(`<div class="banner info"><strong>It's getting crowded</strong> — ${activeInsights} active insights, ${t.open.length} open questions.
    Tidy up asks the AI to propose merging overlapping insights and closing questions that are already answered or duplicated. It doesn't add anything new, and every proposal waits for you in the Inbox.
    <div class="acts"><button class="btn small" data-act="tidy">Tidy up</button></div></div>`);
  if (tidy && ["queued", "running"].includes(tidy.status)) out.push(`<div class="banner info"><span class="thinking">Tidying up (${tidy.id})</span> — proposals will land in the Inbox.</div>`);
  else if (tidy && tidy.status === "done" && tidy.result_brief) out.push(`<div class="meta tidy-last">Last tidy-up ${esc(tidy.id)} · ${fmtTs(tidy.ended)}: ${linkIds(esc(tidy.result_brief))}</div>`);
  return out.join("");
}

function bindTree(root) {
  $$("[data-tree]", root).forEach((b) => b.onclick = () => { fold.set("tree." + b.dataset.tree, b.textContent !== "▾"); renderResearch(); });
  $$("[data-star]", root).forEach((b) => b.onclick = async () => {
    try { await api("POST", `/api/questions/${b.dataset.star}/active`, { active: b.dataset.on !== "1" }); hint.done("active"); } catch (err) { fail(err); }
    loadOverview();
  });
  $$("[data-adopt]", root).forEach((b) => b.onclick = async () => {
    try { await api("POST", `/api/questions/${b.dataset.adopt}/parent`, { parent: b.dataset.parent }); toast(`${b.dataset.adopt} is now under ${b.dataset.parent}.`); } catch (err) { fail(err); }
    loadOverview();
  });
  $$("[data-dismiss]", root).forEach((b) => b.onclick = () => { hint.done(b.dataset.dismiss); renderResearch(); });
  $$('[data-act="tidy"]', root).forEach((b) => b.onclick = tidyUp);
}

async function tidyUp() {
  const r = await ask("Tidy up?", `<p>A separate task reads every active insight and every open question in full, and proposes only two things: <b>merging</b> insights that say the same thing, and <b>closing</b> questions that are already answered or duplicate another. It adds nothing new, and it's fine for it to find nothing.</p>
    <p class="meta">Every proposal waits in the Inbox for you. Costs about one distill.</p>`, "Tidy up");
  if (!r) return;
  try {
    const x = await api("POST", "/api/tidy", {});
    toast(x.task ? `Tidy-up queued (${x.task.id}).` : "A tidy-up is already queued or running.");
  } catch (err) { fail(err); }
  loadOverview();
}

// ---------------------------------------------------------------- object actions (used by the object page)
const firmSel = (cur) => select("Firmness", "firmness", { hunch: "Hunch — a feel", working: "Working — an understanding we act on", settled: "Settled — solid" }, cur);
const after = () => { loadOverview(); loadInbox(); if (S.page === "obj" && S.objId) renderObject(S.objId); };

async function newInsight() {
  const r = await ask("Add an insight",
    field("The insight", "statement", "", "can be descriptive — a feel is fine", 4) + firmSel("hunch") +
    field("Grounded in", "basis", "", "ids from the state, comma-separated, e.g. DS001, H002") +
    field("Or a grounding note", "basis_note", "", "a source outside the state, e.g. years of assembly work") +
    field("Informs", "informs", "", "optional · ids it shapes") +
    field("Would change if", "change_mind", "", "optional"), "Add");
  if (!r) return;
  try { const x = await api("POST", "/api/insights", r); toast(`Added ${x.id}.`); } catch (err) { fail(err); }
  loadOverview();
}
async function starInsight(id, on) {
  try { await api("POST", `/api/insights/${id}/star`, { starred: on }); hint.done("star"); } catch (err) { fail(err); }
  after();
}

// Manual merge (§5.17.3): you pick the insights and write the merged one yourself. No candidate, no quota.
async function mergeInsights(pre) {
  if (!S.overview) await loadOverview();
  const act = (S.overview.insights || []).filter((i) => i.status === "active");
  if (act.length < 2) return toast("Merging needs at least two active insights.", "warn");
  const r = await ask("Merge insights", `<p>Pick two or more and write the merged insight. The picked ones are marked as merged into the new one — nothing is withdrawn, and assumptions derived from them still stand. Grounding and “informs” are combined automatically.</p>
    <div class="checks">${act.map((i) => `<label><input type="checkbox" name="supersedes" value="${i.id}" ${pre.includes(i.id) ? "checked" : ""}><span><span class="id">${i.id}</span> <span class="tag insight">${FIRM[i.firmness] || i.firmness}</span>${i.starred === "true" ? " ★" : ""}<br>${esc(mainText(i.body).replace(/（来由见候选 C\d+。?）/g, "").slice(0, 400))}</span></label>`).join("")}</div>` +
    field("The merged insight", "statement", "", "write it so it reads on its own — someone who wasn't in the discussion should get it", 6) + firmSel("working") +
    field("Would change if", "change_mind", "", "optional") + field("Why merge", "note", "", "optional"), "Merge", { wide: true });
  if (!r) return;
  if ((r.supersedes || []).length < 2) return toast("Pick at least two insights to merge.", "warn");
  if (!(r.statement || "").trim()) return toast("Write the merged insight.", "warn");
  try {
    const x = await api("POST", "/api/insights/merge", r);
    toast(`Merged ${r.supersedes.join(", ")} into ${x.id}.`);
    go(`#/obj/${x.id}`);
  } catch (err) { fail(err); }
  after();
}

async function reviseInsight(x) {
  const r = await ask(`Revise ${x.id}`, `<p>Nothing is overwritten. A new insight replaces this one; the old one stays in the history and becomes part of the new one's grounding.</p>` +
    field("Revised insight", "statement", mainText(x.body), "", 4) + firmSel(x.meta.firmness) +
    field("Would change if", "change_mind", x.meta.change_mind || "") +
    field("Why revise", "note", ""), "Revise");
  if (!r) return;
  try { const n = await api("POST", `/api/insights/${x.id}/revise`, r); toast(`Revised as ${n.id}.`); go(`#/obj/${n.id}`); } catch (err) { fail(err); }
  after();
}
async function setMaturity(x) {
  const r = await ask(`Maturity of ${x.id}`, `<p><b>Vague</b>: a direction, but you can't yet say what's outside it. <b>Scoped</b>: you can say what's studied, what isn't, and what's taken as given. <b>Formalized</b>: you can say what's measured, in what setting, and what result would answer it — and the vaguer a question is, the more it helps to Evolve it.</p>` +
    select("Maturity", "maturity", { vague: "Vague", scoped: "Scoped — boundaries are clear", formalized: "Formalized — measurable definition" }, x.meta.maturity), "Save");
  if (!r) return;
  try { await api("POST", `/api/questions/${x.id}/maturity`, r); toast(`${x.id} is now ${r.maturity}.`); } catch (err) { fail(err); }
  after(); loadMode();
}
async function groundCheck(id) {
  const r = await ask(`Ground check ${id}?`, `<p>A separate task searches for prior work and the strongest counter-evidence, then annotates ${id}. It never rewrites it. It can't see who proposed ${id}.</p>`, "Queue it");
  if (!r) return;
  try { await api("POST", "/api/grounding", { target: id }); toast(`Ground check for ${id} queued.`); } catch (err) { fail(err); }
}
async function invalidate(id) {
  const r = await ask(`Invalidate ${id}`, `<p>Use this when the premise doesn't hold. Everything linked to it goes to the Inbox for re-examination. Nothing is changed automatically. (If it's merely no longer relevant, withdraw it instead.)</p>` +
    field("Why doesn't this premise hold anymore?", "reason", "", "", 4), "Invalidate");
  if (!r) return;
  try {
    const x = await api("POST", `/api/assumptions/${id}/invalidate`, r);
    toast(x.reviews.length ? `Invalidated. ${x.reviews.length} linked object(s) need re-examination.` : "Invalidated. Nothing depended on it.", x.reviews.length ? "warn" : "info");
  } catch (err) { fail(err); }
  after();
}
async function withdrawObj(x) {
  const t = x.meta.type;
  if (t === "insight") {
    const r = await ask(`Abandon ${x.id}`, `<p>Assumptions derived from it will be flagged for re-examination.</p>` + field("Reason", "reason", "", "", 3), "Abandon");
    if (!r) return;
    try { await api("POST", `/api/insights/${x.id}/abandon`, r); } catch (err) { fail(err); }
    return after();
  }
  const to = { question: "withdrawn", assumption: "retired", hypothesis: "abandoned", uncertainty: "withdrawn" }[t];
  const r = await ask(`Withdraw ${x.id}`, `<p>For things that are <b>no longer relevant</b> — not wrong. ${x.id} becomes <i>${to}</i>; every link to it stays, nothing is sent for re-examination, and you can restore it later. The AI stops seeing it, except for one line that keeps it from being proposed again.</p>` +
    field("Why is it no longer relevant?", "reason", "", "required · recorded as a decision", 3), "Withdraw");
  if (!r) return;
  try { const d = await api("POST", `/api/objects/${x.id}/withdraw`, r); toast(`${x.id} withdrawn (${d.decision}).`); } catch (err) { fail(err); }
  after();
}
async function restoreObj(x) {
  const r = await ask(`Restore ${x.id}`, `<p>It goes back to <i>${esc(x.withdrawn.from)}</i>. Recorded as a decision.</p>` + field("Why bring it back?", "reason", "", "optional", 2), "Restore");
  if (!r) return;
  try { await api("POST", `/api/objects/${x.id}/restore`, r); toast(`${x.id} restored.`); } catch (err) { fail(err); }
  after();
}
async function undoAccept(x) {
  const src = (x.provenance || {}).source;
  const r = await ask(`Undo accepting ${x.id}?`, `<p>Nothing uses ${x.id} yet, so it can be taken back: ${x.id} is deleted, ${esc(src)} returns to the Inbox as pending, and the undo is recorded as a decision. The id ${x.id} won't be reused.</p>` +
    field("Why?", "reason", "", "optional", 2), "Undo accept");
  if (!r) return;
  try { const d = await api("POST", `/api/objects/${x.id}/undo-accept`, r); toast(`${x.id} removed; ${d.candidate} is pending again.`); go("#/inbox"); } catch (err) { fail(err); }
  loadOverview(); loadInbox();
}
async function discussThis(id) {
  try {
    const r = await api("POST", "/api/discussions", { focus: id });
    toast(`Opened ${r.id}, focused on ${id}.`);
    go(`#/chat/${r.id}`);
    setTimeout(() => input.focus(), 50);
  } catch (err) { fail(err); }
}

// ---------------------------------------------------------------- closing questions (DESIGN §5.16.1)
function closedBanner(x) {
  const q = x.question;
  const out = [];
  if (q && Q_CLOSED.includes(q.status)) {
    const what = q.status === "answered" ? `Answered by ${q.answered_by.map((a) => `${linkIds(esc(a.id))} — ${esc(a.text)}`).join("; ")}`
      : q.status === "decided" ? `Decided (${linkIds(esc(q.decided_by))}): ${esc(q.decision || "")}`
        : `Merged into ${linkIds(esc(q.merged_into))} — its discussions and links show up there.`;
    out.push(`<div class="banner ok"><strong>${Q_STATUS[q.status]}.</strong> ${what}<br><span class="meta">Nothing was deleted. The AI now sees it as one line with this conclusion, so it won't raise it again. Reopen it if it turns out not to be settled.</span></div>`);
  }
  if (x.merged_into) out.push(`<div class="banner info"><strong>Merged into ${linkIds(esc(x.merged_into))}.</strong> Not withdrawn — the same understanding, said together with others. Anything derived from it still stands.</div>`);
  if (x.derived_merged) out.push(`<div class="banner info">Derived from ${linkIds(esc(x.derived_merged.from))}, which has been merged into ${linkIds(esc(x.derived_merged.into))}. The link isn't rewritten; nothing needs re-examining.</div>`);
  return out.join("");
}

function questionOptions(exclude) {
  const t = (S.overview || {}).tree || { nodes: {} };
  return Object.values(t.nodes).filter((n) => !exclude.includes(n.id) && !n.withdrawn)
    .map((n) => [n.id, `${n.id} · ${n.text.slice(0, 70)}${n.status !== "open" ? ` (${Q_STATUS[n.status]})` : ""}`]);
}

async function moveQuestion(x) {
  if (!S.overview) await loadOverview();
  const q = x.question;
  const opts = Object.fromEntries([["", "— Not placed (no parent) —"], ...questionOptions([x.id])]);
  const r = await ask(`Move ${x.id} in the tree`, `<p>Pick the question ${x.id} is a sub-question of. <i>Related</i> links stay as they are — only the tree changes.</p>` +
    select("Parent question", "parent", opts, q.parent || q.suggest_parent || ""), "Move");
  if (!r) return;
  try { await api("POST", `/api/questions/${x.id}/parent`, { parent: r.parent }); toast(r.parent ? `${x.id} is now under ${r.parent}.` : `${x.id} is no longer placed.`); } catch (err) { fail(err); }
  after();
}

async function resolveQuestion(x) {
  if (!S.overview) await loadOverview();
  const o = S.overview;
  const answers = [...(o.insights || []).filter((i) => i.status === "active"), ...(o.hypotheses || []).filter((h) => !h.withdrawn && h.status !== "abandoned")]
    .map((a) => `<label><input type="checkbox" name="answered_by" value="${a.id}"><span><span class="id">${a.id}</span> ${esc(firstLine(mainText(a.body)).slice(0, 140))}</span></label>`).join("");
  const targets = Object.fromEntries(questionOptions([x.id]).filter(([id]) => (o.tree.nodes[id] || {}).status === "open"));
  const r = await ask(`Close ${x.id}`, `<p>Closing deletes nothing: ${x.id} fades in the tree, and the AI sees it as one line with its conclusion so it won't raise it again. You can reopen it any time.</p>` +
    select("How is it settled?", "resolution", RESOLUTION, "answered") +
    `<div class="res-part" data-res="answered"><label>Answered by <span class="hint">— the insights or hypotheses that answer it</span></label><div class="checks">${answers || `<div class="meta">No active insights or hypotheses yet. Record the answer as an insight first.</div>`}</div></div>` +
    `<div class="res-part" data-res="decided" hidden>${field("The decision", "decision", "", "what you chose, why, and what would make you revisit it — recorded as a research decision", 5)}</div>` +
    `<div class="res-part" data-res="merged" hidden>${select("Merge into", "merged_into", targets, Object.keys(targets)[0])}<p class="meta">Its discussions and links will show up on that question's page.</p></div>` +
    field("Why can it be closed now?", "reason", "", "required", 3), "Close question", { wide: true });
  if (!r) return;
  const body = { resolution: r.resolution, reason: r.reason, answered_by: r.answered_by || [], merged_into: r.merged_into, decision: r.decision };
  try { const d = await api("POST", `/api/questions/${x.id}/resolve`, body); toast(`${x.id} closed as ${r.resolution} (${d.decision}).`); } catch (err) { fail(err); }
  after();
}
// Show only the part of the close dialog that matches the chosen resolution.
document.addEventListener("change", (e) => {
  if (e.target.name !== "resolution" || !e.target.closest("#dlg-body")) return;
  $$(".res-part", $("#dlg-body")).forEach((p) => { p.hidden = p.dataset.res !== e.target.value; });
});

async function reopenQuestion(x) {
  const r = await ask(`Reopen ${x.id}`, `<p>It goes back to open. The earlier conclusion stays in its history and in the decision log.</p>` +
    field("Why reopen it?", "reason", "", "required", 3), "Reopen");
  if (!r) return;
  try { await api("POST", `/api/questions/${x.id}/reopen`, r); toast(`${x.id} reopened.`); } catch (err) { fail(err); }
  after();
}

// ---------------------------------------------------------------- revision review (DESIGN §5.15.3)
async function reviewRevision(cid) {
  let pv;
  try { pv = await api("GET", `/api/candidates/${cid}/revision`); } catch (err) { fail(err); return false; }
  const c = S.cands.find((y) => y.id === cid) || {};
  const isQ = pv.type === "question";
  const cur = pv.current.meta;
  const fieldRows = pv.changes.filter((ch) => ch.field !== "statement" && ch.field !== "maturity")
    .map((ch) => `<tr><td>${esc(ch.field)}</td><td>${idl(ch.old)}</td><td>${idl(ch.new)}</td></tr>`).join("");
  const suggested = (pv.proposed.fields || {}).maturity;
  const html =
    (pv.stale ? `<div class="banner warn"><strong>Drafted on an older version.</strong> ${cid} was written against v${pv.base_revision}; ${pv.target} is now v${pv.revision} because another revision was accepted meanwhile. Compare with the current text below. Nothing is merged automatically — confirming replaces the current text with this one.</div>` : "") +
    (pv.falsifier_evidence ? `<div class="banner warn"><strong>This moves the goalposts.</strong> It changes what would refute ${pv.target}, and ${pv.falsifier_evidence} evidence item(s) were judged against the old condition. They stay attached and are marked with the version they were judged under; the change is written into the decision.</div>` : "") +
    (pv.withdrawn ? `<div class="fnote">${pv.target} is currently withdrawn. Revising it doesn't restore it.</div>` : "") +
    `<div class="diff"><div><div class="flabel">Current · v${pv.revision}</div><div class="md small cur">${md(pv.current.statement)}</div></div>
      <div><div class="flabel">Proposed · v${pv.revision + 1} <span>edit freely</span></div><textarea name="statement" rows="9">${esc(pv.proposed.statement)}</textarea></div></div>` +
    (fieldRows ? `<table class="t diff-t"><tr><th>Field</th><th>Now</th><th>Proposed</th></tr>${fieldRows}</table>` : "") +
    (isQ ? select(`Maturity${suggested && suggested !== cur.maturity ? ` — the AI suggests ${MATURITY[suggested]}` : ""}`, "maturity",
      { vague: "Vague — can't yet say what's outside it", scoped: "Scoped — says what's in, what's out, what's given", formalized: "Formalized — says what's measured and what answers it" },
      suggested || cur.maturity) + `<p class="meta">Only you set this. Staying at the same level is a perfectly good revision.</p>` : "") +
    (c.origin === "unclear" ? select("Who raised this revision first?", "origin", { human: "Me", ai: "The AI" }, "human") : "") +
    `<details class="why"><summary>Why the revision</summary><div class="md small">${md(pv.proposed.rationale)}</div></details>`;
  const r = await ask(`Revise ${pv.target} — ${cid}`, html, pv.stale ? "Confirm anyway" : "Confirm revision", { wide: true });
  if (!r) return false;
  const body = { force: !!pv.stale };
  if (isQ) body.maturity = r.maturity;
  if (r.origin) body.origin = r.origin;
  if ((r.statement || "").trim() !== (pv.proposed.statement || "").trim()) body.changes = { statement: r.statement };
  try {
    const x = await api("POST", `/api/candidates/${cid}/accept`, body);
    toast(x.id === pv.target ? `${pv.target} is now v${pv.revision + 1}.` : `${pv.target} superseded by ${x.id}.`);
  } catch (err) {
    if (err.status === 409) { toast(`${pv.target} changed while you were reviewing — compare again.`, "warn"); return reviewRevision(cid); }
    fail(err); return false;
  }
  after();
  return true;
}

// ================================================================ OBJECT PAGE (DESIGN §5.15.4)
const USE = { discussion: "focused on by", evidence: "evidence", decision: "decision", candidate: "candidate", review: "re-examination" };
function undoWhy(x) {
  if (x.undo_code === "not_from_candidate") return "it wasn't created by accepting a candidate";
  if (x.undo_code === "revised") return `it has been revised (now v${x.revision})`;
  const us = (x.users || []).map((u) => u.id === "project" ? "it's the project's main question"
    : `${USE[u.type] ? USE[u.type] + " " : ""}${linkIds(esc(u.id))}${["discussion", "evidence"].includes(u.type) ? "" : ` (${esc(u.field)})`}`);
  return `it's already in use — ${us.join(", ")}`;
}
const WITHDRAW_WHY = { main_question: "It's the main question — point the project at another question first.", already: "Already withdrawn or closed.", not_withdrawable: "This kind of object can't be withdrawn.", closed: "It's closed — reopen it first." };
function block(key, title, count, content, defOpen = false) {
  const open = fold.get("obj." + key, defOpen);
  return `<details class="blk" data-fold="obj.${key}" ${open ? "open" : ""}><summary><span>${title}</span><b class="cnt">${count}</b></summary><div class="blk-body">${content}</div></details>`;
}

function objFields(m) {
  const t = m.type;
  const rows = {
    question: [["Maturity", MATURITY[m.maturity] || m.maturity], ["Sub-question of", m.parent]],
    assumption: [["Supports", m.relied_on_by], ["Fragile", m.fragile], ["Derived from", m.derived_from], ["Promoted to", m.promoted_to], ["Invalidated by", m.invalidated_by]],
    hypothesis: [["Refuted if", m.falsifier], ["Tested by", m.validation], ["Confidence", m.confidence], ["From assumption", m.promoted_from]],
    uncertainty: [["Importance", m.importance]],
    insight: [["Grounded in", m.basis], ["Grounding note", m.basis_note], ["Informs", m.informs], ["Would change if", m.change_mind], ["Merges", m.consolidates], ["Superseded by", m.superseded_by]],
    evidence: [["About", m.target], ["Stance", m.stance], ["Strength", m.strength], ["Source", m.source], ["Quote", m.quote]],
    candidate: [["Kind", m.kind], ["Status", m.status], ["Target", m.target], ["Resolution", m.resolution], ["Answered by", m.answered_by], ["Merge into", m.merged_into], ["Merges", m.supersedes], ["Parent", m.parent], ["Became", m.promoted_to], ["From", m.source]],
    review: [["Target", m.target], ["Because of", m.trigger], ["Status", m.status]],
    decision: [["Kind", m.kind], ["Refs", m.refs]],
    evolution: [["Started from", m.seeds], ["Why", TRIGGER[m.trigger] || m.trigger]],
  }[t] || [];
  return dl([...rows, ["Related to", m.relates_to]]);
}

async function renderObject(id) {
  S.objId = id;
  const body = $("#obj-body");
  let x;
  try { x = await api("GET", `/api/objects/${id}/links`); } catch (err) {
    body.innerHTML = `<p><a href="#/research">← Research</a></p><div class="empty">${esc(err.status === 404 ? `${id} doesn't exist (it may have been taken back).` : err.message)}</div>`;
    return;
  }
  if (S.objId !== id) return;
  S.obj = x;
  const m = x.meta, t = m.type, p = x.provenance || {};
  const focusable = FOCUSABLE.includes(t);
  const who = p.origin ? `raised by ${ORIGIN[p.origin] || p.origin}` : "";
  const src = [who, p.discussion ? `in <a href="#/chat/${p.discussion}">${p.discussion}</a>${(p.turns || []).length ? ` turn ${p.turns.join(", ")}` : ""}` : "",
    /^C\d/.test(p.source || "") ? `via <a class="idlink" href="#/obj/${p.source}">${p.source}</a>` : "",
    m.created ? `created ${esc(m.created)}` : "", m.revised ? `revised ${esc(m.revised)}` : ""].filter(Boolean).join(" · ");
  const acts = [];
  if (focusable) acts.push(`<button class="btn small" data-act="discuss">Discuss this</button>`);
  if (t === "question" && !x.withdrawn) acts.push(`<button class="btn ghost small" data-act="maturity">Set maturity</button>`);
  const qx = x.question;
  if (qx && !x.withdrawn) {
    if (qx.status === "open" && !x.is_main_question) acts.push(`<button class="btn ghost small" data-act="active">${qx.active ? "★ Active — unmark" : "☆ Mark active"}</button>`);
    if (!x.is_main_question) acts.push(`<button class="btn ghost small" data-act="move">Move in tree…</button>`);
    if (qx.status === "open") acts.push(`<button class="btn ghost small" data-act="resolve" ${qx.can_resolve ? "" : `disabled title="${esc(x.is_main_question ? "The main question can't be closed — point the project at another question first." : qx.resolve_blocked || "")}"`}>Close…</button>`);
    else acts.push(`<button class="btn ghost small" data-act="reopen">Reopen</button>`);
  }
  if (["assumption", "hypothesis"].includes(t)) acts.push(`<a class="btn ghost small" href="#/reading/${id}">Evidence chain</a>`);
  if (["assumption", "hypothesis"].includes(t) && !x.withdrawn && m.status !== "invalidated") acts.push(`<button class="btn ghost small" data-act="ground">Ground check</button>`);
  if (t === "assumption" && !["invalidated", "retired"].includes(m.status)) acts.push(`<button class="btn ghost small danger" data-act="invalidate">Invalidate</button>`);
  if (t === "insight" && m.status === "active") {
    const on = m.starred === "true";
    acts.push(`<button class="btn ghost small" data-act="star-insight">${on ? "★ Starred — unstar" : "☆ Star"}</button>`);
    acts.push(`<button class="btn ghost small" data-act="revise-insight">Revise</button>`);
    acts.push(`<button class="btn ghost small" data-act="merge-with">Merge with…</button>`);
  }
  if ((t === "question" && !x.withdrawn && (qx || {}).status === "open") || (t === "insight" && m.status === "active"))
    acts.push(`<button class="btn ghost small" data-act="evolve" title="Think onward from it with search switched off, into a document">Evolve ✦</button>`);
  const curatable = CURATABLE.includes(t);
  if (curatable && x.withdrawn) acts.push(`<button class="btn ghost small" data-act="restore">Restore</button>`);
  else if (curatable) acts.push(`<button class="btn ghost small danger" data-act="withdraw" ${x.withdraw_code ? `disabled title="${esc(WITHDRAW_WHY[x.withdraw_code] || "")}"` : ""}>${t === "insight" ? "Abandon" : "Withdraw"}</button>`);
  if (curatable) acts.push(`<button class="btn ghost small" data-act="undo" ${x.can_undo ? "" : `disabled title="Not available: ${esc(undoWhy(x).replace(/<[^>]+>/g, ""))}"`}>Undo accept</button>`);

  const pend = x.candidates.filter((c) => c.kind === "revision" && c.status === "pending");
  const others = x.candidates.filter((c) => !(c.kind === "revision" && c.status === "pending"));
  const pendHtml = pend.map((c) => `<div class="card ${c.stale ? "flagged" : ""}" data-cand="${c.id}">
      <div class="card-top"><span class="id">${c.id}</span><span>drafted on v${esc(c.base_revision)} · from ${idl(c.source)} turn ${esc(fmtv(c.turns))} · proposed by ${ORIGIN[c.origin] || c.origin}</span>
        ${c.stale ? `<span class="tag warn">Drafted on an old version</span>` : ""}</div>
      <div class="statement small md">${md(c.statement)}</div>
      <details class="why"><summary>Why</summary><div class="md">${md(c.rationale)}</div></details>
      <div class="acts"><button class="btn small" data-act="review">Review…</button><button class="btn ghost small danger" data-act="reject-cand">Reject</button></div></div>`).join("")
    || `<div class="empty">No revisions waiting. Discuss it — when the wording gets sharper, the distiller proposes a revision here.</div>`;

  const hist = x.revisions.map((h) => `<details class="rev"><summary><b>v${esc(h.from)} → v${esc(h.to)}</b> · ${esc(h.created)} · ${idl(h.id)}${h.candidate ? ` · from ${idl(h.candidate)}` : ""}</summary><div class="md small">${md(h.body)}</div></details>`).join("")
    + `<div class="meta rev-origin">v1 · ${esc(m.created || "")}${p.source ? ` · ${idl(p.source)}` : ""}</div>`
    + (x.decisions.length ? `<h4>Other decisions</h4>` + x.decisions.map((d) => `<details class="rev"><summary>${idl(d.id)} · ${esc(d.kind)} · ${esc(d.created)}</summary><div class="md small">${md(d.body)}</div></details>`).join("") : "");

  const dsHtml = x.discussions.map((d) => `<a class="orow" href="#/chat/${d.id}"><span class="id">${d.id}</span>
      <span class="tags">${d.relation === "focus" ? `<span class="tag insight">Focused on it</span>` : d.relation === "merged" ? `<span class="tag plain" title="${esc(d.via)} was merged into ${id}">From ${esc(d.via)}</span>` : `<span class="tag plain">Where it came from</span>`}${d.status === "closed" ? `<span class="tag plain">Ended</span>` : ""}</span>
      <span class="t">${esc(d.title)}</span><span class="cnt">${d.turns} turn${d.turns === 1 ? "" : "s"}${d.last_ts ? " · " + fmtTs(d.last_ts) : ""}</span></a>`).join("");
  const dsBlock = (dsHtml ? `<div class="olist">${dsHtml}</div>` : `<div class="empty">No discussions yet.</div>`) +
    (focusable ? `<div class="acts" style="margin-top:10px"><button class="btn ghost small" data-act="discuss">New discussion about ${id}</button></div>` : "");

  const byRev = {};
  x.evidence.forEach((e) => { const r = e.via ? "via" : (e.revision || 1); (byRev[r] = byRev[r] || []).push(e); });
  const evRow = (e) => `<div class="ev ${e.stance}"><div class="head"><span class="tag ${e.stance}">${STANCE[e.stance] || e.stance}</span><a class="id idlink" href="#/obj/${e.id}">${e.id}</a>
      <span>${esc(e.strength)}${e.basis === "abstract" ? " · abstract only" : ""} · ${idl(e.source)}${e.via ? ` · ${esc(e.via)}` : ""}</span>
      ${e.revision && e.revision !== x.revision && !e.via ? `<span class="tag warn" title="Judged before the latest revision">Judged under v${e.revision}</span>` : ""}</div>
      ${e.quote ? `<blockquote>“${esc(e.quote)}”</blockquote>` : ""}<div class="md small">${md(e.note)}</div></div>`;
  const evHtmlAll = Object.keys(byRev).sort((a, b) => (a === "via") - (b === "via") || b - a).map((r) =>
    (x.revision > 1 || r === "via" ? `<h4>${r === "via" ? "Indirect" : `Under v${r}${+r === x.revision ? " (current)" : ""}`}</h4>` : "") + byRev[r].map(evRow).join("")).join("")
    || `<div class="empty">No evidence.</div>`;

  const DIR = { in: "→ it", out: "it →" };
  const relHtml = x.related.length ? `<div class="olist">${x.related.map((r) => `<a class="orow ${r.withdrawn ? "dim" : ""}" href="${idHref(r.id)}"><span class="id">${r.id}</span>
      <span class="tags"><span class="tag ${FOCUSABLE.includes(r.type) ? r.type : "plain"}">${TYPE[r.type] || esc(r.type)}</span>${r.withdrawn ? `<span class="tag plain">Withdrawn</span>` : ""}${r.via ? `<span class="tag plain" title="Linked to ${esc(r.via)}, which was merged into ${id}">From ${esc(r.via)}</span>` : ""}</span>
      <span class="t">${esc(r.text)}</span><span class="cnt">${r.dir === "in" ? `its ${esc(r.field)} ${DIR.in}` : `${DIR.out} via ${esc(r.field)}`}</span></a>`).join("")}</div>` : `<div class="empty">Nothing linked.</div>`;
  // Sub-questions of a merged question keep their parent; offer to move them here in one click (§5.16.1)
  const strays = x.related.filter((r) => r.via && r.field === "parent" && r.dir === "in");
  const treeHtmlObj = qx ? `${dl([["Parent", qx.parent], ["Sub-questions", qx.children], ["Absorbed", qx.merged_from]]) || `<div class="empty">Not placed in the tree yet.</div>`}
    ${qx.suggest_parent ? `<div class="suggest"><span class="meta">Its only related question is ${linkIds(esc(qx.suggest_parent))}.</span> <button class="btn ghost small" data-adopt="${id}" data-parent="${esc(qx.suggest_parent)}">Put under ${esc(qx.suggest_parent)}</button></div>` : ""}
    ${strays.map((r) => `<div class="suggest"><span class="meta">${linkIds(esc(r.id))} still sits under ${linkIds(esc(r.via))}, which was merged into ${id}.</span> <button class="btn ghost small" data-adopt="${esc(r.id)}" data-parent="${id}">Move it under ${id}</button></div>`).join("")}` : "";
  const rvHtml = x.reviews.length ? x.reviews.map(reviewCard).join("") : `<div class="empty">Nothing to re-examine.</div>`;
  const cHtml = others.length ? `<div class="olist">${others.map((c) => `<a class="orow ${c.status !== "pending" ? "dim" : ""}" href="#/obj/${c.id}"><span class="id">${c.id}</span>
      <span class="tags"><span class="tag ${c.kind === "revision" ? "plain" : c.kind}">${KIND[c.kind] || c.kind}</span><span class="tag plain">${esc(c.status)}</span>${c.own ? `<span class="tag plain">Created it</span>` : ""}</span>
      <span class="t">${esc(firstLine(c.statement))}</span><span class="cnt">${esc(c.field)}</span></a>`).join("")}</div>` : `<div class="empty">No other candidates mention it.</div>`;

  body.innerHTML = `<p class="back"><a href="#/research">← Research</a></p>
    <header class="obj-head">
      <div class="eyebrow">${TYPE[t] || esc(t)} · ${id}${x.revision > 1 ? ` · v${x.revision}` : ""}${x.is_main_question ? " · main question" : ""}</div>
      <div class="obj-tags">${statusTag(m)}${inBatch(id)}${x.withdrawn ? `<span class="tag plain">Withdrawn</span>` : ""}</div>
      <div class="obj-statement md">${md(x.statement || x.body)}</div>
      ${objFields(m)}
      <div class="meta obj-src">${src}</div>
      ${closedBanner(x)}
      ${x.withdrawn ? `<div class="banner warn"><strong>Withdrawn</strong> ${esc(x.withdrawn.date || "")} (${idl(x.withdrawn.decision)}, was <i>${esc(x.withdrawn.from)}</i>): ${esc(x.withdrawn.reason)}. Every link to it is kept; nothing was sent for re-examination.</div>` : ""}
      <div class="acts">${acts.join("")}</div>
      ${curatable && !x.can_undo ? `<div class="meta undo-why">Undo accept isn't available: ${undoWhy(x)}.${x.withdrawn || x.withdraw_code ? "" : " Withdraw it instead if it's no longer relevant."}</div>` : ""}
    </header>
    ${curatable ? block("pending", "Revisions waiting", pend.length, pendHtml, true) : ""}
    ${qx ? block("tree", "Place in the tree", qx.children.length + qx.merged_from.length + (qx.parent ? 1 : 0), treeHtmlObj, !!(qx.suggest_parent || strays.length)) : ""}
    ${block("history", "Revision history", x.revisions.length + x.decisions.length, hist)}
    ${block("discussions", "Discussions", x.discussions.length, dsBlock)}
    ${block("evidence", "Evidence", x.evidence.length, evHtmlAll)}
    ${block("related", "Linked objects", x.related.length, relHtml)}
    ${block("reviews", "Re-examinations", x.reviews.length, rvHtml)}
    ${block("candidates", "Candidates that mention it", others.length, cHtml)}`;
  bindFolds(body);
  const on = (act, fn) => $$(`[data-act="${act}"]`, body).forEach((b) => b.onclick = fn);
  on("discuss", () => discussThis(id));
  on("maturity", () => setMaturity(x));
  on("ground", () => groundCheck(id));
  on("invalidate", () => invalidate(id));
  on("revise-insight", () => reviseInsight(x));
  on("star-insight", () => starInsight(id, m.starred !== "true"));
  on("merge-with", () => mergeInsights([id]));
  on("withdraw", () => withdrawObj(x));
  on("restore", () => restoreObj(x));
  on("undo", () => undoAccept(x));
  on("evolve", () => evolveFrom(id));
  on("active", async () => { try { await api("POST", `/api/questions/${id}/active`, { active: !qx.active }); hint.done("active"); } catch (err) { fail(err); } after(); });
  on("move", () => moveQuestion(x));
  on("resolve", () => resolveQuestion(x));
  on("reopen", () => reopenQuestion(x));
  $$("[data-adopt]", body).forEach((b) => b.onclick = async () => {
    try { await api("POST", `/api/questions/${b.dataset.adopt}/parent`, { parent: b.dataset.parent }); toast(`${b.dataset.adopt} is now under ${b.dataset.parent}.`); } catch (err) { fail(err); }
    after();
  });
  $$("[data-cand] [data-act=review]", body).forEach((b) => b.onclick = () => reviewRevision(b.closest(".card").dataset.cand));
  $$("[data-cand] [data-act=reject-cand]", body).forEach((b) => b.onclick = async () => {
    const cid = b.closest(".card").dataset.cand;
    const r = await ask(`Reject ${cid}`, field("Why not?", "reason", "", "kept, so future distills won't propose this again", 3), "Reject");
    if (!r) return;
    try { await api("POST", `/api/candidates/${cid}/reject`, r); toast(`Rejected ${cid}.`); } catch (err) { fail(err); }
    after();
  });
  $$(".card[data-review] button", body).forEach((b) => b.onclick = () => reviewAct(b.dataset.act, x.reviews.find((r) => r.id === b.closest(".card").dataset.review)));
}

$$("#research-chips a").forEach((a) => a.onclick = () => {
  const el = document.getElementById(a.dataset.s);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
});
let spy = null;
function spyChips() {
  if (spy) spy.disconnect();
  spy = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) $$("#research-chips a").forEach((a) => a.classList.toggle("on", a.dataset.s === e.target.id));
    });
  }, { root: $("#page-research"), rootMargin: "-80px 0px -70% 0px" });
  $$(".rsec").forEach((s) => spy.observe(s));
}

// ================================================================ MODES & HANDOFF (DESIGN §5.7)
async function loadMode() {
  try { renderMode(await api("GET", "/api/mode")); } catch (err) { /* server restarting */ }
  try { S.prep = await api("GET", "/api/prep"); renderModeBanner(); } catch (err) { /* ignore */ }
}

function renderMode(m) {
  S.mode = m;
  $("#mode-pill").textContent = MODE[m.mode] || m.mode;
  $("#mode-pill").className = "pill " + (m.mode === "validation" ? "ok" : "accent");
  const b = $("#handoff-btn"), away = m.mode !== "discussion";
  b.textContent = away ? "← Back to discussion" : "Hand off →";
  b.title = away ? "Take the lead back and return to discussion mode"
    : "Hand a batch of premises and hypotheses to the AI to check against the literature";
  $("#mode-pill").className = "pill " + (m.mode === "discussion" ? "accent" : "ok");
  renderModeBanner();
}

function renderModeBanner() {
  const m = S.mode, el = $("#mode-banner");
  if (!m || !el) return;
  const parts = [];
  if (m.mode === "validation") {
    parts.push(`<div class="banner info"><strong>Validation mode.</strong> The AI is checking ${(m.batch || []).map((x) => `<a href="#/reading/${x}">${x}</a>`).join(", ")} against the literature on its own.
      It can't see who proposed them. You can still talk here. <a href="#/reading">See what it's reading →</a></div>`);
    if (m.hold) parts.push(`<div class="banner warn"><strong>Suggest going back to discussion:</strong> ${esc(m.hold.reason)}.
      Validation has stopped starting new work until you decide.
      <div class="acts"><button class="btn small" data-mode="recall">Back to discussion</button><button class="btn ghost small" data-mode="continue">Keep validating</button></div></div>`);
  } else if (m.mode === "discussion") {
    const p = (S.prep || {}).prep || {}, dec = (S.prep || {}).decision;
    if (dec && (p.active || p.ended)) {
      const tasks = (S.prep.tasks || []);
      parts.push(`<details class="banner info" ${p.active ? "" : "open"}><summary><strong>${p.active ? "Reading while you're away" : "Overnight reading"}:</strong> ${esc(p.topic || "")}</summary>
        <div class="md small" style="margin-top:8px">${md((dec.body || "").replace(/^## 做了什么\n\n.*?\n\n/s, ""))}</div>
        ${tasks.length ? `<div class="meta">${tasks.map((t) => `${t.id} ${T_KIND[t.kind] || t.kind}${t.paper ? " " + t.paper : ""} · ${T_STATUS[t.status] || t.status}${t.result_brief ? " — " + esc(t.result_brief) : ""}`).join("<br>")}</div>` : ""}
        ${p.end_reason ? `<div class="meta">Stopped: ${esc(p.end_reason)} · ${fmtTs(p.ended)} · ${dec.id}</div>` : ""}</details>`);
    }
    const req = m.prep_request;
    parts.push(`<div class="prep-row"><span class="meta">Tonight, look into:</span><input id="prep-input" placeholder="${req ? "" : "optional — if empty, the AI picks an unexamined premise and says why tomorrow"}" value="${esc(req ? req.text : "")}"><button class="btn ghost small" id="prep-save">${req ? "Update" : "Set"}</button></div>`);
  }
  el.innerHTML = parts.join("");
  $$("[data-mode]", el).forEach((b) => b.onclick = () => (b.dataset.mode === "recall" ? recallMode() : continueMode()));
  const ps = $("#prep-save", el);
  if (ps) ps.onclick = async () => {
    try { renderMode(await api("POST", "/api/prep-request", { text: $("#prep-input").value })); toast("Noted for tonight."); } catch (err) { fail(err); }
  };
}

async function handoffDialog() {
  if (!S.overview) await loadOverview();
  const o = S.overview;
  const pend = S.cands.filter((c) => c.status === "pending" && ["hypothesis", "assumption"].includes(c.kind) && c.origin !== "unclear");
  const as = o.assumptions.filter((x) => ["unexamined", "examined"].includes(x.status));
  const hs = o.hypotheses.filter((x) => ["proposed", "investigating", "inconclusive"].includes(x.status));
  const item = (id, text, tag) => `<label><input type="checkbox" name="items" value="${id}"><span><span class="id">${id}</span>${tag}<br>${esc(text.slice(0, 180))}</span></label>`;
  const body = `<p>Pick what the AI should check against the literature. It works on its own — search, read, record evidence traced to paragraphs, and change a status only when the evidence is there. It stops and suggests coming back when something major turns up.</p>
    <div class="checks">
      ${hs.length ? `<h4>Hypotheses</h4>` + hs.map((x) => item(x.id, mainText(x.body), ` <span class="tag hypothesis">${H_STATUS[x.status]}</span>`)).join("") : ""}
      ${as.length ? `<h4>Assumptions</h4>` + as.map((x) => item(x.id, mainText(x.body), ` <span class="tag assumption">${A_STATUS[x.status]}</span>`)).join("") : ""}
      ${pend.length ? `<h4>From the Inbox (accepted on hand-off)</h4>` + pend.map((c) => item(c.id, c.statement, ` <span class="tag ${c.kind}">${KIND[c.kind]}</span>`)).join("") : ""}
    </div>` + field("What do you want to find out?", "note", "", "optional — goes into the hand-off decision", 2);
  const r = await ask("Hand off to validation", body, "Hand off");
  if (!r) return;
  if (!(r.items || []).length) return toast("Pick at least one item.", "warn");
  try {
    const x = await api("POST", "/api/mode/handoff", { items: r.items, note: r.note });
    toast(`Handed off (${x.decision}): ${x.batch.join(", ")}.`);
  } catch (err) { fail(err); }
  loadMode(); loadOverview(); loadInbox();
}

async function recallMode() {
  const r = await ask("Back to discussion?", `<p>Queued validation work is cancelled; anything running finishes and commits. The switch is recorded as a decision.</p>` + field("Why now?", "reason", "", "optional", 2), "Back to discussion");
  if (!r) return;
  try { await api("POST", "/api/mode/recall", r); toast("Back in discussion mode."); } catch (err) { fail(err); }
  loadMode(); loadOverview();
}

async function continueMode() {
  const r = await ask("Keep validating?", `<p>The system suggested coming back because: <em>${esc(S.mode.hold.reason)}</em>. Continuing is recorded as a decision — say why.</p>` + field("Why continue?", "reason", "", "required", 3), "Keep validating");
  if (!r) return;
  try { await api("POST", "/api/mode/continue", r); toast("Validation continues."); } catch (err) { fail(err); }
  loadMode();
}

$("#handoff-btn").onclick = () => (S.mode && S.mode.mode !== "discussion" ? recallMode() : handoffDialog());
// ================================================================ EVOLUTION (M11 v1.8, DESIGN §5.18)
// Thinking evolved from one question or insight with search switched off, kept as a document. It changes nothing in the research state.
const TRIGGER = { manual: "you asked", auto: "while you were away" };

async function evolveFrom(seed) {
  const pick = !seed;
  let why = "";
  if (pick) {
    try { const v = await api("GET", "/api/evolutions"); seed = v.next.seed; why = v.next.why || ""; } catch (err) { return fail(err); }
    if (!seed) return toast("Nothing to evolve from yet — there are no open questions or active insights.", "warn");
  }
  const r = await ask(`Evolve ${seed}?`, `<p>A separate task thinks onward from <b>${esc(seed)}</b> with <b>search switched off</b> — no web, no papers — and writes what it found into a document. The vaguer the question, the more there is to do: what it's really asking, other ways to ask it, hidden premises, how to split it.</p>
    ${pick ? `<p class="meta">Picked by the system: ${esc(why)}. To choose yourself, press Evolve on a question's or insight's page.</p>` : ""}
    <p class="meta">It changes nothing in the research state. You read it and keep what's worth keeping. About the cost of one distill.</p>`, "Evolve");
  if (!r) return;
  try {
    const x = await api("POST", "/api/evolve", pick ? {} : { seed });     // picked: the server records why it chose
    toast(x.task ? `Evolving ${seed} (${x.task.id}) — the document lands in Evolution.` : `${seed} is already being evolved.`);
  } catch (err) { fail(err); }
  if (S.page === "evolution") renderEvolution();
}

async function renderEvolution(open) {
  const body = $("#evolution-body");
  let v;
  try { v = await api("GET", "/api/evolutions"); } catch (err) { body.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
  S.evolutions = v;
  const live = v.live.map((t) => `<div class="banner info"><span class="thinking">${t.status === "running" ? "Evolving" : "Queued"} ${linkIds(esc(t.seed))} (${esc(t.id)})</span> — ${esc(t.why || "")}</div>`).join("");
  const doc = (d, i) => `<details class="evo card" data-fold="evo.${d.id}" ${fold.get("evo." + d.id, i === 0 || d.id === open) ? "open" : ""}>
      <summary><span class="id">${d.id}</span> <strong>${esc(d.title || "")}</strong>
        <span class="meta">from ${idl(d.seeds)} · ${TRIGGER[d.trigger] || esc(d.trigger)} · ${esc(d.created || "")}</span></summary>
      <div class="md">${md(d.body.replace(/^# .*\n/, ""))}</div>
      <div class="acts"><a class="btn ghost small" href="#/obj/${d.id}">Open & discuss</a>
        <button class="btn ghost small" data-evolve="${esc((d.seeds || [])[0] || "")}">Evolve ${esc((d.seeds || [])[0] || "")} further</button></div>
    </details>`;
  body.innerHTML = live + (v.docs.map(doc).join("") ||
    `<div class="empty">No evolution documents yet. Press Evolve on a question or insight — or let the system pick; it also runs one on its own after you've been away for a while.</div>`);
  bindFolds(body);
  $$("[data-evolve]", body).forEach((b) => b.onclick = () => evolveFrom(b.dataset.evolve));
}
$("#evolve-pick").onclick = () => evolveFrom(null);

// ================================================================ READING (M8: what, why, evidence chain)
$$("#reading-seg button").forEach((b) => b.onclick = () => { S.readingView = b.dataset.v; renderReading(); });

function taskRow(t) {
  const p = t.paper_info;
  return `<div class="reading-row"><div><span class="status ${t.status}">${T_STATUS[t.status] || t.status}</span><div class="mono meta">${t.id}</div></div>
    <div><div><strong>${esc(T_KIND[t.kind] || t.kind)}</strong>${t.target ? ` · <a href="#/reading/${t.target}">${t.target}</a>` : ""}${p ? ` · <a href="#/reading/${p.id}">${p.id}</a> ${esc(p.title || "")} ${paperRef(p)}` : ""}${t.prep ? ` <span class="tag plain">overnight</span>` : ""}</div>
      ${t.why ? `<div class="why">Why: ${esc(t.why)}</div>` : ""}
      ${t.blocked_on ? `<div class="why">Waiting on <a href="#/inbox">${t.blocked_on}</a> — upload the PDF in the Inbox</div>` : ""}
      ${t.result_brief ? `<div>${esc(t.result_brief)}</div>` : ""}</div></div>`;
}

async function renderReading() {
  $$("#reading-seg button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.readingView));
  const body = $("#reading-body");
  try {
    if (S.readingView === "now") {
      const r = S.reading = await api("GET", "/api/reading");
      $("#reading-badge").textContent = r.blocked.length || "";
      const blk = (title, rows, empty) => `<h2 style="margin:22px 0 8px">${title}</h2>` +
        (rows.length ? `<div class="card" style="padding:4px 18px">${rows.map(taskRow).join("")}</div>` : `<div class="empty">${empty}</div>`);
      body.innerHTML = (S.mode && S.mode.hold ? `<div class="banner warn">Validation is holding: ${esc(S.mode.hold.reason)}. <a href="#/chat">Decide in Chat →</a></div>` : "") +
        blk("Reading now", r.running, S.mode && S.mode.mode === "validation" ? "Nothing running right now." : "Nothing running. Literature work runs in validation mode, or overnight while you're away.") +
        blk("Up next", r.queued, "Nothing queued.") +
        (r.blocked.length ? blk("Waiting on you", r.blocked, "") : "") +
        blk("Recently", r.recent, "No literature work yet.");
    } else if (S.readingView === "chain") {
      if (!S.overview) await loadOverview();
      const o = S.overview;
      const targets = [...o.hypotheses, ...o.assumptions];
      if (!S.chainTarget && targets.length) S.chainTarget = ((S.mode || {}).batch || [])[0] || targets[0].id;
      const pick = `<div class="target-pick">${targets.map((x) => `<button data-t="${x.id}" class="${x.id === S.chainTarget ? "on" : ""}">${x.id} · ${x.id.startsWith("H") ? H_STATUS[x.status] : A_STATUS[x.status]}${(x.evidence || []).length ? ` · ${x.evidence.length} ev` : ""}</button>`).join("")}</div>`;
      if (!S.chainTarget) { body.innerHTML = `<div class="empty">No hypotheses or assumptions yet.</div>`; return; }
      const c = await api("GET", `/api/chain/${S.chainTarget}`);
      body.innerHTML = pick + chainHtml(c);
      $$(".target-pick button", body).forEach((b) => b.onclick = () => go(`#/reading/${b.dataset.t}`));
    } else {
      if (!S.overview) await loadOverview();
      const ps = (S.overview.papers || []).slice().reverse();
      $("#seg-papers").textContent = ps.length || "";
      if (S.paperOpen) {
        const p = await api("GET", `/api/papers/${S.paperOpen}`);
        body.innerHTML = `<p><a href="#/reading" id="papers-back">← All papers</a></p>` + paperHtml(p);
        $("#papers-back").onclick = (e) => { e.preventDefault(); S.paperOpen = null; S.readingView = "papers"; location.hash = "#/reading"; renderReading(); };
        return;
      }
      body.innerHTML = ps.length ? `<div class="card" style="padding:4px 8px"><table class="t"><tr><th>Paper</th><th>Year</th><th>Read</th><th>Full text</th><th>For</th></tr>
        ${ps.map((p) => `<tr><td><a href="#/reading/${p.id}" class="mono">${p.id}</a> ${esc(p.title)} ${paperRef(p)}</td><td>${esc(p.year || "")}</td><td>${esc(p.read)}</td><td>${esc(p.fulltext)}</td><td>${esc(fmtv(p.for))}</td></tr>`).join("")}</table></div>`
        : `<div class="empty">No papers yet. Every paper here was verified against arXiv or Crossref when it was registered.</div>`;
    }
  } catch (err) { body.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
}

function evHtml(e) {
  const p = e.paper || {};
  return `<div class="ev ${e.stance}">
    <div class="head"><span class="tag ${e.stance}">${STANCE[e.stance] || e.stance}</span><a class="id idlink" href="#/obj/${e.id}">${e.id}</a>
      <span>${e.strength}${e.basis === "abstract" ? " · abstract only" : ""}</span>
      <span>· <a href="#/reading/${p.id}">${p.id}</a> ${esc(p.title || "")} ${p.year ? `(${esc(p.year)})` : ""} ${paperRef(p)}</span>
      <span>· ${esc(fmtv(e.locator))}</span>${e.task ? `<span>· by ${e.task}</span>` : ""}</div>
    ${e.quote ? `<blockquote>“${esc(e.quote)}”</blockquote>` : ""}
    <div class="md small">${md(e.note)}</div>
    ${Object.keys(e.paragraphs || {}).length ? `<details><summary>The paragraph${Object.keys(e.paragraphs).length > 1 ? "s" : ""} it cites</summary>${Object.entries(e.paragraphs).map(([a, t]) => `<div class="para"><b>[${esc(a)}]</b>${esc(t)}</div>`).join("")}</details>` : ""}
  </div>`;
}

function chainHtml(c) {
  const t = c.target;
  const status = t.type === "hypothesis" ? H_STATUS[t.status] : A_STATUS[t.status];
  const sup = c.evidence.filter((e) => e.stance === "support").length, con = c.evidence.filter((e) => e.stance === "contradict").length;
  return `<div class="card"><div class="card-top"><span class="tag ${t.type}">${status}</span><span class="id">${t.id}</span>
      <span>${c.evidence.length} evidence · ${sup} supporting · ${con} contradicting</span>${inBatch(t.id)}</div>
    <div class="statement md">${md(mainText(t.body))}</div>
    ${dl([["Refuted if", t.falsifier], ["Tested by", t.validation], ["Supports", t.relied_on_by]])}</div>
    ${c.changes.length ? `<h2 style="margin:24px 0 8px">Why the status is what it is</h2>${c.changes.map((x) => `<div class="card"><div class="md small">${md(x.text)}</div></div>`).join("")}` : ""}
    <h2 style="margin:24px 0 8px">Evidence</h2>
    ${c.evidence.length ? c.evidence.map(evHtml).join("") : `<div class="empty">No evidence yet.</div>`}
    ${c.groundings.length ? `<h2 style="margin:24px 0 8px">Ground checks</h2>${c.groundings.map((g) => `<div class="card"><div class="card-top"><span class="tag warn">${VERDICT[g.verdict] || g.verdict}</span><span class="id">${g.id}</span><span>${esc(fmtv(g.refs))}</span></div><div class="md small">${md(g.body)}</div></div>`).join("")}` : ""}
    ${c.reviews.length ? `<h2 style="margin:24px 0 8px">Re-examinations</h2>${c.reviews.map((r) => `<div class="card dim"><div class="card-top"><span class="id">${r.id}</span><span>${r.trigger} → ${r.target} · ${r.status}</span></div><div class="md small">${md(r.body)}</div></div>`).join("")}` : ""}
    <h2 style="margin:24px 0 8px">Work on ${t.id}</h2>
    ${c.tasks.length ? `<div class="card" style="padding:4px 18px">${c.tasks.slice().reverse().map((x) => taskRow(x)).join("")}</div>` : `<div class="empty">No literature work on it yet.</div>`}
    ${c.papers.length ? `<h2 style="margin:24px 0 8px">Papers registered for it</h2><div class="card" style="padding:10px 18px">${c.papers.map((p) => `<div><a href="#/reading/${p.id}" class="mono">${p.id}</a> ${esc(p.title)} · read: ${esc(p.read)} · full text: ${esc(p.fulltext)} ${paperRef(p)}</div>`).join("")}</div>` : ""}`;
}

function paperHtml(p) {
  const m = p.meta;
  return `<div class="card"><div class="card-top"><span class="id">${m.id}</span><span>${esc(m.year || "")} ${esc(m.venue || "")} · ${paperRef(m)} · read: ${esc(m.read)} · full text: ${esc(m.fulltext)}</span></div>
    <div class="statement"><strong>${esc(m.title)}</strong></div><div class="meta">${esc(fmtv(m.authors))}</div>
    ${m.found_via ? `<div class="meta">Found via ${esc(m.found_via)}${m.for ? ` · for ${esc(fmtv(m.for))}` : ""}</div>` : ""}
    <div class="md" style="margin-top:12px">${md(p.body)}</div></div>
    <h2 style="margin:24px 0 8px">Evidence drawn from it</h2>
    ${p.evidence.length ? p.evidence.map((e) => evHtml({ ...e, paper: m, paragraphs: {} })).join("") : `<div class="empty">None yet.</div>`}
    ${p.library && p.library.source ? `<div class="meta" style="margin-top:16px">Text source: ${esc(p.library.source)} · ${p.library.anchors} paragraphs indexed</div>` : ""}`;
}

// ================================================================ SYSTEM
$$("#sys-seg button").forEach((b) => b.onclick = () => { S.sysView = b.dataset.v; renderSystem(); });

async function renderSystem() {
  $$("#sys-seg button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.sysView));
  const body = $("#sys-body");
  const v = S.shift;
  if (S.sysView === "status" && v) {
    const sh = v.shift || {}, q = v.quota || {}, p = v.pause;
    const stateTxt = v.state === "paused" ? `Paused · ${PAUSE[p.reason] || p.reason}` : v.state === "active" ? "Running" : "Idle";
    const pct = (x) => x == null ? "–" : Math.round(x * 100) + "%";
    const bar = (x) => `<div class="bar"><i style="width:${x == null ? 0 : Math.round(x * 100)}%" class="${x >= .9 ? "hot" : ""}"></i></div>`;
    body.innerHTML = `<div class="stat-grid">
      <div class="card stat"><div class="label">Shift</div>
        <div class="big"><i class="dot ${v.state}"></i>${esc(sh.id || "—")}</div>
        <div class="sub">${stateTxt}${sh.started ? ` · since ${fmtTs(sh.started)}` : ""}</div>
        ${p && p.resume_at_iso ? `<div class="sub">Resumes around ${p.resume_at_iso}${v.auto_resume && !p.manual ? " (automatically)" : ""}</div>` : ""}
        <div class="sub">${v.running.length ? "Working on: " + v.running.map((t) => `${T_KIND[t.kind] || t.kind} ${t.id}`).join(", ") : "Nothing running"} · ${v.queued.length} queued</div>
      </div>
      <div class="card stat"><div class="label">Usage</div>
        <div class="qrow"><span>5-hour</span>${bar(q.five_hour)}<span>${pct(q.five_hour)}</span></div>
        <div class="sub">${q.five_hour_resets_iso ? "Window resets at " + q.five_hour_resets_iso : "No reading yet — appears after the first task"}</div>
        <div class="qrow"><span>Weekly</span>${bar(q.seven_day)}<span>${pct(q.seven_day)}</span></div>
        <div class="sub">Everything pauses at ${Math.round((q.weekly_stop || .95) * 100)}% to protect your other Claude Code usage.</div>
      </div>
      <div class="card stat"><div class="label">Controls</div>
        <div class="acts" style="margin-top:4px">
          <button class="btn small" id="pause-btn">${v.state === "paused" ? "Resume" : "Pause"}</button>
          <button class="btn ghost small danger" id="cutoff-btn">Simulate cutoff</button>
        </div>
        <div class="sub" style="margin-top:10px">Simulate cutoff kills running tasks the way an exhausted 5-hour window would, writes a handoff, and opens the next shift after 60 s.</div>
      </div></div>`;
    $("#pause-btn").onclick = async () => {
      try { renderShift(await api("POST", v.state === "paused" ? "/api/shift/resume" : "/api/shift/pause")); } catch (err) { fail(err); }
      renderSystem();
    };
    $("#cutoff-btn").onclick = async () => {
      const ok = await ask("Simulate a cutoff?", `<p>Running tasks are killed with SIGKILL, a handoff is written to the research state, and the next shift opens automatically in 60 seconds.</p>`, "Cut off");
      if (!ok) return;
      try { renderShift(await api("POST", "/api/shift/cutoff", { resume_in: 60 })); } catch (err) { fail(err); }
      renderSystem();
    };
  } else if (S.sysView === "tasks") {
    const tasks = await api("GET", "/api/tasks?limit=50");
    body.innerHTML = tasks.length ? `<div class="card" style="padding:4px 8px"><table class="t">
      <tr><th>Task</th><th>Kind</th><th>Status</th><th>Shift</th><th>Started</th><th>Cost</th><th>Result</th></tr>
      ${tasks.map((t) => `<tr><td class="mono">${t.id}</td><td>${T_KIND[t.kind] || t.kind}${t.discussion ? ` · ${t.discussion}` : ""}</td>
        <td><span class="status ${t.status}">${T_STATUS[t.status] || t.status}${t.attempts > 1 ? ` · attempt ${t.attempts}` : ""}</span></td>
        <td class="mono">${t.shift || "—"}</td><td>${fmtTs(t.started) || "—"}</td>
        <td>${t.cost_usd ? "$" + t.cost_usd.toFixed(3) : "—"}</td>
        <td>${t.why ? `<div class="meta">${esc(t.why)}</div>` : ""}${esc(t.result_brief || (t.status !== "done" && t.error ? t.error.slice(0, 100) : ""))}</td></tr>`).join("")}</table></div>`
      : `<div class="empty">No tasks yet.</div>`;
  } else if (S.sysView === "activity") {
    body.innerHTML = `<p class="lede" style="margin:0 0 12px">Tool calls as they happen — which files the AI reads, what it searches, what it proposes.</p>` +
      (S.activity.length ? `<div class="log">${S.activity.map((a) => `<div>${esc(a.ts.slice(11, 19))} ${a.data.task} <span class="tool">${esc((a.data.tool || "").replace("mcp__state__", "state."))}</span> ${esc(a.data.input)}</div>`).join("")}</div>`
        : `<div class="empty">Nothing yet in this browser session.</div>`);
  } else if (S.sysView === "handoffs") {
    const hs = await api("GET", "/api/handoffs");
    body.innerHTML = `<p class="lede" style="margin:0 0 16px">Written mechanically whenever a shift ends — limit reached, cutoff, pause, crash. Every briefing in the next shift includes the latest one.</p>` +
      (hs.map((h, i) => `<details class="card" ${i === 0 ? "open" : ""}><summary style="cursor:pointer"><span class="id">${h.id}</span> · ${h.shift} · ${PAUSE[h.reason] || h.reason} · <span class="meta">${fmtTs(h.ended)}</span></summary><div class="md" style="margin-top:12px">${md(h.body.replace(/^# .*\n/, ""))}</div></details>`).join("")
        || `<div class="empty">No handoffs yet.</div>`);
  } else if (S.sysView === "notes") {
    body.innerHTML = S.notes.length ? `<div class="card" style="padding:4px 18px">${S.notes.map((n) => `<div class="note-item"><i class="lv ${n.level}"></i><div><div>${esc(n.text)}</div><div class="ts">${fmtTs(n.ts)}</div></div></div>`).join("")}</div>`
      : `<div class="empty">No notifications.</div>`;
  }
}

// ---------------------------------------------------------------- shift & quota (rail)
function renderShift(v) {
  S.shift = v;
  $("#shift-dot").className = "dot " + v.state;
  const sh = v.shift || {};
  $("#shift-label").textContent = v.state === "paused" ? `Paused · ${PAUSE[(v.pause || {}).reason] || ""}`
    : v.state === "active" ? (v.running.length ? `Working · ${sh.id}` : `Ready · ${sh.id}`) : "Idle";
  renderQuota(v.quota);
  renderStream();
  return v;
}
function renderQuota(q) {
  const set = (id, v, resets) => {
    const pct = v == null ? null : Math.round(v * 100);
    $(`#${id}`).style.width = (pct ?? 0) + "%";
    $(`#${id}`).className = pct >= 90 ? "hot" : "";
    $(`#${id}t`).textContent = pct == null ? "–" : pct + "%";
    $(`#${id}t`).parentElement.title = resets ? `Resets at ${resets}` : "Read directly from Claude's rate_limit_event";
  };
  set("q5", q.five_hour, q.five_hour_resets_iso);
  set("q7", q.seven_day, q.seven_day_resets_iso);
}
async function loadShift() { renderShift(await api("GET", "/api/shift")); }

// ---------------------------------------------------------------- collapsible panels (remembered per browser)
const pref = {
  get: (k) => { try { return localStorage.getItem("ar." + k) === "1"; } catch { return false; } },
  set: (k, v) => { try { localStorage.setItem("ar." + k, v ? "1" : "0"); } catch { /* private mode */ } },
};
function applyPanels() {
  const rail = pref.get("railMin"), ds = pref.get("dsHidden");
  document.body.classList.toggle("rail-min", rail || innerWidth <= 1000);
  document.body.classList.toggle("ds-hidden", ds);
  $("#rail-toggle").title = rail ? "Expand sidebar (Ctrl+B)" : "Collapse sidebar (Ctrl+B)";
  $("#ds-toggle").title = ds ? "Show discussion list" : "Hide discussion list";
}
const toggleRail = () => { pref.set("railMin", !pref.get("railMin")); applyPanels(); };
$("#rail-toggle").onclick = toggleRail;
$("#ds-toggle").onclick = () => { pref.set("dsHidden", !pref.get("dsHidden")); applyPanels(); };
addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "b") { e.preventDefault(); toggleRail(); }
});
addEventListener("resize", () => soon("resize", applyPanels, 100));
applyPanels();

// ---------------------------------------------------------------- live events
function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data), d = ev.data || {};
    switch (ev.type) {
      case "reply_reset": if (d.discussion === S.ds) { S.streaming = ""; renderStream(); } break;
      case "reply_delta": if (d.discussion === S.ds) { S.streaming += d.text; renderStream(); } break;
      case "turn": if (d.discussion === S.ds) S.streaming = ""; soon("ds", () => { refreshDs(); loadDiscussions(); }); break;
      case "task": soon("task", () => { refreshDs(); loadDiscussions(); loadShift(); if (S.page === "system") renderSystem(); if (S.page === "reading") renderReading(); if (S.page === "evolution") renderEvolution(); loadMode(); }); break;
      case "candidates": case "state": soon("inbox", loadInbox); soon("ov", loadOverview); if (S.page === "evolution") soon("evo", renderEvolution, 400);
        if (S.page === "obj" && S.objId && !$("#dlg").open) soon("obj", () => renderObject(S.objId), 300);
        soon("dsl", loadDiscussions, 300); break;
      case "quota": renderQuota(d); break;
      case "mode": renderMode(d); soon("ov", loadOverview); break;
      case "shift": renderShift(d); if (S.page === "system") renderSystem(); break;
      case "activity":
        S.activity.unshift(ev); S.activity = S.activity.slice(0, 200);
        if (S.page === "system" && S.sysView === "activity") soon("act", renderSystem, 300);
        break;
      case "notification":
        S.notes.unshift(d);
        if (S.page !== "system") S.unread++;
        renderBadges();
        if (d.level !== "info") toast(d.text, d.level, 7000);
        if (S.page === "system" && S.sysView === "notes") renderSystem();
        break;
    }
  };
  es.onerror = () => { es.close(); setTimeout(() => { connect(); loadShift(); refreshDs(); }, 3000); };
}

// A fresh State has no project: the researcher writes its starting point in their own words.
// Nothing else works until then, so the form comes back until it succeeds.
async function setupProject() {
  let v = { title: "", description: "", question: "", maturity: "vague" };
  while (true) {
    const r = await ask("Start a research project",
      `<p>The State is empty. Everything the system does will start from what you write here.</p>` +
      field("Project title", "title", v.title) +
      field("Description", "description", v.description, "the direction and its boundaries", 5) +
      field("Main question", "question", v.question, "becomes Q001; it can stay vague", 5) +
      select("How clear is the question?", "maturity",
        { vague: "Vague — still exploring", scoped: "Scoped — boundaries are clear", formalized: "Formalized — measurable" }, v.maturity),
      "Create project");
    if (!r) { toast("A project is needed before anything else can run.", "warn"); continue; }
    v = r;
    try {
      await api("POST", "/api/project/setup", r);
      toast("Project created.");
      await Promise.all([loadOverview(), loadMode()]);
      return;
    } catch (err) { fail(err); }
  }
}

(async function init() {
  route();          // show the page frame immediately; data fills in as it arrives
  connect();
  try {
    S.notes = await api("GET", "/api/notifications");
    S.dsList = await api("GET", "/api/discussions");
    await Promise.all([loadShift(), loadInbox(), loadOverview(), loadMode()]);
    if (S.overview?.setup_needed) await setupProject();
  } catch (err) { fail(err); }
  route();
})();
