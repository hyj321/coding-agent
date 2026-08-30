const homeView = document.getElementById("homeView");
const chatView = document.getElementById("chatView");
const timeline = document.getElementById("timeline");
const composer = document.getElementById("composer");
const chatInput = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");
const workdirInput = document.getElementById("workdirInput");
const maxStepsInput = document.getElementById("maxStepsInput");
const suggestionCards = document.getElementById("suggestionCards");
const recentList = document.getElementById("recentList");
const historySearch = document.getElementById("historySearch");
const todoList = document.getElementById("todoList");
const todoEmpty = document.getElementById("todoEmpty");
const runStatus = document.getElementById("runStatus");
const contextRing = document.getElementById("contextRing");
const contextRingArc = document.getElementById("contextRingArc");
const contextRingDetail = document.getElementById("contextRingDetail");
const attachChips = document.getElementById("attachChips");
const workspace = document.querySelector(".workspace");
const composerWrap = document.querySelector(".composer-wrap");
const fileTree = document.getElementById("fileTree");
const filesWorkdir = document.getElementById("filesWorkdir");
const filesPane = document.getElementById("filesPane");
const planPane = document.getElementById("planPane");
const changedBar = document.getElementById("changedBar");
const changedList = document.getElementById("changedList");
const folderModal = document.getElementById("folderModal");
const folderPathInput = document.getElementById("folderPathInput");
const folderListing = document.getElementById("folderListing");
const codeModal = document.getElementById("codeModal");
const codeTitle = document.getElementById("codeTitle");
const codeTabName = document.getElementById("codeTabName");
const codeModeBadge = document.getElementById("codeModeBadge");
const codeLangLabel = document.getElementById("codeLangLabel");
const monacoHost = document.getElementById("monacoHost");
const diffActions = document.getElementById("diffActions");
const btnDiffUndo = document.getElementById("btnDiffUndo");
const btnDiffRedo = document.getElementById("btnDiffRedo");
const approvalBar = document.getElementById("approvalBar");
const approvalRisk = document.getElementById("approvalRisk");
const approvalTool = document.getElementById("approvalTool");
const approvalSummary = document.getElementById("approvalSummary");
const btnApprovalAllow = document.getElementById("btnApprovalAllow");
const btnApprovalDeny = document.getElementById("btnApprovalDeny");

let running = false;
let pendingApprovalId = null;
let pendingApprovalCallId = null;
let historyCache = [];
let stepCards = new Map();
let activeStep = null;
/** One conversation (= one history / memory unit). */
let sessionId = null;
let sessionActive = false;
/** Offset so multi-turn sessions don't reuse Step 1 badges in the DOM map. */
let stepKeyBase = 0;
/** path -> { path, old_content, new_content, is_new, tool } */
let changedFiles = new Map();
let projectRoot = "";
/** path -> { path, kind: "file"|"dir" } — dragged into composer */
let attachedPaths = new Map();
let monacoReady = null;
let monacoEditor = null;
let monacoDiff = null;
let monacoMode = null; // "code" | "diff"
/** Active diff for Undo/Redo: { path, old_content, new_content, is_new, applied: "modified"|"original" } */
let activeDiff = null;

const ICONS = ["🌐", "📈", "📄"];

