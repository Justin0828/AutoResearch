"use strict";
// AutoResearch 前端：讨论面板 + 候选区 + 总览 + 交接 + 活动。无构建步骤，无依赖。

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = { ds: null, dsData: null, shift: null, streaming: "", activity: [], notes: [], unread: 0 };

async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
const toast = (msg) => alert(msg);

// ---------------------------------------------------------------- 迷你 markdown（先转义再标记）
function md(src) {
  const lines = esc(src || "").split("\n");
  const out = [];
  let list = null, para = [], code = null, table = null;
  const inline = (t) => t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
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
    if ((m = l.match(/^(#{1,4})\s+(.*)$/))) { flush(); out.push(`<h${m[1].length + 1}>${inline(m[2])}</h${m[1].length + 1}>`); continue; }
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

// ---------------------------------------------------------------- 讨论
async function loadDiscussions() {
  const list = await api("GET", "/api/discussions");
  const ul = $("#ds-list");
  ul.innerHTML = list.slice().reverse().map((d) => `
    <li data-id="${d.id}" class="${d.id === S.ds ? "sel" : ""} ${d.status === "closed" ? "closed" : ""}">
      <span class="t">${esc(d.title)}</span>
      <span class="m">${d.id} · ${d.turns} 轮${d.awaiting_reply ? " · 待回复" : ""}${d.undistilled_human ? ` · ${d.undistilled_human} 轮未蒸馏` : ""}</span>
    </li>`).join("") || `<li class="muted small">还没有讨论</li>`;
  ul.querySelectorAll("li[data-id]").forEach((li) => li.onclick = () => selectDs(li.dataset.id));
  if (!S.ds && list.length) selectDs(list[list.length - 1].id);
}

async function selectDs(id) {
  S.ds = id; S.streaming = "";
  location.hash = id;
  await refreshDs();
  loadDiscussions();
}

async function refreshDs() {
  if (!S.ds) return;
  const d = await api("GET", `/api/discussions/${S.ds}`);
  S.dsData = d;
  if (d.streaming != null && !S.streaming) S.streaming = d.streaming;
  $("#ds-title").textContent = `${d.meta.title}`;
  $("#ds-meta").textContent = `${d.meta.id} · ${d.meta.status === "open" ? "进行中" : "已结束"} · ${d.turns.length} 轮 · 摘要覆盖至第 ${d.summary.covers_through} 轮`;
  const open = d.meta.status === "open";
  $("#input").disabled = !open; $("#send").disabled = !open;
  $("#distill-btn").disabled = !open && !d.undistilled_human;
  $("#close-ds-btn").disabled = !open;
  $("#summary-box").classList.toggle("hidden", !d.summary.body);
  $("#summary-body").innerHTML = md(d.summary.body);
  renderTurns();
}

function renderTurns() {
  const d = S.dsData;
  if (!d) return;
  const html = d.turns.map((t) => `
    <div class="turn ${t.role}">
      <div class="who">第 ${t.n} 轮 · ${t.role === "human" ? "你" : "AI"} · ${esc(t.ts.replace("T", " "))}</div>
      <div class="md">${md(t.text)}</div>
    </div>`);
  const last = d.turns[d.turns.length - 1];
  const busy = d.tasks.find((t) => t.kind === "discuss_turn");
  if (last && last.role === "human") {
    if (S.streaming) {
      html.push(`<div class="turn streaming"><div class="who">AI · 正在回复</div><div class="md">${md(S.streaming)}</div></div>`);
    } else {
      const paused = S.shift && S.shift.state === "paused";
      const msg = paused ? "班次已暂停，恢复后会接着回复（交接记录已写入 State）"
        : busy ? (busy.status === "running" ? "AI 正在阅读 briefing…" : "排队中…") : "等待派发…";
      html.push(`<div class="turn pending">${msg}</div>`);
    }
  }
  const distilling = d.tasks.find((t) => t.kind === "distill");
  if (distilling) html.push(`<div class="muted small">蒸馏任务 ${distilling.id} ${distilling.status === "running" ? "进行中" : "排队中"}…</div>`);
  const box = $("#turns");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  box.innerHTML = html.join("") || `<div class="muted">还没有发言。在下方输入框写下你的第一句话并发送（Ctrl+Enter），AI 才会开始回复。</div>`;
  if (atBottom) box.scrollTop = box.scrollHeight;
}

$("#composer").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("#input").value.trim();
  if (!text || !S.ds) return;
  $("#send").disabled = true;
  try {
    await api("POST", `/api/discussions/${S.ds}/messages`, { text });
    $("#input").value = ""; S.streaming = "";
    await refreshDs();
    $("#turns").scrollTop = $("#turns").scrollHeight;
  } catch (err) { toast(err.message); }
  $("#send").disabled = false;
};
$("#input").onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#composer").requestSubmit(); };
$("#new-ds").onclick = async () => {
  const f = await ask("新讨论",
    `<label>标题（只用于列表，不会发给 AI）</label><input name="title" placeholder="比如：精细操作的瓶颈在哪" autofocus>
     <label>你的第一句话（发给 AI，可稍后再说）</label><textarea name="text" rows="4" placeholder="比如：我觉得现在的 VLA 在精细操作上差得远，你认为瓶颈在哪？"></textarea>`);
  if (!f) return;
  try {
    const r = await api("POST", "/api/discussions", { title: f.title || (f.text || "").slice(0, 30) });
    if (f.text && f.text.trim()) await api("POST", `/api/discussions/${r.id}/messages`, { text: f.text.trim() });
    S.ds = null; await selectDs(r.id);
    $("#input").focus();
  } catch (err) { toast(err.message); }
};
$("#distill-btn").onclick = async () => {
  try {
    const r = await api("POST", `/api/discussions/${S.ds}/distill`);
    if (r.note) toast(r.note);
    refreshDs();
  } catch (err) { toast(err.message); }
};
$("#close-ds-btn").onclick = async () => {
  if (!confirm("结束这个讨论？结束后会自动蒸馏未覆盖的部分。")) return;
  await api("POST", `/api/discussions/${S.ds}/status`, { status: "closed" });
  refreshDs(); loadDiscussions();
};

// ---------------------------------------------------------------- 候选区
const KIND_CN = { assumption: "前提", hypothesis: "假设", question: "问题", uncertainty: "不确定性" };
const FIELDS = {
  assumption: [["relied_on_by", "支撑着"]],
  hypothesis: [["falsifier", "证伪条件"], ["validation", "验证安排"], ["confidence", "置信度"]],
  question: [["maturity", "成熟度"]],
  uncertainty: [["importance", "重要性"]],
};
const fmtv = (v) => Array.isArray(v) ? v.join(", ") : (v ?? "");

async function loadCandidates() {
  const all = await api("GET", "/api/candidates");
  const pending = all.filter((c) => c.status === "pending");
  const decided = all.filter((c) => c.status !== "pending").reverse().slice(0, 20);
  $("#cand-count").textContent = pending.length || "";
  const card = (c) => `
    <div class="card ${c.status !== "pending" ? "decided" : ""}" data-id="${c.id}">
      <h4><span class="kind ${c.kind}">${KIND_CN[c.kind]}</span> ${c.id}
        <span class="muted small">来自 ${c.source} 第 ${fmtv(c.turns)} 轮 · 提出者 ${c.origin}</span>
        ${c.origin === "unclear" ? `<span class="flag">归属待你裁定</span>` : ""}
        ${c.status !== "pending" ? `<span class="muted small">· ${c.status === "accepted" ? "已确认 → " + c.promoted_to : c.status === "rejected" ? "已丢弃" : c.status}</span>` : ""}
      </h4>
      <div class="st md">${md(c.statement)}</div>
      <dl>
        ${(FIELDS[c.kind] || []).filter(([k]) => c[k]).map(([k, n]) => `<dt>${n}</dt><dd>${esc(fmtv(c[k]))}</dd>`).join("")}
        ${c.relates_to ? `<dt>相关</dt><dd>${esc(fmtv(c.relates_to))}</dd>` : ""}
        ${c.origin_note ? `<dt>归属说明</dt><dd>${esc(c.origin_note)}</dd>` : ""}
        <dt>理由</dt><dd class="md">${md(c.rationale)}</dd>
        ${c.decision_note ? `<dt>处理意见</dt><dd>${esc(c.decision_note)}</dd>` : ""}
      </dl>
      ${c.status === "pending" ? `<div class="acts">
        <button data-act="accept">确认入库</button>
        <button data-act="edit" class="ghost">修改</button>
        <button data-act="reject" class="ghost danger">丢弃</button>
      </div>` : ""}
    </div>`;
  $("#tab-cands").innerHTML =
    `<p class="muted small">讨论中蒸馏出的候选。确认后才成为正式 State 对象；丢弃要写理由，它会成为后续蒸馏的去重依据。</p>` +
    (pending.map(card).join("") || `<p class="muted">没有待确认的候选。</p>`) +
    (decided.length ? `<div class="sect">最近处理过的</div>` + decided.map(card).join("") : "");
  $("#tab-cands").querySelectorAll(".card[data-id]").forEach((el) => {
    const c = all.find((x) => x.id === el.dataset.id);
    el.querySelectorAll("button[data-act]").forEach((b) => b.onclick = () => candAct(b.dataset.act, c));
  });
}

async function candAct(act, c) {
  try {
    if (act === "accept") {
      let origin = null;
      if (c.origin === "unclear") {
        const r = await ask(`裁定 ${c.id} 的归属`,
          `<p class="small">${esc(c.origin_note)}</p>
           <label>谁先提出的？</label>
           <select name="origin"><option value="human">我（human）</option><option value="ai">AI</option></select>`);
        if (!r) return;
        origin = r.origin;
      }
      const r = await api("POST", `/api/candidates/${c.id}/accept`, { origin });
      toast(`已入库为 ${r.id}`);
    } else if (act === "reject") {
      const r = await ask(`丢弃 ${c.id}`, `<label>理由（后续蒸馏会据此去重）</label><textarea name="reason" rows="3" required></textarea>`);
      if (!r) return;
      await api("POST", `/api/candidates/${c.id}/reject`, { reason: r.reason });
    } else if (act === "edit") {
      const kinds = Object.entries(KIND_CN).map(([k, n]) => `<option value="${k}" ${k === c.kind ? "selected" : ""}>${n}</option>`).join("");
      const allFields = [["relied_on_by", "支撑着（前提必填，id 逗号分隔）"], ["falsifier", "证伪条件（假设必填）"],
        ["validation", "验证安排（假设必填）"], ["confidence", "置信度 low/medium/high"],
        ["maturity", "成熟度 vague/scoped/formalized"], ["importance", "重要性 low/medium/high"]];
      const r = await ask(`修改 ${c.id}`,
        `<label>类别（按当前角色：被依赖而未安排验证 = 前提；已安排验证 = 假设）</label><select name="kind">${kinds}</select>
         <label>陈述</label><textarea name="statement" rows="3">${esc(c.statement)}</textarea>
         <label>理由</label><textarea name="rationale" rows="3">${esc(c.rationale)}</textarea>
         ${allFields.map(([k, n]) => `<label>${n}</label><input name="${k}" value="${esc(fmtv(c[k]))}">`).join("")}`);
      if (!r) return;
      await api("POST", `/api/candidates/${c.id}/update`, { changes: r });
    }
  } catch (err) { toast(err.message); }
  loadCandidates(); loadOverview();
}

// ---------------------------------------------------------------- 总览
async function loadOverview() {
  const o = await api("GET", "/api/overview");
  $("#project-title").textContent = o.project.title || "";
  $("#mode-badge").textContent = { discussion: "讨论模式", incubation: "自演进模式", validation: "验证模式" }[o.project.mode] || o.project.mode;
  const who = (x) => x.provenance ? ` · ${x.provenance.origin}${x.provenance.disputed ? "（有争议）" : ""}` : "";
  const first = (b) => (b || "").split("\n").find((l) => l.trim() && !l.startsWith("#")) || "";
  const item = (x, extra, acts) => `<div class="card" data-id="${x.id}"><h4>${x.id} ${extra || ""}<span class="muted small">${who(x)}</span></h4><div class="md">${md(first(x.body))}</div>${acts || ""}</div>`;
  const q = o.questions.map((x) => item(x, `<span class="kind question">成熟度 ${x.maturity}</span>`)).join("");
  const as = o.assumptions.slice().sort((a, b) => (a.status !== "unexamined") - (b.status !== "unexamined"))
    .map((x) => item(x, `<span class="kind assumption">${x.status}</span><span class="muted small">支撑 ${fmtv(x.relied_on_by)}</span>`,
      x.status === "invalidated" ? "" : `<div class="acts"><button class="ghost small danger" data-act="invalidate">推翻这条前提</button></div>`)).join("");
  const rv = (o.reviews || []).map((r) => `<div class="card" data-review="${r.id}">
      <h4><span class="flag">待重新审视</span> ${r.target} <span class="muted small">${r.id} · 因 ${r.trigger} 被推翻 · 距离 ${r.depth}</span></h4>
      <div class="md">${md(r.body)}</div>
      <div class="acts"><button class="small" data-act="resolved">已据此调整</button><button class="ghost small" data-act="dismissed">判断不受影响</button></div>
    </div>`).join("");
  const hs = o.hypotheses.map((x) => item(x, `<span class="kind hypothesis">${x.status} · ${x.confidence}</span><span class="muted small">证据 ${fmtv(x.evidence) || "无"}</span>`)).join("");
  const us = o.uncertainties.filter((x) => x.status !== "resolved").map((x) => item(x, `<span class="kind uncertainty">${x.importance}</span>`)).join("");
  const de = o.dead_ends.map((x) => item(x, `<span class="kind">${x.status}</span>`)).join("");
  const bias = o.bias.length ? `<table class="bias"><tr><th>提出者</th><th>总数</th><th>已有结论</th><th>被反驳</th><th>反驳率</th></tr>
    ${o.bias.map((b) => `<tr><td>${b.origin}</td><td>${b.total}</td><td>${b.resolved}</td><td>${b.refuted}</td><td>${b.refute_rate == null ? "—" : Math.round(b.refute_rate * 100) + "%"}</td></tr>`).join("")}</table>
    <p class="muted small">origin 在评判时对 agent 屏蔽；屏蔽不可能完美，这张表是检测“人提的假设系统性活得更久”的唯一仪器。</p>` : "";
  const v = o.validation;
  $("#tab-overview").innerHTML = `
    ${v.errors.length ? `<div class="card"><h4 class="flag">State 校验：${v.errors.length} 个错误</h4><div class="small">${v.errors.map(esc).join("<br>")}</div></div>` : ""}
    ${rv ? `<div class="sect">待重新审视（${o.reviews.length}）</div><p class="muted small">这些对象与已被推翻的前提或假设相关。系统不会自动改写它们，由你判断。</p>${rv}` : ""}
    <div class="sect">研究问题</div>${q}
    <div class="sect">前提（unexamined 在前）</div>${as || `<p class="muted">暂无</p>`}
    <div class="sect">假设</div>${hs || `<p class="muted">暂无</p>`}
    <div class="sect">未决不确定性</div>${us || `<p class="muted">暂无</p>`}
    <div class="sect">已关闭方向</div>${de || `<p class="muted">暂无</p>`}
    <div class="sect">偏差指标（按 origin 的反驳率）</div>${bias}`;
  const tab = $("#tab-overview");
  tab.querySelectorAll('[data-act="invalidate"]').forEach((b) => b.onclick = async () => {
    const id = b.closest(".card").dataset.id;
    const r = await ask(`推翻 ${id}`, `<p class="small">依赖它的所有对象会列入“待重新审视”，由你逐条判断；它们本身不会被改动。</p>
      <label>为什么这条前提不再成立</label><textarea name="reason" rows="4" required></textarea>`);
    if (!r) return;
    try {
      const res = await api("POST", `/api/assumptions/${id}/invalidate`, { reason: r.reason });
      toast(res.reviews.length ? `已推翻。${res.reviews.length} 个相关对象待你重新审视。` : "已推翻。没有依赖它的对象。");
    } catch (err) { toast(err.message); }
    loadOverview();
  });
  tab.querySelectorAll("[data-review] button").forEach((b) => b.onclick = async () => {
    const rid = b.closest(".card").dataset.review, st = b.dataset.act;
    const r = await ask(`${rid}：${st === "resolved" ? "已据此调整" : "判断不受影响"}`,
      `<label>${st === "resolved" ? "你做了什么调整" : "为什么它不受影响"}（会留作记录）</label><textarea name="note" rows="3" required></textarea>`);
    if (!r) return;
    try { await api("POST", `/api/reviews/${rid}/resolve`, { status: st, note: r.note }); } catch (err) { toast(err.message); }
    loadOverview();
  });
}

async function loadHandoffs() {
  const hs = await api("GET", "/api/handoffs");
  $("#tab-handoffs").innerHTML = `<p class="muted small">每次班次结束（额度耗尽、切断、暂停、崩溃）都会机械生成一份，下一班所有 briefing 都带着最新的那份。</p>` +
    (hs.map((h) => `<details class="card" ${h === hs[0] ? "open" : ""}><summary><strong>${h.id}</strong> · ${h.shift} · ${h.reason} · <span class="muted small">${esc(h.ended)}</span></summary><div class="md">${md(h.body)}</div></details>`).join("")
      || `<p class="muted">还没有交接记录。</p>`);
}

async function loadActivity() {
  const tasks = await api("GET", "/api/tasks?limit=25");
  const st = { queued: "排队", running: "进行中", done: "完成", failed: "失败", interrupted: "被截断", cancelled: "取消" };
  $("#tab-activity").innerHTML = `
    <div class="sect">任务</div>
    ${tasks.map((t) => `<div class="act">${t.id} ${t.kind} · ${st[t.status] || t.status}${t.attempts > 1 ? ` · 第${t.attempts}次` : ""}${t.cost_usd ? ` · $${t.cost_usd.toFixed(3)}` : ""}${t.result_brief ? " · " + esc(t.result_brief) : ""}${t.error && t.status !== "done" ? " · " + esc(t.error.slice(0, 80)) : ""}</div>`).join("") || `<p class="muted">暂无</p>`}
    <div class="sect">工具调用（实时）</div>
    ${S.activity.map((a) => `<div class="act">${esc(a.ts.slice(11))} ${a.data.task} ${esc(a.data.tool)} ${esc(a.data.input)}</div>`).join("") || `<p class="muted small">agent 读文件、查文献、提交候选时会显示在这里。</p>`}`;
}

// ---------------------------------------------------------------- 班次与额度
function renderShift(v) {
  S.shift = v;
  const dot = $("#shift-state");
  dot.className = "dot " + v.state;
  const sh = v.shift || {};
  let label = sh.id ? `${sh.id}` : "—";
  if (v.state === "paused") {
    const p = v.pause || {};
    label += ` · 已暂停（${{ quota_5h: "5h 额度", quota_7d: "周额度", cutoff: "切断", manual: "人工" }[p.reason] || p.reason}）`;
    if (p.resume_at_iso) label += ` · ${p.resume_at_iso} 恢复`;
  } else if (v.running.length) label += ` · ${v.running.map((t) => t.kind).join(", ")}`;
  $("#shift-label").textContent = label;
  $("#pause-btn").textContent = v.state === "paused" ? "恢复" : "暂停";
  renderQuota(v.quota);
  renderTurns();
}
function renderQuota(q) {
  const set = (id, v, resets) => {
    const pct = v == null ? null : Math.round(v * 100);
    $(`#${id}`).style.width = (pct ?? 0) + "%";
    $(`#${id}`).className = pct >= 90 ? "hot" : "";
    $(`#${id}t`).textContent = pct == null ? "?" : pct + "%";
    $(`#${id}t`).title = resets ? `重置于 ${resets}` : "";
  };
  set("q5", q.five_hour, q.five_hour_resets_iso);
  set("q7", q.seven_day, q.seven_day_resets_iso);
}
async function loadShift() { renderShift(await api("GET", "/api/shift")); }
$("#pause-btn").onclick = async () => {
  try { renderShift(await api("POST", S.shift && S.shift.state === "paused" ? "/api/shift/resume" : "/api/shift/pause")); }
  catch (err) { toast(err.message); }
};
$("#cutoff-btn").onclick = async () => {
  if (!confirm("模拟 5h 窗口切断：SIGKILL 所有在飞任务、写交接记录，60 秒后自动开下一班。继续？")) return;
  renderShift(await api("POST", "/api/shift/cutoff", { resume_in: 60 }));
};

// ---------------------------------------------------------------- 通知
function renderNotes() {
  $("#notes-count").textContent = S.unread || "";
  $("#notes-panel").innerHTML = S.notes.map((n) => `<div class="note"><span class="lv ${n.level}">${n.level}</span>${esc(n.ts.replace("T", " "))}<br>${esc(n.text)}</div>`).join("") || `<p class="muted">没有通知</p>`;
}
$("#notes-btn").onclick = () => { $("#notes-panel").classList.toggle("hidden"); S.unread = 0; renderNotes(); };

// ---------------------------------------------------------------- tabs / dialog
document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + b.dataset.tab));
  ({ cands: loadCandidates, overview: loadOverview, handoffs: loadHandoffs, activity: loadActivity })[b.dataset.tab]();
});

