// Project Pulse local demo. Plain JS, no build step. Bot text is shown exactly as the bot sent it.
"use strict";

const S = { token: null, ac: null, members: [], areaNames: {}, busy: false };
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
};
const IST = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const ist = (iso) => (iso ? IST.format(new Date(iso)) : "");
const money = (v) => (v == null ? "" : "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }));

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}
const field = (path) => api(`/acs/${S.ac}/field${path}`, { headers: { Authorization: `Bearer ${S.token}` } });
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });

// ---- clock -----------------------------------------------------------------------------------

function showClock(now, offsetMin) {
  $("#now").textContent = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", weekday: "short", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(now));
  if (offsetMin != null) {
    const d = Math.floor(offsetMin / 1440), h = Math.floor((offsetMin % 1440) / 60), m = offsetMin % 60;
    $("#offset").textContent = offsetMin ? `(+${d ? d + "d " : ""}${h ? h + "h " : ""}${m ? m + "m" : ""} ahead of real time)` : "(real time)";
  }
}

async function skip(minutes, btn) {
  document.querySelectorAll(".skip button").forEach((b) => (b.disabled = true));
  $("#skipnote").textContent = "running the worker…";
  try {
    const r = await post("/demo/api/skip", { minutes });
    $("#skipnote").textContent = r.snapped_to_followup_window ? "moved on to 10:05 IST, when follow-ups are asked" : "";
    const st = await api("/demo/api/state");
    showClock(st.now, st.offset_minutes);
  } catch (e) { $("#skipnote").textContent = "skip failed: " + e.message; }
  document.querySelectorAll(".skip button").forEach((b) => (b.disabled = false));
  phones.forEach((p) => p.poll(true));
  refreshPanels();
}

// ---- phones ----------------------------------------------------------------------------------

class Phone {
  constructor(root) {
    this.root = root;
    root.append($("#phone-tpl").content.cloneNode(true));
    this.screen = $(".screen", root);
    this.who = $(".who", root);
    this.text = $(".text", root);
    this.toast = $(".toast", root);
    this.rev = -1;
    this.seenToast = null;
    for (const m of S.members) this.who.append(el("option", { value: m.key }, `${m.name} (${m.key})`));
    this.who.value = localStorage.getItem("pulse-demo-" + root.dataset.default) || root.dataset.default;
    this.who.addEventListener("change", () => {
      localStorage.setItem("pulse-demo-" + root.dataset.default, this.who.value);
      this.rev = -1; this.seenToast = null; this.poll(true);
    });
    $(".compose", root).addEventListener("submit", (ev) => { ev.preventDefault(); this.sendText(); });
    const file = $("input[type=file]", root);
    file.addEventListener("change", () => { if (file.files[0]) this.sendFile(file.files[0]); file.value = ""; });
  }

  get member() { return this.who.value; }

  async act(fn) {
    this.root.classList.add("busy");
    try { this.render(await fn()); } catch (e) { alert("Demo request failed: " + e.message); }
    this.root.classList.remove("busy");
    phones.forEach((p) => p !== this && p.poll(true));
    refreshPanels();
  }

  sendText() {
    const text = this.text.value.trim();
    if (!text) return;
    this.text.value = "";
    this.act(() => post("/demo/api/message", { member: this.member, text }));
  }