function newSessionId() {
  if (crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "").slice(0, 24);
  return `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

/** Circumference of r=14 ring: 2πr */
const CONTEXT_RING_LEN = 87.96;
let contextRingOpen = false;
let lastContextUsage = null;

function setStatus(mode, text) {
  runStatus.className = `run-status ${mode}`;
  runStatus.textContent = text;
}

function hideApprovalBar() {
  pendingApprovalId = null;
  pendingApprovalCallId = null;
  if (approvalBar) approvalBar.classList.add("hidden");
  if (btnApprovalAllow) btnApprovalAllow.disabled = false;
  if (btnApprovalDeny) btnApprovalDeny.disabled = false;
}

function markToolAwaiting(callId, awaiting) {
  if (!callId || activeStep == null) return;
  const card = stepCards.get(activeStep);
  if (!card) return;
  const block = findToolBlock(card, callId);
  if (!block) return;
  block.classList.toggle("awaiting", !!awaiting);
  const status = block.querySelector(".tool-status");
  if (status && awaiting) status.textContent = "等待授权…";
}

function showApprovalRequest(data) {
  pendingApprovalId = data.request_id;
  pendingApprovalCallId = data.call_id || null;
  const risk = (data.risk_level || "medium").toLowerCase();
  if (approvalRisk) approvalRisk.textContent = risk;
  if (approvalTool) approvalTool.textContent = data.tool_name || "tool";
  if (approvalSummary) approvalSummary.textContent = data.summary || "";
  if (approvalBar) {
    approvalBar.classList.toggle("is-high", risk === "high");
    approvalBar.classList.remove("hidden");
  }
  if (btnApprovalAllow) btnApprovalAllow.disabled = false;
  if (btnApprovalDeny) btnApprovalDeny.disabled = false;
  markToolAwaiting(pendingApprovalCallId, true);
  setStatus("running", "等待授权…");
  approvalBar?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function respondApproval(allowed) {
  if (!pendingApprovalId || !running) return;
  const requestId = pendingApprovalId;
  if (btnApprovalAllow) btnApprovalAllow.disabled = true;
  if (btnApprovalDeny) btnApprovalDeny.disabled = true;
  try {
    const res = await fetch("/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id: requestId, allowed: !!allowed }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    setStatus("running", allowed ? "已允许，继续执行…" : "已拒绝，继续执行…");
  } catch (err) {
    addInfoBubble(`授权失败：${err}`);
    if (btnApprovalAllow) btnApprovalAllow.disabled = false;
    if (btnApprovalDeny) btnApprovalDeny.disabled = false;
  }
}

function formatContextDetail(data) {
  if (!data) return "—%";
  const remaining = Math.max(0, Math.min(100, Number(data.remaining_pct)));
  const scope = data.scope === "session" ? "会话" : "本轮";
  if (data.used_tokens != null && data.budget_tokens != null) {
    return `剩余 ${remaining}% · ${data.used_tokens}/${data.budget_tokens} tok · ${scope}`;
  }
  return `剩余 ${remaining}% · ${scope}`;
}

function updateContextMeter(data) {
  if (!contextRing || !data) return;
  lastContextUsage = data;
  const remaining = Math.max(0, Math.min(100, Number(data.remaining_pct)));
  const used = Math.max(0, Math.min(100, Number(data.used_pct ?? 100 - remaining)));
  const level = data.level || (remaining <= 15 ? "critical" : remaining <= 35 ? "warn" : "ok");
  contextRing.dataset.level = level;
  // Ring fill = used portion (fills up as context is consumed)
  if (contextRingArc) {
    const offset = CONTEXT_RING_LEN * (1 - used / 100);
    contextRingArc.style.strokeDashoffset = String(offset);
  }
  if (contextRingDetail) {
    contextRingDetail.textContent = formatContextDetail(data);
  }
  const hint = data.hint || "Approximate context capacity";
  contextRing.title = contextRingOpen
    ? hint
    : `上下文剩余 ${Number.isFinite(remaining) ? remaining : "—"}%（点击查看明细）`;
  contextRing.setAttribute("aria-label", `上下文剩余 ${remaining}%`);
}

function resetContextMeter() {
  lastContextUsage = null;
  updateContextMeter({
    remaining_pct: 100,
    used_pct: 0,
    level: "ok",
    scope: "turn",
    hint: "发送任务后显示用量",
    used_tokens: 0,
    budget_tokens: null,
  });
  if (contextRingDetail) contextRingDetail.textContent = "—%";
  if (contextRing) {
    contextRing.title = "发送任务后显示用量";
    contextRing.setAttribute("aria-label", "上下文用量");
  }
}

/** Restore ring from a saved turn / session payload (or estimate from messages). */
function applyStoredContextUsage(data) {
  if (!data) {
    resetContextMeter();
    return;
  }
  const turns = data.turns || [];
  let usage = null;
  if (turns.length) {
    for (let i = turns.length - 1; i >= 0; i--) {
      const cu = turns[i]?.context_usage;
      if (cu && typeof cu === "object" && cu.remaining_pct != null) {
        usage = cu;
        break;
      }
    }
  }
  if (!usage && data.context_usage && typeof data.context_usage === "object") {
    usage = data.context_usage;
  }
  if (!usage && data.memory && typeof data.memory === "object") {
    const cu = data.memory.context_usage;
    if (cu && typeof cu === "object") usage = cu;
  }
  if (!usage && Array.isArray(data.messages) && data.messages.length) {
    usage = estimateContextUsageFromMessages(data.messages);
  }
  if (usage) updateContextMeter(usage);
  else resetContextMeter();
}

function estimateContextUsageFromMessages(messages, budgetTokens = 32000) {
  let chars = 0;
  for (const m of messages) {
    try {
      chars += JSON.stringify(m).length;
    } catch (_) {
      chars += 200;
    }
  }
  const used = Math.max(0, Math.ceil(chars / 4));
  const budget = Math.max(1, Number(budgetTokens) || 32000);
  const usedPct = Math.min(100, Math.round((100 * used) / budget));
  const remaining = Math.max(0, 100 - usedPct);
  const level = remaining <= 15 ? "critical" : remaining <= 35 ? "warn" : "ok";
  return {
    scope: "turn",
    used_tokens: used,
    budget_tokens: budget,
    used_pct: usedPct,
    remaining_pct: remaining,
    level,
    hint: "（由历史消息估算）",
  };
}

function toggleContextRingDetail() {
  if (!contextRing) return;
  contextRingOpen = !contextRingOpen;
  contextRing.classList.toggle("is-open", contextRingOpen);
  contextRing.setAttribute("aria-expanded", contextRingOpen ? "true" : "false");
  if (contextRingDetail) {
    contextRingDetail.hidden = !contextRingOpen;
  }
  if (lastContextUsage) updateContextMeter(lastContextUsage);
  else if (contextRing) {
    contextRing.title = contextRingOpen
      ? "发送任务后显示用量"
      : "发送任务后显示用量（点击查看明细）";
  }
}

function renderRich(text) {
  const raw = String(text ?? "");
  try {
    if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
      const html = marked.parse(raw, { breaks: true, gfm: true });
      return DOMPurify.sanitize(html);
    }
  } catch (_) {
    /* fall through */
  }
  return escapeHtml(raw).replaceAll("\n", "<br>");
}

function showChat() {
  homeView.classList.add("hidden");
  chatView.classList.remove("hidden");
}

function resetSessionUI() {
  // Detach before wiping timeline so the node is not destroyed
  if (changedBar && changedBar.parentElement === timeline) {
    chatView.appendChild(changedBar);
  }
  timeline.innerHTML = "";
  stepCards = new Map();
  activeStep = null;
  stepKeyBase = 0;
  changedFiles = new Map();
  renderChangedBar();
  todoList.innerHTML = "";
  todoEmpty.classList.remove("hidden");
  setStatus("idle", "空闲");
  resetContextMeter();
  clearAttachedPaths();
  hideApprovalBar();
}

function mapStep(step) {
  return stepKeyBase + step;
}

function showHome() {
  homeView.classList.remove("hidden");
  chatView.classList.add("hidden");
  sessionId = null;
  sessionActive = false;
  resetSessionUI();
  resetContextMeter();
}

function scrollChat() {
  chatView.scrollTop = chatView.scrollHeight;
}

function focusActiveCard(card) {
  if (!card) return;
  requestAnimationFrame(() => {
    card.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
  });
}

function collapseStepCard(card) {
  if (!card) return;
  card.classList.remove("is-active");
  card.classList.add("is-collapsed");
  const tools = card.querySelectorAll(".tool-name");
  const names = [...tools].map((n) => n.textContent).filter(Boolean);
  const summary = card.querySelector(".step-summary");
  if (summary) {
    summary.textContent = names.length ? names.join(" · ") : "completed";
  }
}

function setActiveStep(step, maxSteps) {
  const key = mapStep(step);
  activeStep = key;
  const card = ensureStepCard(step, maxSteps);
  stepCards.forEach((c, s) => {
    if (s === key) {
      c.classList.add("is-active");
      c.classList.remove("is-collapsed");
    } else {
      c.classList.remove("is-active");
    }
  });
  focusActiveCard(card);
  return card;
}

function addUserBubble(text) {
  const row = document.createElement("div");
  row.className = "bubble-row user";
  const msg = document.createElement("div");
  msg.className = "msg user";
  const body = document.createElement("div");
  body.className = "rich";
  body.innerHTML = renderRich(text);
  msg.appendChild(body);
  row.appendChild(msg);
  timeline.appendChild(row);
  scrollChat();
}

function formatStoppedReason(reason) {
  const map = {
    completed: "已完成",
    max_steps: "达到最大步数",
    interrupted: "已中断",
    loop_detected: "检测到循环",
    retry_exhausted: "重试耗尽",
    goal_met_forced: "目标已达成（强制收尾）",
  };
  return map[reason] || reason || "";
}

function addFinalBubble(text, meta) {
  if (activeStep != null) {
    const card = stepCards.get(activeStep);
    if (card) collapseStepCard(card);
  }
  activeStep = null;

  const row = document.createElement("div");
  row.className = "bubble-row agent";
  const msg = document.createElement("div");
  msg.className = "msg final";
  msg.innerHTML = `<div class="tag">FINAL</div>`;
  const body = document.createElement("div");
  body.className = "rich";
  body.innerHTML = renderRich(text);
  msg.appendChild(body);
  if (meta) {
    const foot = document.createElement("div");
    foot.className = "final-meta";
    foot.textContent = meta;
    msg.appendChild(foot);
  }
  row.appendChild(msg);
  timeline.appendChild(row);
  // Keep changed-files bar under the latest FINAL output
  if (changedFiles.size) renderChangedBar();
  focusActiveCard(msg);
}

function addInfoBubble(text) {
  const row = document.createElement("div");
  row.className = "bubble-row agent";
  row.innerHTML = `<div class="msg info"><div class="tag">INFO</div><div class="rich">${renderRich(text)}</div></div>`;
  timeline.appendChild(row);
  scrollChat();
}

/** Collapsed-by-default turn summary (keeps the chat readable). */
function addTurnSummaryBubble(text) {
  const body = String(text || "").trim();
  const row = document.createElement("div");
  row.className = "bubble-row agent";
  const msg = document.createElement("div");
  msg.className = "msg info turn-summary";
  msg.innerHTML = `<div class="tag">INFO</div>`;
  const details = document.createElement("details");
  details.className = "turn-summary-details";
  // Default collapsed after generation
  details.open = false;
  const summary = document.createElement("summary");
  summary.className = "turn-summary-summary";
  summary.innerHTML =
    `<span class="turn-summary-title">本轮自动总结</span>` +
    `<span class="turn-summary-hint">点击展开</span>`;
  const content = document.createElement("div");
  content.className = "rich turn-summary-body";
  content.innerHTML = renderRich(body);
  details.appendChild(summary);
  details.appendChild(content);
  msg.appendChild(details);
  row.appendChild(msg);
  timeline.appendChild(row);
  scrollChat();
}

function ensureStepCard(step, maxSteps) {
  const key = mapStep(step);
  if (stepCards.has(key)) {
    const existing = stepCards.get(key);
    if (maxSteps) {
      const badge = existing.querySelector(".step-badge");
      if (badge) badge.textContent = `Step ${step}/${maxSteps}`;
    }
    return existing;
  }
  const card = document.createElement("div");
  card.className = "step-card is-active";
  card.dataset.step = String(key);
  card.innerHTML = `
    <button type="button" class="step-head" aria-expanded="true">
      <span class="step-badge">Step ${step}${maxSteps ? "/" + maxSteps : ""}</span>
      <span class="step-summary"></span>
      <span class="step-state">running</span>
      <span class="step-chevron" aria-hidden="true">▾</span>
    </button>
    <div class="step-body">
      <div class="step-tools"></div>
    </div>
  `;
  const head = card.querySelector(".step-head");
  head.addEventListener("click", () => {
    const collapsed = card.classList.toggle("is-collapsed");
    head.setAttribute("aria-expanded", collapsed ? "false" : "true");
  });
  timeline.appendChild(card);
  stepCards.set(key, card);
  focusActiveCard(card);
  return card;
}

function findToolBlock(card, callId) {
  return card.querySelector(`.tool-block[data-id="${CSS.escape(callId)}"]`);
}

function addToolCall(step, data) {
  const card = setActiveStep(step, data.max_steps);
  const tools = card.querySelector(".step-tools");
  let block = findToolBlock(card, data.id);
  if (!block) {
    block = document.createElement("div");
    block.className = "tool-block pending";
    block.dataset.id = data.id;
    block.innerHTML = `
      <div class="tool-head">
        <span class="tool-name">${escapeHtml(data.name)}</span>
        <span class="tool-status">calling…</span>
      </div>
      <pre class="tool-args"></pre>
      <details class="tool-result hidden"><summary>Result</summary><pre></pre></details>
    `;
    tools.appendChild(block);
  }
  block.querySelector(".tool-args").textContent = data.arguments_summary || data.arguments || "";
  focusActiveCard(card);
}

function addToolResult(step, data) {
  const card = setActiveStep(step);
  let block = findToolBlock(card, data.id);
  if (!block) {
    addToolCall(step, data);
    block = findToolBlock(card, data.id);
  }
  block.classList.remove("pending");
  block.classList.add(data.ok ? "ok" : "err");
  block.querySelector(".tool-status").textContent = data.ok ? "ok" : "error";
  const details = block.querySelector(".tool-result");
  details.classList.remove("hidden");
  const pre = details.querySelector("pre");
  const resultText = data.result || data.result_summary || "";
  if (/\*\*|`|# |Todo list:/.test(resultText)) {
    const wrap = document.createElement("div");
    wrap.className = "rich tool-rich";
    wrap.innerHTML = renderRich(resultText);
    details.replaceChild(wrap, pre);
  } else {
    pre.textContent = resultText;
  }
  focusActiveCard(card);
}