function ask(title, bodyHtml) {
  return new Promise((resolve) => {
    const dlg = $("#dlg");
    $("#dlg-title").textContent = title;
    $("#dlg-body").innerHTML = bodyHtml;
    dlg.onclose = () => {
      if (dlg.returnValue !== "ok") return resolve(null);
      const out = {};
      $("#dlg-body").querySelectorAll("[name]").forEach((el) => { out[el.name] = el.value; });
      resolve(out);
    };
    dlg.showModal();
  });
}

// ---------------------------------------------------------------- 事件流
let refreshTimer = null;
const soon = (fn) => { clearTimeout(refreshTimer); refreshTimer = setTimeout(fn, 150); };
function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data), d = ev.data || {};
    switch (ev.type) {
      case "reply_reset": if (d.discussion === S.ds) { S.streaming = ""; renderTurns(); } break;
      case "reply_delta": if (d.discussion === S.ds) { S.streaming += d.text; renderTurns(); } break;
      case "turn": if (d.discussion === S.ds) S.streaming = ""; soon(() => { refreshDs(); loadDiscussions(); }); break;
      case "task": soon(() => { refreshDs(); loadDiscussions(); loadShift(); if (!$("#tab-activity").classList.contains("hidden")) loadActivity(); }); break;
      case "candidates": loadCandidates(); loadOverview(); break;
      case "quota": renderQuota(d); break;
      case "shift": renderShift(d); loadHandoffs(); break;
      case "state": loadOverview(); break;
      case "activity":
        S.activity.unshift(ev); S.activity = S.activity.slice(0, 60);
        if (!$("#tab-activity").classList.contains("hidden")) loadActivity();
        break;
      case "notification": S.notes.unshift(d); S.unread++; renderNotes(); break;
    }
  };
  es.onerror = () => { es.close(); setTimeout(() => { connect(); loadShift(); refreshDs(); }, 3000); };
}

(async function init() {
  S.notes = await api("GET", "/api/notifications");
  renderNotes();
  await loadShift();
  const h = location.hash.slice(1);
  if (/^DS\d+$/.test(h)) S.ds = h;
  await Promise.all([loadDiscussions(), loadCandidates(), loadOverview(), loadHandoffs()]);
  if (S.ds) refreshDs();
  connect();
})();