  async sendFile(f) {
    const caption = this.text.value.trim() || null;
    this.text.value = "";
    if (f.type.startsWith("image/") && f.type !== "image/jpeg") f = (await asJpeg(f)) || f;   // as Telegram apps send photos
    const buf = new Uint8Array(await f.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    let duration = null;
    if (f.type.startsWith("audio/")) duration = await audioSeconds(f);
    this.act(() => post("/demo/api/upload", { member: this.member, name: f.name, mime: f.type || "application/octet-stream", data_b64: btoa(bin), caption, duration }));
  }

  press(messageId, data) {
    this.act(() => post("/demo/api/callback", { member: this.member, message_id: messageId, data }));
  }

  async poll(force = false) {
    try {
      const chat = await api(`/demo/api/chat/${encodeURIComponent(this.member)}`);
      if (force || chat.rev !== this.rev) this.render(chat);
      return chat.rev;
    } catch (e) { return null; }
  }

  render(chat) {
    const changed = chat.rev !== this.rev;
    this.rev = chat.rev;
    if (chat.now) showClock(chat.now);
    const atBottom = this.screen.scrollHeight - this.screen.scrollTop - this.screen.clientHeight < 40;
    this.screen.replaceChildren(...chat.messages.map((m) => this.bubble(m)));
    if (atBottom || changed) this.screen.scrollTop = this.screen.scrollHeight;
    const maxSeq = Math.max(0, ...chat.toasts.map((t) => t.seq));
    if (this.seenToast == null) this.seenToast = maxSeq;          // first look at this chat: old toasts stay hidden
    else if (maxSeq > this.seenToast) {
      this.seenToast = maxSeq;
      this.toast.textContent = chat.toasts[chat.toasts.length - 1].text; this.toast.hidden = false;
      clearTimeout(this.toastTimer); this.toastTimer = setTimeout(() => (this.toast.hidden = true), 3500);
    }
  }

  bubble(m) {
    if (m.deleted) return el("div", { class: `msg ${m.from} deleted` }, "message deleted (chat cleanup)");
    const b = el("div", { class: `msg ${m.from}` });
    if (m.photo) b.append(el("img", { src: `/demo/api/file/${encodeURIComponent(m.photo)}`, alt: "photo" }));
    if (m.audio) b.append(el("audio", { controls: "", preload: "none", src: `/demo/api/file/${encodeURIComponent(m.audio)}` }));
    if (m.document) b.append(el("div", { class: "muted" }, "📄 " + m.document));
    if (m.text) b.append(m.text);
    const rows = (m.reply_markup && m.reply_markup.inline_keyboard) || [];
    if (rows.length) {
      b.append(el("div", { class: "kb" }, rows.map((row) => el("div", { class: "row" }, row.map((btn) =>
        btn.callback_data != null
          ? el("button", { type: "button", onclick: () => this.press(m.message_id, btn.callback_data) }, btn.text)
          : el("button", { type: "button", class: "url", title: btn.url || "" }, btn.text))))));
    }
    if (m.edits) b.append(el("div", { class: "meta" }, `edited ×${m.edits}`));
    return b;
  }
}

// Telegram apps recompress photos to JPEG (longest side 1280); doing the same here makes any image a "photo".
async function asJpeg(f) {
  try {
    const img = await createImageBitmap(f);
    const k = Math.min(1, 1280 / Math.max(img.width, img.height));
    const c = Object.assign(document.createElement("canvas"), { width: Math.round(img.width * k), height: Math.round(img.height * k) });
    c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
    const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.85));
    return blob ? new File([blob], f.name.replace(/\.[^.]*$/, "") + ".jpg", { type: "image/jpeg" }) : null;
  } catch (e) { return null; }
}

function audioSeconds(f) {
  return new Promise((resolve) => {
    const a = new Audio(URL.createObjectURL(f));
    a.addEventListener("loadedmetadata", () => resolve(Number.isFinite(a.duration) ? Math.max(1, Math.round(a.duration)) : null));
    a.addEventListener("error", () => resolve(null));
    setTimeout(() => resolve(null), 3000);
  });
}

// ---- panels ----------------------------------------------------------------------------------

const table = (cols, rows) => el("table", {},
  el("thead", {}, el("tr", {}, cols.map((c) => el("th", { class: c.num ? "num" : null }, c.h)))),
  el("tbody", {}, rows.map((r) => el("tr", {}, cols.map((c) => el("td", { class: [c.num ? "num" : "", c.nowrap ? "nowrap" : ""].join(" ").trim() || null }, c.f(r)))))));
const chips = (list, cls = "warn") => (list || []).map((f) => el("span", { class: `chip ${cls}` }, f));
const area = (id) => (id ? S.areaNames[id] || id : "");
const STATUS_CLS = { posted: "ok", pending_ack: "warn", disputed: "bad", void: "bad", new: "warn", acknowledged: "", assigned: "", in_progress: "", resolved: "ok", closed_no_action: "ok", duplicate: "" };