function markStepDone(step) {
  const card = stepCards.get(mapStep(step));
  if (!card) return;
  const state = card.querySelector(".step-state");
  state.textContent = "done";
  state.classList.add("done");
  collapseStepCard(card);
  card.querySelector(".step-head")?.setAttribute("aria-expanded", "false");
}

function renderTodos(todos) {
  todoList.innerHTML = "";
  if (!todos || !todos.length) {
    todoEmpty.classList.remove("hidden");
    return;
  }
  todoEmpty.classList.add("hidden");
  // Auto-switch to Plan tab when todos appear
  switchRightTab("plan");
  todos.forEach((t) => {
    const li = document.createElement("div");
    li.className = `todo-item status-${t.status}`;
    const mark =
      t.status === "completed" ? "[x]" :
      t.status === "in_progress" ? "[>]" :
      t.status === "cancelled" ? "[-]" : "[ ]";
    li.innerHTML = `<span class="todo-mark">${mark}</span><span class="todo-text">${escapeHtml(t.content)}</span>`;
    todoList.appendChild(li);
  });
}

function renderChangedBar() {
  changedList.innerHTML = "";
  if (!changedFiles.size) {
    changedBar.classList.add("hidden");
    changedBar.hidden = true;
    return;
  }
  changedBar.hidden = false;
  changedBar.classList.remove("hidden");
  // Always pin to the end of the timeline (after FINAL / latest output)
  if (changedBar.parentElement !== timeline) {
    timeline.appendChild(changedBar);
  } else {
    timeline.appendChild(changedBar);
  }
  for (const ch of changedFiles.values()) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `changed-chip${ch.is_new ? " is-new" : ""}`;
    btn.textContent = ch.is_new ? `${ch.path} (new)` : ch.path;
    btn.title = "View diff · Undo/Redo available";
    btn.addEventListener("click", () => openDiff(ch));
    changedList.appendChild(btn);
  }
  scrollChat();
}

