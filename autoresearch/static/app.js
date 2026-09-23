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
};

async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
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
const KIND = { assumption: "Assumption", hypothesis: "Hypothesis", question: "Question", uncertainty: "Uncertainty", insight: "Insight" };
const FIRM = { hunch: "Hunch", working: "Working", settled: "Settled" };
const ORIGIN = { human: "you", ai: "AI", unclear: "unclear" };
const H_STATUS = { proposed: "Proposed", investigating: "Investigating", supported: "Supported", refuted: "Refuted", inconclusive: "Inconclusive", abandoned: "Abandoned" };
const A_STATUS = { unexamined: "Unexamined", examined: "Examined", promoted: "Promoted", retired: "Retired", invalidated: "Invalidated" };
const T_STATUS = { queued: "Queued", running: "Running", done: "Done", failed: "Failed", interrupted: "Interrupted", cancelled: "Cancelled", blocked_on_human: "Waiting on you" };
const T_KIND = { discuss_turn: "Reply", distill: "Distill", lit_search: "Search", read_paper: "Read", assess: "Assess", contradiction_scan: "Contradiction scan", grounding: "Ground check" };
const STANCE = { support: "Supports", contradict: "Contradicts", neutral: "Neutral" };
const VERDICT = { novel: "No prior work found", prior_work: "Already done", contradicted: "Directly contradicted", mixed: "Mixed" };
const MODE = { discussion: "Discussion mode", incubation: "Incubation mode", validation: "Validation mode" };
const PAUSE = { quota_5h: "5h limit", quota_7d: "weekly limit", cutoff: "cut off", manual: "paused", crash: "crash", normal: "normal" };
const FIELDS = {
  assumption: [["relied_on_by", "Supports"], ["derived_from", "Derived from insight"]],
  hypothesis: [["falsifier", "Refuted if"], ["validation", "How it will be tested"], ["confidence", "Confidence"]],
  question: [["maturity", "Maturity"]],
  uncertainty: [["importance", "Importance"]],
  insight: [["firmness", "Firmness"], ["basis", "Grounded in"], ["basis_note", "Grounding note"], ["informs", "Informs"], ["change_mind", "Would change if"]],
};
const fmtv = (v) => Array.isArray(v) ? v.join(", ") : (v ?? "");
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
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
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
  S.page = ["chat", "inbox", "research", "reading", "system"].includes(page) ? page : "chat";
  $$(".page").forEach((p) => p.classList.toggle("on", p.id === "page-" + S.page));
  $$(".rail nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === S.page));
  if (S.page === "chat") {
    if (arg && arg !== S.ds) { S.ds = arg; S.dsData = null; S.streaming = ""; refreshDs(); }
    loadDiscussions();
  }
  if (S.page === "inbox") renderInbox();
  if (S.page === "research") loadOverview();
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
function ask(title, bodyHtml, okLabel = "Confirm") {
  return new Promise((resolve) => {
    const dlg = $("#dlg");
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
async function loadDiscussions() {
  const list = await api("GET", "/api/discussions");
  S.dsList = list;
  const ul = $("#ds-list");
  ul.innerHTML = list.slice().reverse().map((d) => `
    <li data-id="${d.id}" class="${d.id === S.ds ? "sel" : ""} ${d.status === "closed" ? "closed" : ""}">
      <span class="t">${esc(d.title)}</span>
      <span class="m">${d.turns} turn${d.turns === 1 ? "" : "s"}${d.status === "closed" ? " · ended" : ""}
        ${d.awaiting_reply ? `<span class="await">· awaiting reply</span>` : ""}</span>
    </li>`).join("") || `<li class="empty">No discussions yet.</li>`;
  $$("li[data-id]", ul).forEach((li) => li.onclick = () => go(`#/chat/${li.dataset.id}`));
  if (!S.ds && list.length) go(`#/chat/${list[list.length - 1].id}`);
  if (!list.length) renderTurns();
}

async function refreshDs() {
  if (!S.ds) return renderTurns();
  let d;
  try { d = await api("GET", `/api/discussions/${S.ds}`); } catch (e) { S.ds = null; S.dsData = null; return renderTurns(); }
  S.dsData = d;
  if (d.streaming && !S.streaming) S.streaming = d.streaming;
  $("#ds-title").textContent = d.meta.title;
  $("#ds-meta").textContent = `${d.meta.id} · ${d.turns.length} turns · ` +
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
  if (!d.turns.length) html.push(`<div class="empty-state"><h2>${esc(d.meta.title)}</h2><p>Write your first message below. The title is only a label — the AI starts replying once you send something.</p></div>`);
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
    fields: [{ k: "maturity", label: "Maturity", type: "choice", req: true }],
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
    <div class="kinds">${Object.entries(KIND).map(([k, n]) => `<button type="button" data-kind="${k}" class="tag ${k} ${k === draft.kind ? "on" : ""}">${n}</button>`).join("")}</div>
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
  const fields = (FIELDS[c.kind] || []).filter(([k]) => c[k] && fmtv(c[k]))
    .map(([k, n]) => `<dt>${n}</dt><dd>${esc(k === "firmness" ? FIRM[c[k]] || c[k] : fmtv(c[k]))}</dd>`).join("");
  const state = c.status === "accepted" ? `<span class="tag plain">Accepted → ${c.promoted_to}</span>`
    : c.status === "rejected" ? `<span class="tag plain">Rejected</span>` : "";
  return `<div class="card ${pending ? "" : "dim"} ${c.origin === "unclear" && pending ? "flagged" : ""}" data-id="${c.id}">
    <div class="card-top"><span class="tag ${c.kind}">${KIND[c.kind]}</span><span class="id">${c.id}</span>
      <span>from ${c.source} · turn ${fmtv(c.turns)} · proposed by ${ORIGIN[c.origin] || c.origin}</span>
      ${c.origin === "unclear" && pending ? `<span class="tag warn">Origin needs your call</span>` : ""}${state}</div>
    <div class="statement md">${md(c.statement)}</div>
    ${fields || c.relates_to || c.origin_note ? `<dl>${fields}
      ${c.relates_to ? `<dt>Related to</dt><dd>${esc(fmtv(c.relates_to))}</dd>` : ""}
      ${c.origin_note ? `<dt>Origin note</dt><dd>${esc(c.origin_note)}</dd>` : ""}</dl>` : ""}
    <details class="why"><summary>Why this was proposed</summary><div class="md">${md(c.rationale)}</div></details>
    ${c.decision_note ? `<details class="why" open><summary>Your note</summary><div class="md">${md(c.decision_note)}</div></details>` : ""}
    ${pending ? `<div class="acts"><button class="btn small" data-act="accept">Accept</button>
      <button class="btn ghost small" data-act="edit">Edit</button>
      <button class="btn ghost small danger" data-act="reject">Reject</button></div>` : ""}
  </div>`;
}

function reviewCard(r) {
  const open = r.status === "open";
  const ev = { hypothesis_refuted: "was refuted", assumption_invalidated: "was invalidated", insight_withdrawn: "was withdrawn" }[r.event] || r.event;
  return `<div class="card ${open ? "flagged" : "dim"}" data-review="${r.id}">
    <div class="card-top"><span class="tag warn">Re-examine</span><span class="id">${r.target}</span>
      <span>because ${r.trigger} ${ev} · distance ${r.depth}</span>
      ${open ? "" : `<span class="tag plain">${r.status === "resolved" ? "Adjusted" : "Not affected"}</span>`}</div>
    <div class="statement small md">${md(r.body)}</div>
    ${open ? `<div class="acts"><button class="btn small" data-act="resolved">I adjusted it</button>
      <button class="btn ghost small" data-act="dismissed">Not affected</button>
      <button class="btn ghost small" data-act="goto">View in Research</button></div>` : ""}
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
      "falsifier", "validation", "confidence", "maturity", "importance", "firmness", "basis", "basis_note", "informs", "change_mind"]
      .map((k) => [k, c[k] ?? (["relied_on_by", "basis", "informs"].includes(k) ? [] : "")]))
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
  if (d.kind === "insight" && !(d.basis || []).length && !String(d.basis_note || "").trim()) miss.push("Grounded in or a grounding note");
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
  if (act === "goto") return go("#/research");
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

function objCard(x, tags, extra = "", acts = "") {
  const p = x.provenance;
  const who = p ? `raised by ${ORIGIN[p.origin] || p.origin}${p.disputed ? " (disputed)" : ""}` : "";
  return `<div class="card" data-id="${x.id}">
    <div class="card-top"><span class="id">${x.id}</span>${tags}<span>${who}</span></div>
    <div class="statement small md">${md(mainText(x.body))}</div>${extra}${acts}</div>`;
}
const dl = (rows) => {
  const r = rows.filter(([, v]) => v && fmtv(v));
  return r.length ? `<dl>${r.map(([k, v]) => `<dt>${k}</dt><dd>${esc(fmtv(v))}</dd>`).join("")}</dl>` : "";
};
const section = (id, title, count, desc, content, action = "") => `
  <section class="rsec" id="${id}">
    <div class="rsec-head"><div><h2>${title}<span class="count">${count ?? ""}</span></h2>${desc ? `<p>${desc}</p>` : ""}</div>${action}</div>
    ${content}
  </section>`;

const inBatch = (id) => (S.mode && (S.mode.batch || []).includes(id)) ? `<span class="tag insight">In validation</span>` : "";

function renderResearch() {
  const o = S.overview;
  if (!o) return;
  const openReviews = (o.reviews || []).length;
  const top = (o.validation.errors.length ? `<div class="banner"><strong>The research state has ${o.validation.errors.length} validation error(s).</strong><br>${o.validation.errors.map(esc).join("<br>")}</div>` : "") +
    (openReviews ? `<div class="banner warn"><strong>${openReviews} object(s) need re-examination</strong> because something they are linked to was overturned. <a href="#/inbox" id="to-reviews">Open the Inbox →</a></div>` : "");

  const q = o.questions.map((x) => objCard(x, `<span class="tag question">${x.maturity}</span>`)).join("");

  const ord = { settled: 0, working: 1, hunch: 2 };
  const insAll = o.insights || [];
  const ins = insAll.filter((x) => x.status === "active").sort((a, b) => ord[a.firmness] - ord[b.firmness]);
  const insOld = insAll.filter((x) => x.status !== "active");
  const insHtml = (ins.length ? `<div class="grid">${ins.map((x) => objCard(x, `<span class="tag insight">${FIRM[x.firmness]}</span>`,
    dl([["Grounded in", x.basis], ["Grounding note", x.basis_note], ["Informs", x.informs], ["Would change if", x.change_mind]]),
    `<div class="acts"><button class="btn ghost small" data-act="revise">Revise</button><button class="btn ghost small danger" data-act="abandon">Abandon</button></div>`)).join("")}</div>`
    : `<div class="empty">No insights yet. An insight can be just a feel for the problem — as long as you say where it comes from.</div>`) +
    (insOld.length ? `<details class="history"><summary>How our understanding changed · ${insOld.length} earlier version(s)</summary><div class="grid">${insOld.map((x) =>
      objCard(x, `<span class="tag plain">${x.status === "superseded" ? "Superseded by " + x.superseded_by : "Abandoned"}</span>`)).join("")}</div></details>` : "");

  const as = o.assumptions.slice().sort((a, b) => (a.status !== "unexamined") - (b.status !== "unexamined"));
  const asHtml = as.length ? `<div class="grid">${as.map((x) => objCard(x,
    `<span class="tag assumption">${A_STATUS[x.status] || x.status}</span>${x.fragile === "true" || x.fragile === true ? `<span class="tag warn">Fragile</span>` : ""}${inBatch(x.id)}`,
    dl([["Supports", x.relied_on_by], ["Derived from", x.derived_from], ["Promoted to", x.promoted_to], ["Invalidated by", x.invalidated_by]]),
    `<div class="acts"><button class="btn ghost small" data-act="chain">Evidence${(x.evidence || []).length ? ` (${x.evidence.length})` : ""}</button>` +
    (x.status === "invalidated" ? "" : `<button class="btn ghost small" data-act="ground">Ground check</button><button class="btn ghost small danger" data-act="invalidate">Invalidate</button>`) + `</div>`)).join("")}</div>`
    : `<div class="empty">No assumptions recorded.</div>`;

  const hs = o.hypotheses.length ? `<div class="grid">${o.hypotheses.map((x) => objCard(x,
    `<span class="tag hypothesis">${H_STATUS[x.status] || x.status}</span><span class="tag plain">${x.confidence} confidence</span>${inBatch(x.id)}`,
    dl([["Refuted if", x.falsifier], ["Tested by", x.validation], ["From assumption", x.promoted_from]]),
    `<div class="acts"><button class="btn ghost small" data-act="chain">Evidence${(x.evidence || []).length ? ` (${x.evidence.length})` : ""}</button><button class="btn ghost small" data-act="ground">Ground check</button></div>`)).join("")}</div>`
    : `<div class="empty">No hypotheses yet.</div>`;

  const us = o.uncertainties.filter((x) => x.status !== "resolved");
  const usHtml = us.length ? `<div class="grid">${us.map((x) => objCard(x, `<span class="tag uncertainty">${x.importance} importance</span><span class="tag plain">${x.status}</span>`)).join("")}</div>`
    : `<div class="empty">No open uncertainties.</div>`;

  const de = o.dead_ends.length ? `<div class="grid">${o.dead_ends.map((x) => objCard(x, `<span class="tag plain">${x.status}</span>`, dl([["Closed by", x.closed_by]]))).join("")}</div>`
    : `<div class="empty">No closed directions yet.</div>`;

  const bias = o.bias.length ? `<div class="card"><table class="t"><tr><th>Raised by</th><th>Hypotheses</th><th>With a verdict</th><th>Refuted</th><th>Refutation rate</th></tr>
    ${o.bias.map((b) => `<tr><td>${b.origin === "disputed" ? "Disputed origin (not counted)" : ORIGIN[b.origin] || b.origin}</td><td>${b.total}</td><td>${b.resolved}</td><td>${b.refuted}</td><td>${b.refute_rate == null ? "—" : Math.round(b.refute_rate * 100) + "%"}</td></tr>`).join("")}</table></div>`
    : `<div class="empty">No hypotheses yet.</div>`;

  $("#research-body").innerHTML = top +
    section("sec-question", "Research question", o.questions.length, "", q) +
    section("sec-insights", "Understanding", ins.length, "What we've come to think so far. Understanding, not evidence — it can be a feel, but it must say where it comes from.", insHtml,
      `<button class="btn small" data-act="new-insight">Add insight</button>`) +
    section("sec-assumptions", "Assumptions", as.length, "Premises the research relies on. Unexamined ones come first — they're the dangerous ones.", asHtml) +
    section("sec-hypotheses", "Hypotheses", o.hypotheses.length, "Claims with an arranged test. Status only changes with evidence attached.", hs) +
    section("sec-uncertainties", "Uncertainties", us.length, "Unknowns that discount our conclusions.", usHtml) +
    section("sec-deadends", "Dead ends", o.dead_ends.length, "Closed directions. Every session is shown all of them before exploring anything new.", de) +
    section("sec-bias", "Bias check", "", "Origin is hidden from the AI when it judges evidence. Masking is never perfect — if your hypotheses are refuted far less often than the AI's, either you're right more often, or the masking leaks.", bias);

  const tr = $("#to-reviews");
  if (tr) tr.onclick = () => { S.inboxView = "reviews"; };
  bindResearch();
  spyChips();
}

function bindResearch() {
  const body = $("#research-body");
  const byId = (id) => [...(S.overview.insights || []), ...S.overview.assumptions].find((x) => x.id === id);
  const firm = (cur) => select("Firmness", "firmness", { hunch: "Hunch — a feel", working: "Working — an understanding we act on", settled: "Settled — solid" }, cur);
  const nb = $('[data-act="new-insight"]', body);
  if (nb) nb.onclick = async () => {
    const r = await ask("Add an insight",
      field("The insight", "statement", "", "can be descriptive — a feel is fine", 4) + firm("hunch") +
      field("Grounded in", "basis", "", "ids from the state, comma-separated, e.g. DS001, H002") +
      field("Or a grounding note", "basis_note", "", "a source outside the state, e.g. years of assembly work") +
      field("Informs", "informs", "", "optional · ids it shapes") +
      field("Would change if", "change_mind", "", "optional"), "Add");
    if (!r) return;
    try { const x = await api("POST", "/api/insights", r); toast(`Added ${x.id}.`); } catch (err) { fail(err); }
    loadOverview();
  };
  $$('[data-act="revise"]', body).forEach((b) => b.onclick = async () => {
    const x = byId(b.closest(".card").dataset.id);
    const r = await ask(`Revise ${x.id}`, `<p>Nothing is overwritten. A new insight replaces this one; the old one stays in the history and becomes part of the new one's grounding.</p>` +
      field("Revised insight", "statement", mainText(x.body), "", 4) + firm(x.firmness) +
      field("Would change if", "change_mind", x.change_mind || "") +
      field("Why revise", "note", ""), "Revise");
    if (!r) return;
    try { const n = await api("POST", `/api/insights/${x.id}/revise`, r); toast(`Revised as ${n.id}.`); } catch (err) { fail(err); }
    loadOverview();
  });
  $$('[data-act="abandon"]', body).forEach((b) => b.onclick = async () => {
    const id = b.closest(".card").dataset.id;
    const r = await ask(`Abandon ${id}`, `<p>Assumptions derived from it will be flagged for re-examination.</p>` + field("Reason", "reason", "", "", 3), "Abandon");
    if (!r) return;
    try { await api("POST", `/api/insights/${id}/abandon`, r); } catch (err) { fail(err); }
    loadOverview(); loadInbox();
  });
  $$('[data-act="chain"]', body).forEach((b) => b.onclick = () => go(`#/reading/${b.closest(".card").dataset.id}`));
  $$('[data-act="ground"]', body).forEach((b) => b.onclick = async () => {
    const id = b.closest(".card").dataset.id;
    const r = await ask(`Ground check ${id}?`, `<p>A separate task searches for prior work and the strongest counter-evidence, then annotates ${id}. It never rewrites it. It can't see who proposed ${id}.</p>`, "Queue it");
    if (!r) return;
    try { await api("POST", "/api/grounding", { target: id }); toast(`Ground check for ${id} queued.`); } catch (err) { fail(err); }
  });
  $$('[data-act="invalidate"]', body).forEach((b) => b.onclick = async () => {
    const id = b.closest(".card").dataset.id;
    const r = await ask(`Invalidate ${id}`, `<p>Everything linked to it goes to the Inbox for re-examination. Nothing is changed automatically.</p>` +
      field("Why doesn't this premise hold anymore?", "reason", "", "", 4), "Invalidate");
    if (!r) return;
    try {
      const x = await api("POST", `/api/assumptions/${id}/invalidate`, r);
      toast(x.reviews.length ? `Invalidated. ${x.reviews.length} linked object(s) need re-examination.` : "Invalidated. Nothing depended on it.", x.reviews.length ? "warn" : "info");
    } catch (err) { fail(err); }
    loadOverview(); loadInbox();
  });
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
  const b = $("#handoff-btn");
  b.textContent = m.mode === "validation" ? "← Back to discussion" : "Hand off →";
  b.title = m.mode === "validation" ? "Take the lead back: stop validating and return to discussion mode"
    : "Hand a batch of premises and hypotheses to the AI to check against the literature";
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

$("#handoff-btn").onclick = () => (S.mode && S.mode.mode === "validation" ? recallMode() : handoffDialog());

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
    <div class="head"><span class="tag ${e.stance}">${STANCE[e.stance] || e.stance}</span><span class="id">${e.id}</span>
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
      case "task": soon("task", () => { refreshDs(); loadDiscussions(); loadShift(); if (S.page === "system") renderSystem(); if (S.page === "reading") renderReading(); loadMode(); }); break;
      case "candidates": case "state": soon("inbox", loadInbox); soon("ov", loadOverview); break;
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

(async function init() {
  route();          // show the page frame immediately; data fills in as it arrives
  connect();
  try {
    S.notes = await api("GET", "/api/notifications");
    S.dsList = await api("GET", "/api/discussions");
    await Promise.all([loadShift(), loadInbox(), loadOverview(), loadMode()]);
  } catch (err) { fail(err); }
  route();
})();