async function ledgerPanel() {
  const r = await field("/txns?limit=100");
  return [el("h3", {}, `Latest entries (${r.items.length} of ${r.total})`), table([
    { h: "No.", nowrap: true, f: (t) => t.txn_no },
    { h: "Date", nowrap: true, f: (t) => t.txn_date },
    { h: "Kind", f: (t) => t.kind },
    { h: "Amount", num: true, f: (t) => money(t.amount) },
    { h: "Bucket", f: (t) => t.bucket_name_hi ? `${t.top_bucket_name_hi}${t.bucket_code && t.bucket_code.includes(".") ? " › " + t.bucket_name_hi : ""}` : "" },
    { h: "Area", f: (t) => area(t.area_id) },
    { h: "Member", f: (t) => t.kind === "expense" ? t.from_name : t.kind === "inflow" ? `→ ${t.to_name}` : `${t.from_name} → ${t.to_name}` },
    { h: "Status", f: (t) => el("span", { class: `chip ${STATUS_CLS[t.status] || ""}` }, t.status) },
    { h: "Flags", f: (t) => chips(t.flagged) },
    { h: "Remark", f: (t) => t.remark || "" },
  ], r.items)];
}

async function balancesPanel() {
  const r = await field("/balances");
  return [el("h3", {}, "Team tree (the principal is the source of funds, so has no balance)"), table([
    { h: "Member", f: (m) => el("span", { class: "indent", style: `padding-left:${(m.depth || 0) * 18}px` }, (m.depth ? "└ " : "") + m.display_name) },
    { h: "Received", num: true, f: (m) => money(m.received) },
    { h: "Spent", num: true, f: (m) => money(m.spent) },
    { h: "Passed on", num: true, f: (m) => money(m.passed_on) },
    { h: "Returned", num: true, f: (m) => money(m.returned) },
    { h: "Balance", num: true, f: (m) => m.balance == null ? "—" : el("b", {}, money(m.balance)) },
    { h: "In transit", num: true, f: (m) => money(m.in_transit) },
    { h: "Disputed", num: true, f: (m) => money(m.disputed) },
    { h: "Overdue acks", num: true, f: (m) => m.overdue_acks || "" },
    { h: "Last entry", f: (m) => m.last_entry_date || "" },
  ], r.members)];
}

async function spendPanel() {
  const [byBucket, byArea, nvs] = await Promise.all([field("/spend/summary?group_by=bucket"), field("/spend/summary?group_by=area"), field("/spend/need-vs-spend")]);
  const t = byBucket.totals;
  return [
    el("div", { class: "totals" },
      [["Spend", money(t.spend_total)], ["Entries", t.entries], ["In transit", money(t.in_transit)], ["Disputed", money(t.disputed)], ["Small entries (count)", t.tail_count_pct + "%"], ["Top 10% (value)", t.top_decile_value_pct + "%"]]
        .map(([k, v]) => el("div", {}, el("span", { class: "muted" }, k), el("b", {}, v)))),
    el("h3", {}, "By bucket"),
    table([{ h: "Bucket", f: (g) => g.bucket_name_hi || g.bucket || "" }, { h: "Entries", num: true, f: (g) => g.entries }, { h: "Spend", num: true, f: (g) => money(g.spend) }, { h: "Share", num: true, f: (g) => g.share_pct + "%" }], byBucket.groups),
    el("h3", {}, "By area"),
    table([{ h: "Area", f: (g) => g.area_name_hi || area(g.area) || "(no area)" }, { h: "Entries", num: true, f: (g) => g.entries }, { h: "Spend", num: true, f: (g) => money(g.spend) }, { h: "Share", num: true, f: (g) => g.share_pct + "%" }, { h: "Per 1,000 electors", num: true, f: (g) => money(g.spend_per_1000_electors) }], byArea.groups),
    el("h3", {}, `Need versus spend (${nvs.interpretation})`),
    table([
      { h: "Area", f: (a) => a.name_hi || a.area_id }, { h: "Electors", num: true, f: (a) => a.electors ?? "—" },
      { h: "Open reports", num: true, f: (a) => a.open_reports }, { h: "Open issues", num: true, f: (a) => a.open_issues },
      { h: "Need / 1,000", num: true, f: (a) => a.need_per_1000 ?? "—" }, { h: "Spend", num: true, f: (a) => money(a.spend) },
      { h: "Spend / 1,000", num: true, f: (a) => money(a.spend_per_1000) }, { h: "Need rank", num: true, f: (a) => a.need_rank ?? "—" },
      { h: "Spend rank", num: true, f: (a) => a.spend_rank ?? "—" }, { h: "Gap", num: true, f: (a) => a.rank_gap ?? "—" },
    ], nvs.areas),
  ];
}

async function reportsPanel() {
  const list = await field("/reports?limit=50");
  const details = await Promise.all(list.items.map((r) => field(`/reports/${r.report_id}`)));
  return [el("h3", {}, `Inbox (${list.total}) — urgent first, then unsorted`), ...details.map((d, i) => reportCard(list.items[i], d))];
}