function recordFileChange(data) {
  const path = data.path;
  if (!path) return;
  const prev = changedFiles.get(path);
  changedFiles.set(path, {
    path,
    tool: data.tool,
    is_new: prev ? prev.is_new && data.is_new : !!data.is_new,
    old_content: prev ? prev.old_content : (data.old_content ?? ""),
    new_content: data.new_content ?? "",
  });
  renderChangedBar();
  loadFileTree();
}

function langFromPath(path) {
  const ext = String(path).split(".").pop()?.toLowerCase() || "";
  const map = {
    py: "python",
    js: "javascript",
    mjs: "javascript",
    cjs: "javascript",
    ts: "typescript",
    tsx: "typescript",
    jsx: "javascript",
    json: "json",
    md: "markdown",
    html: "html",
    htm: "html",
    css: "css",
    scss: "scss",
    less: "less",
    yml: "yaml",
    yaml: "yaml",
    sh: "shell",
    bash: "shell",
    ps1: "powershell",
    sql: "sql",
    rs: "rust",
    go: "go",
    java: "java",
    c: "c",
    h: "c",
    cpp: "cpp",
    hpp: "cpp",
    xml: "xml",
    toml: "ini",
    ini: "ini",
    txt: "plaintext",
  };
  return map[ext] || "plaintext";
}

function loadMonaco() {
  if (monacoReady) return monacoReady;
  monacoReady = new Promise((resolve, reject) => {
    if (typeof require === "undefined") {
      reject(new Error("Monaco loader missing"));
      return;
    }
    require.config({
      paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs" },
    });
    // eslint-disable-next-line no-undef
    require(["vs/editor/editor.main"], () => resolve(window.monaco), reject);
  });
  return monacoReady;
}

function disposeMonacoEditors() {
  if (monacoEditor) {
    monacoEditor.dispose();
    monacoEditor = null;
  }
  if (monacoDiff) {
    monacoDiff.dispose();
    monacoDiff = null;
  }
  monacoMode = null;
  monacoHost.innerHTML = "";
  activeDiff = null;
  setDiffActionsVisible(false);
}

function setDiffActionsVisible(show) {
  if (!diffActions) return;
  diffActions.classList.toggle("hidden", !show);
}

function updateDiffActionButtons() {
  if (!activeDiff) {
    if (btnDiffUndo) btnDiffUndo.disabled = true;
    if (btnDiffRedo) btnDiffRedo.disabled = true;
    return;
  }
  const atModified = activeDiff.applied === "modified";
  if (btnDiffUndo) {
    btnDiffUndo.disabled = !atModified;
    btnDiffUndo.classList.toggle("is-active", !atModified);
  }
  if (btnDiffRedo) {
    btnDiffRedo.disabled = atModified;
    btnDiffRedo.classList.toggle("is-active", atModified);
  }
  const status = document.getElementById("codeStatusRight");
  if (status) {
    status.textContent =
      activeDiff.applied === "modified"
        ? "Disk = modified · Undo restores original"
        : "Disk = original · Redo re-applies change";
  }
}

async function openCodeViewer(path, content, { modeBadge = "Preview" } = {}) {
  const lang = langFromPath(path);
  codeTitle.textContent = path;
  codeTabName.textContent = path.split(/[/\\]/).pop() || path;
  codeModeBadge.textContent = modeBadge;
  codeLangLabel.textContent = lang;
  codeModal.classList.remove("hidden");
  disposeMonacoEditors();
  setDiffActionsVisible(false);
  try {
    const monaco = await loadMonaco();
    monacoMode = "code";
    monacoEditor = monaco.editor.create(monacoHost, {
      value: content ?? "",
      language: lang,
      theme: "vs-dark",
      readOnly: true,
      automaticLayout: true,
      minimap: { enabled: true },
      fontSize: 13,
      fontFamily: "Consolas, 'Courier New', monospace",
      lineNumbers: "on",
      renderLineHighlight: "line",
      scrollBeyondLastLine: false,
      wordWrap: "off",
      padding: { top: 8 },
    });
  } catch (err) {
    monacoHost.innerHTML = `<pre style="color:#ccc;padding:16px;margin:0;white-space:pre-wrap">${escapeHtml(content || String(err))}</pre>`;
  }
}

async function openDiff(ch) {
  const path = ch.path || "file";
  const lang = langFromPath(path);
  codeTitle.textContent = `${path} (diff)`;
  codeTabName.textContent = path.split(/[/\\]/).pop() || path;
  codeModeBadge.textContent = ch.is_new ? "New file" : "Diff";
  codeLangLabel.textContent = lang;
  codeModal.classList.remove("hidden");
  disposeMonacoEditors();
  activeDiff = {
    path,
    old_content: ch.old_content ?? "",
    new_content: ch.new_content ?? "",
    is_new: !!ch.is_new,
    applied: "modified",
  };
  setDiffActionsVisible(true);
  updateDiffActionButtons();
  try {
    const monaco = await loadMonaco();
    monacoMode = "diff";
    const original = monaco.editor.createModel(activeDiff.old_content, lang);
    const modified = monaco.editor.createModel(activeDiff.new_content, lang);
    monacoDiff = monaco.editor.createDiffEditor(monacoHost, {
      theme: "vs-dark",
      readOnly: true,
      automaticLayout: true,
      renderSideBySide: true,
      enableSplitViewResizing: true,
      minimap: { enabled: false },
      fontSize: 13,
      fontFamily: "Consolas, 'Courier New', monospace",
      originalEditable: false,
      renderIndicators: true,
      ignoreTrimWhitespace: false,
    });
    monacoDiff.setModel({ original, modified });
  } catch (err) {
    monacoHost.innerHTML = `<pre style="color:#ccc;padding:16px;margin:0;white-space:pre-wrap">Diff failed: ${escapeHtml(String(err))}</pre>`;
  }
}

