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
  inboxView: "cands", sysView: "status",
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
const T_KIND = { discuss_turn: "Reply", distill: "Distill" };
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
  S.page = ["chat", "inbox", "research", "system"].includes(page) ? page : "chat";
  $$(".page").forEach((p) => p.classList.toggle("on", p.id === "page-" + S.page));
  $$(".rail nav a").forEach((a) => a.classList.toggle("on", a.dataset.page === S.page));
  if (S.page === "chat") {
    if (arg && arg !== S.ds) { S.ds = arg; S.dsData = null; S.streaming = ""; refreshDs(); }
    loadDiscussions();
  }
  if (S.page === "inbox") renderInbox();
  if (S.page === "research") loadOverview();
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
      $$("[name]", $("#dlg-body")).forEach((el) => { out[el.name] = el.value; });
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
  const [cands, revs] = await Promise.all([api("GET", "/api/candidates"), api("GET", "/api/reviews")]);
  S.cands = cands; S.reviews = revs;
  renderBadges();
  if (S.page === "inbox") renderInbox();
}

function renderBadges() {
  const pc = S.cands.filter((c) => c.status === "pending").length;
  const pr = S.reviews.filter((r) => r.status === "open").length;
  $("#inbox-badge").textContent = pc + pr || "";
  $("#seg-cands").textContent = pc || "";
  $("#seg-reviews").textContent = pr || "";
  $("#sys-badge").textContent = S.unread || "";
  $("#seg-notes").textContent = S.unread || "";
}

$$("#inbox-seg button").forEach((b) => b.onclick = () => { S.inboxView = b.dataset.v; renderInbox(); });

function candCard(c) {
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

function renderInbox() {
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
  } else {
    const done = S.cands.filter((c) => c.status !== "pending").reverse();
    const revs = S.reviews.filter((r) => r.status !== "open").reverse();
    body.innerHTML = ((done.length ? `<h2 style="margin:0 0 12px">Decided candidates</h2>` + done.map(candCard).join("") : "") +
      (revs.length ? `<h2 style="margin:28px 0 12px">Resolved re-examinations</h2>` + revs.map(reviewCard).join("") : "")) ||
      `<div class="empty">No history yet.</div>`;
  }
  $$(".card[data-id] button", body).forEach((b) => b.onclick = () => candAct(b.dataset.act, S.cands.find((c) => c.id === b.closest(".card").dataset.id)));
  $$(".card[data-review] button", body).forEach((b) => b.onclick = () => reviewAct(b.dataset.act, S.reviews.find((r) => r.id === b.closest(".card").dataset.review)));
}