function reportCard(row, d) {
  const r = d.report, tr = d.transcript;
  const media = d.media.filter((m) => m.url).map((m) => m.kind === "voice"
    ? el("audio", { controls: "", preload: "none", src: m.url })
    : el("img", { src: m.url, alt: m.role === "closing" ? "closing photo" : "photo", title: m.role }));
  const spend = d.linked_spend;
  return el("div", { class: "report" },
    el("div", { class: "media" }, media.length ? media : el("span", { class: "muted" }, "no media")),
    el("div", {},
      el("h4", {}, `${row.report_no} `, row.urgency === "urgent" ? el("span", { class: "chip urgent" }, "urgent") : null,
        el("span", { class: `chip ${STATUS_CLS[row.status] || ""}` }, row.status), row.restricted ? el("span", { class: "chip bad" }, "restricted") : null),
      el("div", { class: "line" }, el("b", {}, row.category_name_hi || "(to be sorted)"), " · ", area(row.area_id), " · reporter ", row.reporter_name, " · filed ", ist(row.filed_at)),
      row.note_text ? el("div", { class: "line" }, row.note_text) : null,
      tr && tr.text ? el("div", { class: "line" }, tr.label ? el("span", { class: "label" }, tr.label) : null, " ", tr.text) : tr && tr.status !== "none" ? el("div", { class: "line muted" }, "transcript: " + tr.status) : null,
      d.issue ? el("div", { class: "line" }, "Issue ", el("b", {}, d.issue.issue_no), " ", d.issue.title, ` (${d.issue.status})`) : null,
      spend && spend.entries && spend.entries.length ? el("div", { class: "line" }, `Linked spend: ${money(spend.total)} in ${spend.entries.length} entr${spend.entries.length === 1 ? "y" : "ies"}`) : null,
      el("details", {}, el("summary", {}, `Timeline (${d.events.length})`),
        el("ul", { class: "tl" }, d.events.map((e) => el("li", {}, `${ist(e.at)} · ${e.kind}${e.actor_name ? " · " + e.actor_name : ""}${e.note ? " · " + e.note : ""}`))))));
}

const PANELS = { ledger: ledgerPanel, balances: balancesPanel, spend: spendPanel, reports: reportsPanel };
let current = "ledger", pending = null;

function refreshPanels() {
  clearTimeout(pending);
  pending = setTimeout(async () => {
    const box = $(`#panel-${current}`);
    try { box.replaceChildren(...(await PANELS[current]())); } catch (e) { box.replaceChildren(el("p", { class: "muted" }, "Could not load: " + e.message)); }
  }, 150);
}

async function loadAreaNames() {
  try {
    const r = await field("/spend/need-vs-spend");
    for (const a of r.areas) S.areaNames[a.area_id] = a.name_hi;
  } catch (e) { /* ids are shown instead */ }
}

// ---- start -----------------------------------------------------------------------------------

let phones = [];

(async function start() {
  let st;
  for (;;) {
    try { st = await api("/demo/api/state"); break; } catch (e) { await new Promise((r) => setTimeout(r, 1000)); }
  }
  Object.assign(S, { token: st.token, ac: st.ac_number, members: st.members });
  showClock(st.now, st.offset_minutes);
  $("#viewer").textContent = "Panels: the principal's view (finance scope all)";
  for (const s of st.skips) $("#skips").append(el("button", { type: "button", onclick: (ev) => skip(s.minutes, ev.target) }, s.label));
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#tabs button, .panel").forEach((x) => x.classList.remove("on"));
    b.classList.add("on"); $(`#panel-${b.dataset.tab}`).classList.add("on");
    current = b.dataset.tab; history.replaceState(null, "", "#" + current); refreshPanels();
  }));
  const wanted = document.querySelector(`#tabs button[data-tab="${location.hash.slice(1)}"]`);
  if (wanted) wanted.click();
  await loadAreaNames();
  phones = [...document.querySelectorAll(".phone")].map((p) => new Phone(p));
  phones.forEach((p) => p.poll(true));
  refreshPanels();
  // the worker thread also sends (alerts, reminders): watch both chats and refresh the panels on change
  setInterval(async () => {
    const before = phones.map((p) => p.rev);
    await Promise.all(phones.map((p) => p.poll()));
    if (phones.some((p, i) => p.rev !== before[i])) refreshPanels();
  }, 1500);
})();