function formatApiDetail(data, res) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d.map((x) => x.msg || JSON.stringify(x)).join("; ");
  }
  if (d != null) return JSON.stringify(d);
  return `HTTP ${res.status}`;
}

function normalizeRelPath(path) {
  let p = String(path || "").trim().replace(/\\/g, "/");
  const wd = (workdirInput.value || "").trim().replace(/\\/g, "/");
  if (wd && p.toLowerCase().startsWith(wd.toLowerCase() + "/")) {
    p = p.slice(wd.length + 1);
  }
  // Strip leading ./ 
  p = p.replace(/^\.\//, "");
  return p;
}

async function applyDiffToDisk(which) {
  if (!activeDiff || running) return;
  const wd = workdirInput.value.trim() || "demos";
  const path = normalizeRelPath(activeDiff.path);
  if (!path) {
    alert("Undo/Redo failed: empty path");
    return;
  }
  try {
    if (which === "original") {
      if (activeDiff.is_new) {
        const res = await fetch("/api/fs/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workdir: wd, path }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          // Fallback: write empty / old content if delete endpoint missing (old server)
          if (res.status === 404) {
            const w = await fetch("/api/fs/write", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                workdir: wd,
                path,
                content: activeDiff.old_content ?? "",
              }),
            });
            const wdData = await w.json().catch(() => ({}));
            if (!w.ok) {
              throw new Error(
                formatApiDetail(wdData, w) +
                  (w.status === 404
                    ? " — 请重启 Web 服务：python -m src.web"
                    : "")
              );
            }
          } else {
            throw new Error(formatApiDetail(data, res));
          }
        }
      } else {
        const res = await fetch("/api/fs/write", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            workdir: wd,
            path,
            content: activeDiff.old_content ?? "",
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(
            formatApiDetail(data, res) +
              (res.status === 404 ? " — 请重启 Web 服务：python -m src.web" : "")
          );
        }
      }
      activeDiff.applied = "original";
      codeModeBadge.textContent = "Undone";
    } else {
      const res = await fetch("/api/fs/write", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workdir: wd,
          path,
          content: activeDiff.new_content ?? "",
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          formatApiDetail(data, res) +
            (res.status === 404 ? " — 请重启 Web 服务：python -m src.web" : "")
        );
      }
      activeDiff.applied = "modified";
      codeModeBadge.textContent = activeDiff.is_new ? "New file" : "Diff";
    }
    updateDiffActionButtons();
    loadFileTree();
  } catch (err) {
    alert("Undo/Redo failed: " + err);
  }
}

function renderSuggestions(items) {
  suggestionCards.innerHTML = "";
  items.forEach((item, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "card";
    btn.innerHTML = `
      <div class="card-banner">${ICONS[i % ICONS.length]}</div>
      <div class="card-body">
        <div class="card-title">${escapeHtml(item.title)}</div>
        <p class="card-desc">${escapeHtml(item.desc)}</p>
      </div>`;
    btn.addEventListener("click", () => {
      startRun(item.prompt);
    });
    suggestionCards.appendChild(btn);
  });
}

function renderHistory(items) {
  const q = (historySearch.value || "").trim().toLowerCase();
  const filtered = !q
    ? items
    : items.filter((x) => (x.title || "").toLowerCase().includes(q) || (x.task || "").toLowerCase().includes(q));
  recentList.innerHTML = "";
  if (!filtered.length) {
    recentList.innerHTML = `<div class="recent-item"><span>No chats yet</span></div>`;
    return;
  }
  filtered.forEach((item) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "recent-item";
    const kind = item.kind === "session" ? "🗂" : "💬";
    btn.innerHTML = `<span class="bubble">${kind}</span><span>${escapeHtml(item.title)}</span>`;
    btn.addEventListener("click", () => loadTranscript(item.id));
    recentList.appendChild(btn);
  });
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function handleEvent(data) {
  switch (data.type) {
    case "start":
      addInfoBubble(`workdir: ${data.workdir}`);
      if (data.session_id) sessionId = data.session_id;
      break;
    case "run_start":
      break;
    case "step_start":
      setActiveStep(data.step, data.max_steps);
      break;
    case "think":
      {
        const card = setActiveStep(data.step);
        const think = document.createElement("div");
        think.className = "step-think rich";
        think.innerHTML = renderRich(data.text || "");
        card.querySelector(".step-tools").prepend(think);
        focusActiveCard(card);
      }
      break;
    case "tool_call":
      addToolCall(data.step, data);
      break;
    case "tool_result":
      addToolResult(data.step, data);
      break;
    case "approval_request":
      showApprovalRequest(data);
      break;
    case "approval_resolved":
      markToolAwaiting(data.call_id || pendingApprovalCallId, false);
      if (!data.request_id || data.request_id === pendingApprovalId) {
        hideApprovalBar();
      }
      if (running) setStatus("running", "运行中…");
      break;
    case "auth_decision":
      {
        const key = data.step != null ? mapStep(data.step) : activeStep;
        const card = key != null ? stepCards.get(key) : null;
        const block = card && data.id ? findToolBlock(card, data.id) : null;
        if (block) {
          block.classList.remove("awaiting");
          const status = block.querySelector(".tool-status");
          if (status && !data.allowed) {
            status.textContent = data.decision === "deny" ? "已拒绝" : "已拦截";
          } else if (status && data.decision === "confirm") {
            status.textContent = "已批准";
          }
        }
      }
      break;
    case "file_change":
      recordFileChange(data);
      break;
    case "todo_update":
      renderTodos(data.todos || []);
      break;
    case "context_usage":
      updateContextMeter(data);
      break;
    case "turn_summary":
      addTurnSummaryBubble(data.text || "");
      if (data.context_usage) updateContextMeter(data.context_usage);
      break;
    case "step_end":
      markStepDone(data.step);
      break;
    case "final":
      break;
    case "done":
      hideApprovalBar();
      addFinalBubble(
        data.final_text || "",
        `${formatStoppedReason(data.stopped_reason)} · ${data.steps} 步` +
          (data.transcript_id ? ` · ${data.transcript_id}` : "")
      );
      setStatus("idle", "完成");
      sessionActive = true;
      break;
    case "error":
      hideApprovalBar();
      addInfoBubble(data.message || "未知错误");
      setStatus("err", "错误");
      break;
    case "log":
      break;
    default:
      break;
  }
}