async function candAct(act, c) {
  try {
    if (act === "accept") {
      let origin = null;
      if (c.origin === "unclear") {
        const r = await ask(`Who raised ${c.id} first?`, `<p>${esc(c.origin_note)}</p>` +
          select("Origin", "origin", { human: "Me", ai: "The AI" }, "human"), "Accept");
        if (!r) return;
        origin = r.origin;
      }
      const r = await api("POST", `/api/candidates/${c.id}/accept`, { origin });
      toast(`Accepted as ${r.id}.`);
    } else if (act === "reject") {
      const r = await ask(`Reject ${c.id}`, `<p>Rejected candidates are kept with your reason, so future distills won't propose the same thing again.</p>` +
        field("Reason", "reason", "", "", 3), "Reject");
      if (!r) return;
      await api("POST", `/api/candidates/${c.id}/reject`, { reason: r.reason });
    } else if (act === "edit") {
      const r = await ask(`Edit ${c.id}`,
        `<p>Assumption vs hypothesis is decided by its current role: relied on but not scheduled for testing → assumption; a test has been arranged → hypothesis.</p>` +
        select("Kind", "kind", KIND, c.kind) +
        field("Statement", "statement", c.statement, "", 3) +
        field("Rationale", "rationale", c.rationale, "", 3) +
        field("Supports", "relied_on_by", fmtv(c.relied_on_by), "assumption, required · ids, comma-separated") +
        field("Refuted if", "falsifier", c.falsifier, "hypothesis, required") +
        field("How it will be tested", "validation", c.validation, "hypothesis, required") +
        field("Confidence", "confidence", c.confidence, "low / medium / high") +
        field("Maturity", "maturity", c.maturity, "question: vague / scoped / formalized") +
        field("Importance", "importance", c.importance, "uncertainty: low / medium / high") +
        field("Firmness", "firmness", c.firmness, "insight: hunch / working / settled") +
        field("Grounded in", "basis", fmtv(c.basis), "insight · ids") +
        field("Grounding note", "basis_note", c.basis_note, "insight · a source outside the state") +
        field("Informs", "informs", fmtv(c.informs), "insight · ids") +
        field("Would change if", "change_mind", c.change_mind, "insight, optional") +
        field("Derived from insight", "derived_from", c.derived_from, "assumption · IN###"), "Save");
      if (!r) return;
      await api("POST", `/api/candidates/${c.id}/update`, { changes: r });
      toast("Saved. It is still waiting for you to accept it.");
    }
  } catch (err) { fail(err); }
  loadInbox();
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
  $("#mode-pill").textContent = { discussion: "Discussion mode", incubation: "Incubation mode", validation: "Validation mode" }[o.project.mode] || o.project.mode;
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
    `<span class="tag assumption">${A_STATUS[x.status] || x.status}</span>`,
    dl([["Supports", x.relied_on_by], ["Derived from", x.derived_from], ["Promoted to", x.promoted_to], ["Invalidated by", x.invalidated_by]]),
    x.status === "invalidated" ? "" : `<div class="acts"><button class="btn ghost small danger" data-act="invalidate">Invalidate</button></div>`)).join("")}</div>`
    : `<div class="empty">No assumptions recorded.</div>`;

  const hs = o.hypotheses.length ? `<div class="grid">${o.hypotheses.map((x) => objCard(x,
    `<span class="tag hypothesis">${H_STATUS[x.status] || x.status}</span><span class="tag plain">${x.confidence} confidence</span>`,
    dl([["Refuted if", x.falsifier], ["Tested by", x.validation], ["Evidence", x.evidence], ["From assumption", x.promoted_from]]))).join("")}</div>`
    : `<div class="empty">No hypotheses yet.</div>`;

  const us = o.uncertainties.filter((x) => x.status !== "resolved");
  const usHtml = us.length ? `<div class="grid">${us.map((x) => objCard(x, `<span class="tag uncertainty">${x.importance} importance</span><span class="tag plain">${x.status}</span>`)).join("")}</div>`
    : `<div class="empty">No open uncertainties.</div>`;

  const de = o.dead_ends.length ? `<div class="grid">${o.dead_ends.map((x) => objCard(x, `<span class="tag plain">${x.status}</span>`, dl([["Closed by", x.closed_by]]))).join("")}</div>`
    : `<div class="empty">No closed directions yet.</div>`;

  const bias = o.bias.length ? `<div class="card"><table class="t"><tr><th>Raised by</th><th>Hypotheses</th><th>With a verdict</th><th>Refuted</th><th>Refutation rate</th></tr>
    ${o.bias.map((b) => `<tr><td>${ORIGIN[b.origin] || b.origin}</td><td>${b.total}</td><td>${b.resolved}</td><td>${b.refuted}</td><td>${b.refute_rate == null ? "—" : Math.round(b.refute_rate * 100) + "%"}</td></tr>`).join("")}</table></div>`
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
        <td>${esc(t.result_brief || (t.status !== "done" && t.error ? t.error.slice(0, 100) : ""))}</td></tr>`).join("")}</table></div>`
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
      case "task": soon("task", () => { refreshDs(); loadDiscussions(); loadShift(); if (S.page === "system") renderSystem(); }); break;
      case "candidates": case "state": soon("inbox", loadInbox); soon("ov", loadOverview); break;
      case "quota": renderQuota(d); break;
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
    await Promise.all([loadShift(), loadInbox(), loadOverview()]);
  } catch (err) { fail(err); }
  route();
})();