function replayTranscript(data) {
  showChat();
  resetSessionUI();
  sessionId = data.session_id || (data.meta || {}).session_id || null;
  sessionActive = !!sessionId;

  const task = data.task || "";
  const turns = data.turns || [];
  if (turns.length > 1) {
    addInfoBubble(`Session · ${turns.length} turns · ${data.updated_at || data.created_at || ""}`);
    turns.forEach((t, i) => {
      addUserBubble(t.task || task);
      if (i < turns.length - 1 && t.final_text) {
        addFinalBubble(t.final_text, `${t.stopped_reason || ""} · turn ${i + 1}`);
      }
    });
  } else {
    addUserBubble(task);
    addInfoBubble(`Replay · ${data.created_at || ""} · ${data.stopped_reason || ""}`);
  }

  const wd = (data.meta || {}).workdir;
  if (wd) {
    workdirInput.value = wd;
    loadFileTree();
  }

  const messages = data.messages || [];
  let step = 0;
  // For multi-turn sessions, only replay tool steps from the message log once
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (m.role === "assistant" && m.tool_calls && m.tool_calls.length) {
      step += 1;
      ensureStepCard(step);
      if (m.content) {
        handleEvent({ type: "think", step, text: m.content });
      }
      const resultsById = {};
      for (let j = i + 1; j < messages.length; j++) {
        const t = messages[j];
        if (t.role !== "tool") break;
        resultsById[t.tool_call_id] = t.content || "";
      }
      for (const tc of m.tool_calls) {
        const name = tc.function?.name || "tool";
        const args = tc.function?.arguments || "{}";
        handleEvent({
          type: "tool_call",
          step,
          id: tc.id,
          name,
          arguments: args,
          arguments_summary: args.length > 120 ? args.slice(0, 120) + "..." : args,
        });
        const result = resultsById[tc.id] || "";
        handleEvent({
          type: "tool_result",
          step,
          id: tc.id,
          name,
          ok: !String(result).startsWith("Error"),
          result,
          result_summary: result,
        });
        if (name === "todo_write" && String(result).startsWith("Todo list:")) {
          const todos = parseTodoText(result);
          if (todos.length) renderTodos(todos);
        }
      }
      markStepDone(step);
    }
  }

  // Only this turn's file changes (last turn on session, or top-level file_changes)
  changedFiles = new Map();
  renderChangedBar();
  let changes = [];
  if (turns.length) {
    const last = turns[turns.length - 1];
    if (Array.isArray(last.file_changes) && last.file_changes.length) {
      changes = last.file_changes;
    }
  }
  if (!changes.length) {
    changes = data.file_changes || [];
  }
  for (const ch of changes) recordFileChange(ch);

  addFinalBubble(
    data.final_text || "",
    `${data.stopped_reason || ""} · ${data.steps || step} steps (replay)`
  );
  setStatus("idle", "Replay");
  // Restore this session/turn's context ring (do not keep previous chat's meter)
  applyStoredContextUsage(data);
}

function parseTodoText(result) {
  const items = [];
  for (const line of String(result).split("\n")) {
    const m = line.match(/\s*\[([ x>\-])\]\s*\(([^)]+)\)\s*(.+)$/);
    if (!m) continue;
    const mark = m[1];
    const status =
      mark === "x" ? "completed" :
      mark === ">" ? "in_progress" :
      mark === "-" ? "cancelled" : "pending";
    items.push({ id: m[2], content: m[3].trim(), status });
  }
  return items;
}

async function loadTranscript(id) {
  if (running) return;
  try {
    const res = await fetch(`/api/history/${encodeURIComponent(id)}`);
    if (!res.ok) {
      addInfoBubble(`Failed to load history: HTTP ${res.status}`);
      return;
    }
    const data = await res.json();
    replayTranscript(data);
  } catch (err) {
    addInfoBubble(String(err));
  }
}

async function loadMeta() {
  const res = await fetch("/api/meta");
  const data = await res.json();
  projectRoot = data.project_root || "";
  if (data.default_workdir && !workdirInput.dataset.touched) {
    workdirInput.value = data.default_workdir;
  }
  renderSuggestions(data.suggestions || []);
  loadFileTree();
}

async function loadHistory() {
  const res = await fetch("/api/history");
  const data = await res.json();
  historyCache = data.items || [];
  renderHistory(historyCache);
}

async function loadFileTree() {
  const wd = workdirInput.value.trim() || "demos";
  filesWorkdir.textContent = wd;
  try {
    const res = await fetch(`/api/fs/tree?workdir=${encodeURIComponent(wd)}`);
    if (!res.ok) {
      fileTree.innerHTML = `<div class="todo-empty">Cannot list workdir</div>`;
      return;
    }
    const data = await res.json();
    filesWorkdir.textContent = data.workdir || wd;
    renderFileTree(data.nodes || []);
  } catch (err) {
    fileTree.innerHTML = `<div class="todo-empty">${escapeHtml(String(err))}</div>`;
  }
}

/** Flat API nodes → expandable tree. Directories start collapsed. */
function renderFileTree(nodes) {
  fileTree.innerHTML = "";
  if (!nodes.length) {
    fileTree.innerHTML = `<div class="todo-empty">Empty folder</div>`;
    return;
  }

  const rows = [];
  nodes.forEach((node, index) => {
    const btn = document.createElement("button");
    btn.type = "button";
    const depth = Number(node.depth) || 0;
    const isDir = node.kind === "dir";
    btn.className = `file-node${isDir ? " is-dir is-collapsed" : ""}`;
    btn.dataset.depth = String(depth);
    btn.dataset.kind = node.kind || "file";
    btn.dataset.path = node.path || node.name || "";
    btn.dataset.index = String(index);
    btn.style.paddingLeft = `${8 + depth * 14}px`;

    if (isDir) {
      btn.innerHTML = `
        <span class="file-chevron" aria-hidden="true">▸</span>
        <span class="icon">📁</span>
        <span class="file-name">${escapeHtml(node.name)}</span>`;
      btn.setAttribute("aria-expanded", "false");
      btn.title = "Click to expand / collapse · drag to attach";
      btn.draggable = true;
      btn.addEventListener("click", () => toggleFileTreeDir(btn));
      btn.addEventListener("dragstart", (e) => onFileDragStart(e, node.path, "dir"));
    } else {
      btn.innerHTML = `
        <span class="file-chevron-spacer" aria-hidden="true"></span>
        <span class="icon">📄</span>
        <span class="file-name">${escapeHtml(node.name)}</span>`;
      btn.title = "Click to preview · drag to composer to attach";
      btn.draggable = true;
      btn.addEventListener("click", () => openFileWindow(node.path));
      btn.addEventListener("dragstart", (e) => onFileDragStart(e, node.path, "file"));
    }
    fileTree.appendChild(btn);
    rows.push(btn);
  });

  // Apply initial collapsed visibility
  rows.forEach((btn) => {
    if (btn.dataset.kind === "dir" && btn.classList.contains("is-collapsed")) {
      setFileTreeChildrenHidden(btn, true);
    }
  });
}

function setFileTreeChildrenHidden(dirBtn, hidden) {
  const depth = Number(dirBtn.dataset.depth) || 0;
  let el = dirBtn.nextElementSibling;
  while (el && el.classList.contains("file-node")) {
    const d = Number(el.dataset.depth) || 0;
    if (d <= depth) break;
    if (hidden) {
      el.classList.add("is-hidden");
    } else {
      // Show only direct children; nested dirs keep their own collapse state
      const parentCollapsed = fileTreeAncestorCollapsed(el, dirBtn);
      if (!parentCollapsed) {
        el.classList.remove("is-hidden");
        if (el.dataset.kind === "dir" && el.classList.contains("is-collapsed")) {
          setFileTreeChildrenHidden(el, true);
        }
      }
    }
    el = el.nextElementSibling;
  }
}

function fileTreeAncestorCollapsed(nodeEl, stopAt) {
  const depth = Number(nodeEl.dataset.depth) || 0;
  let el = nodeEl.previousElementSibling;
  while (el && el.classList.contains("file-node")) {
    if (el === stopAt) return false;
    if (el.dataset.kind === "dir") {
      const d = Number(el.dataset.depth) || 0;
      if (d < depth && el.classList.contains("is-collapsed")) return true;
    }
    el = el.previousElementSibling;
  }
  return false;
}

function toggleFileTreeDir(dirBtn) {
  const collapse = !dirBtn.classList.contains("is-collapsed");
  dirBtn.classList.toggle("is-collapsed", collapse);
  dirBtn.setAttribute("aria-expanded", collapse ? "false" : "true");
  const chev = dirBtn.querySelector(".file-chevron");
  if (chev) chev.textContent = collapse ? "▸" : "▾";
  setFileTreeChildrenHidden(dirBtn, collapse);
}

function onFileDragStart(e, path, kind) {
  if (!e.dataTransfer) return;
  const payload = JSON.stringify({ path, kind: kind || "file" });
  e.dataTransfer.setData("application/x-codeagent-path", payload);
  e.dataTransfer.setData("text/plain", path);
  e.dataTransfer.effectAllowed = "copy";
}

function addAttachedPath(path, kind) {
  const p = String(path || "").trim().replace(/\\/g, "/");
  if (!p) return;
  attachedPaths.set(p, { path: p, kind: kind === "dir" ? "dir" : "file" });
  renderAttachChips();
}

function removeAttachedPath(path) {
  attachedPaths.delete(path);
  renderAttachChips();
}

function clearAttachedPaths() {
  attachedPaths = new Map();
  renderAttachChips();
}

function renderAttachChips() {
  if (!attachChips) return;
  attachChips.innerHTML = "";
  if (!attachedPaths.size) {
    attachChips.classList.add("hidden");
    return;
  }
  attachChips.classList.remove("hidden");
  for (const item of attachedPaths.values()) {
    const chip = document.createElement("span");
    chip.className = `attach-chip${item.kind === "dir" ? " is-dir" : ""}`;
    chip.title = item.path;
    const label = document.createElement("span");
    label.className = "attach-chip-name";
    label.textContent = (item.kind === "dir" ? "📁 " : "📄 ") + item.path;
    const x = document.createElement("button");
    x.type = "button";
    x.className = "attach-chip-x";
    x.setAttribute("aria-label", `Remove ${item.path}`);
    x.textContent = "×";
    x.addEventListener("click", () => removeAttachedPath(item.path));
    chip.appendChild(label);
    chip.appendChild(x);
    attachChips.appendChild(chip);
  }
}

function buildTaskWithAttachments(userText) {
  const text = String(userText || "").trim();
  if (!attachedPaths.size) return text;
  const lines = ["请重点关注以下附件（相对 workdir；请先 read_file / list_dir 了解内容）："];
  for (const item of attachedPaths.values()) {
    lines.push(`- ${item.kind === "dir" ? "[dir] " : ""}${item.path}`);
  }
  if (text) {
    lines.push("");
    lines.push("用户需求：");
    lines.push(text);
  } else {
    lines.push("");
    lines.push("请根据上述附件理解上下文并等待进一步指示；若需求已隐含在文件中，请合理处理。");
  }
  return lines.join("\n");
}

function setupComposerDrop() {
  const targets = [composer, composerWrap, attachChips].filter(Boolean);
  let dragDepth = 0;

  const setDropHighlight = (on) => {
    if (composer) composer.classList.toggle("is-drop-target", on);
  };

  targets.forEach((el) => {
    el.addEventListener("dragenter", (e) => {
      if (!e.dataTransfer) return;
      e.preventDefault();
      dragDepth += 1;
      setDropHighlight(true);
    });
    el.addEventListener("dragover", (e) => {
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
      setDropHighlight(true);
    });
    el.addEventListener("dragleave", () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) setDropHighlight(false);
    });
    el.addEventListener("drop", (e) => {
      e.preventDefault();
      dragDepth = 0;
      setDropHighlight(false);
      let path = "";
      let kind = "file";
      const raw = e.dataTransfer?.getData("application/x-codeagent-path");
      if (raw) {
        try {
          const parsed = JSON.parse(raw);
          path = parsed.path || "";
          kind = parsed.kind || "file";
        } catch (_) {
          path = raw;
        }
      }
      if (!path) path = e.dataTransfer?.getData("text/plain") || "";
      if (path) {
        addAttachedPath(path, kind);
        chatInput?.focus();
      }
    });
  });
}

function openFileWindow(relPath) {
  const wd = workdirInput.value.trim() || "demos";
  fetch(`/api/fs/file?workdir=${encodeURIComponent(wd)}&path=${encodeURIComponent(relPath)}`)
    .then(async (res) => {
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.status);
      return openCodeViewer(data.path || relPath, data.content || "", { modeBadge: "Preview" });
    })
    .catch((err) => alert("Open file failed: " + err));
}

async function resetDemos() {
  try {
    const res = await fetch("/api/demos/reset", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.status);
    alert("Demos reset:\n" + (data.reset || []).join("\n"));
    loadFileTree();
  } catch (err) {
    alert("Reset failed: " + err);
  }
}

async function startRun(taskText) {
  const typed = (taskText || chatInput.value || "").trim();
  const task = buildTaskWithAttachments(typed);
  if (!task || running) return;

  // Clear composer immediately so sent text / chips do not linger
  chatInput.value = "";
  clearAttachedPaths();

  running = true;
  sendBtn.disabled = true;
  setStatus("running", "运行中…");
  showChat();

  // Same session: keep timeline (multi-turn conversation = one history).
  // New session: clear and mint session id.
  if (!sessionActive || !sessionId) {
    resetSessionUI();
    sessionId = newSessionId();
    sessionActive = true;
  } else {
    // Continuing: keep DOM history; offset step keys for the new turn
    if (activeStep != null) {
      const card = stepCards.get(activeStep);
      if (card) collapseStepCard(card);
    }
    stepKeyBase += 1000;
    activeStep = null;
    // Only show files changed in this turn
    if (changedBar && changedBar.parentElement === timeline) {
      chatView.appendChild(changedBar);
    }
    changedFiles = new Map();
    renderChangedBar();
    // New turn: clear previous turn's ring until this turn's usage arrives
    resetContextMeter();
  }

  addUserBubble(task);

  const payload = {
    task,
    workdir: workdirInput.value.trim() || "demos",
    max_steps: Number(maxStepsInput.value) || 30,
    session_id: sessionId,
  };

  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      addInfoBubble(err.detail || `HTTP ${res.status}`);
      setStatus("err", "已阻塞");
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const data = JSON.parse(line.slice(6));
        handleEvent(data);
      }
    }
  } catch (err) {
    addInfoBubble(String(err));
    setStatus("err", "错误");
  } finally {
    hideApprovalBar();
    running = false;
    sendBtn.disabled = false;
    if (runStatus.textContent === "运行中…" || runStatus.textContent === "等待授权…") {
      setStatus("idle", "空闲");
    }
    loadHistory();
    loadFileTree();
  }
}

/* —— Open folder modal —— */
async function openFolderModal() {
  const start = workdirInput.value.trim() || projectRoot || "demos";
  folderPathInput.value = start;
  folderModal.classList.remove("hidden");
  await browseFolder(start);
}

async function browseFolder(path) {
  try {
    const res = await fetch(`/api/fs/list?path=${encodeURIComponent(path || "")}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.status);
    folderPathInput.value = data.path;
    folderListing.innerHTML = "";
    if (data.parent) {
      const up = document.createElement("button");
      up.type = "button";
      up.className = "folder-entry is-dir";
      up.innerHTML = `<span>⬆</span><span>..</span>`;
      up.addEventListener("click", () => browseFolder(data.parent));
      up.addEventListener("dblclick", () => browseFolder(data.parent));
      folderListing.appendChild(up);
    }
    (data.entries || []).forEach((ent) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `folder-entry${ent.kind === "dir" ? " is-dir" : ""}`;
      btn.innerHTML = `<span>${ent.kind === "dir" ? "📁" : "📄"}</span><span>${escapeHtml(ent.name)}</span>`;
      if (ent.kind === "dir") {
        btn.addEventListener("dblclick", () => browseFolder(ent.path));
        btn.addEventListener("click", () => {
          folderPathInput.value = ent.path;
        });
      }
      folderListing.appendChild(btn);
    });
  } catch (err) {
    folderListing.innerHTML = `<div class="todo-empty">${escapeHtml(String(err))}</div>`;
  }
}

async function selectFolder() {
  const path = folderPathInput.value.trim();
  if (!path) return;
  try {
    const res = await fetch("/api/workdir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.status);
    workdirInput.value = data.workdir;
    workdirInput.dataset.touched = "1";
    folderModal.classList.add("hidden");
    loadFileTree();
    switchRightTab("files");
    workspace.classList.remove("right-collapsed");
  } catch (err) {
    alert("Open folder failed: " + err);
  }
}

function switchRightTab(name) {
  document.querySelectorAll(".rp-tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === name);
  });
  filesPane.classList.toggle("hidden", name !== "files");
  planPane.classList.toggle("hidden", name !== "plan");
}

function closeModal(which) {
  if (which === "folder") folderModal.classList.add("hidden");
  if (which === "code" || which === "diff") {
    codeModal.classList.add("hidden");
    disposeMonacoEditors();
  }
}

composer.addEventListener("submit", (e) => {
  e.preventDefault();
  startRun();
});

if (contextRing) {
  contextRing.addEventListener("click", (e) => {
    e.preventDefault();
    toggleContextRingDetail();
  });
}

workdirInput.addEventListener("change", () => {
  workdirInput.dataset.touched = "1";
  loadFileTree();
});
workdirInput.addEventListener("input", () => {
  workdirInput.dataset.touched = "1";
});

document.querySelector('.nav-item[data-view="home"]').addEventListener("click", () => {
  showHome();
});

document.getElementById("btnOpenFolder").addEventListener("click", openFolderModal);
document.getElementById("btnFolderGo").addEventListener("click", () => browseFolder(folderPathInput.value.trim()));
document.getElementById("btnFolderSelect").addEventListener("click", selectFolder);
document.getElementById("btnToggleRight").addEventListener("click", () => {
  workspace.classList.toggle("right-collapsed");
});

if (btnDiffUndo) {
  btnDiffUndo.addEventListener("click", () => applyDiffToDisk("original"));
}
if (btnApprovalAllow) {
  btnApprovalAllow.addEventListener("click", () => respondApproval(true));
}
if (btnApprovalDeny) {
  btnApprovalDeny.addEventListener("click", () => respondApproval(false));
}
if (btnDiffRedo) {
  btnDiffRedo.addEventListener("click", () => applyDiffToDisk("modified"));
}

document.querySelectorAll(".rp-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchRightTab(tab.dataset.tab));
});

document.querySelectorAll("[data-close]").forEach((el) => {
  el.addEventListener("click", () => closeModal(el.getAttribute("data-close")));
});

folderPathInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    browseFolder(folderPathInput.value.trim());
  }
});

historySearch.addEventListener("input", () => renderHistory(historyCache));

setupComposerDrop();
loadMeta();
loadHistory();
resetContextMeter();
chatInput.focus();
