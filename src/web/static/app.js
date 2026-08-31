const homeView = document.getElementById("homeView");
const chatView = document.getElementById("chatView");
const timeline = document.getElementById("timeline");
const composer = document.getElementById("composer");
const chatInput = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");
const stopBtn = document.getElementById("stopBtn");
const workdirInput = document.getElementById("workdirInput");
const maxStepsInput = document.getElementById("maxStepsInput");
const suggestionCards = document.getElementById("suggestionCards");
const recentList = document.getElementById("recentList");
const historySearch = document.getElementById("historySearch");
const todoList = document.getElementById("todoList");
const todoEmpty = document.getElementById("todoEmpty");
const runStatus = document.getElementById("runStatus");
const costPanel = document.getElementById("costPanel");
const costSteps = document.getElementById("costSteps");
const costTokens = document.getElementById("costTokens");
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
const stylesPane = document.getElementById("stylesPane");
const stylesList = document.getElementById("stylesList");
const stylesEmpty = document.getElementById("stylesEmpty");
const styleModal = document.getElementById("styleModal");
const styleModalTitle = document.getElementById("styleModalTitle");
const styleIdInput = document.getElementById("styleIdInput");
const styleTitleInput = document.getElementById("styleTitleInput");
const styleDescInput = document.getElementById("styleDescInput");
const styleKindInput = document.getElementById("styleKindInput");
const styleBodyInput = document.getElementById("styleBodyInput");
const btnStyleSave = document.getElementById("btnStyleSave");
const btnStyleDelete = document.getElementById("btnStyleDelete");
const btnStyleNew = document.getElementById("btnStyleNew");
const btnStyleRefresh = document.getElementById("btnStyleRefresh");
const capsPane = document.getElementById("capsPane");
const capsLoading = document.getElementById("capsLoading");
const capsContent = document.getElementById("capsContent");
const capsPolicies = document.getElementById("capsPolicies");
const capsToolBody = document.getElementById("capsToolBody");
const capsBoundaries = document.getElementById("capsBoundaries");
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
const askUserBar = document.getElementById("askUserBar");
const askUserQuestion = document.getElementById("askUserQuestion");
const askUserInput = document.getElementById("askUserInput");
const btnAskUserReply = document.getElementById("btnAskUserReply");

let running = false;
/** @type {Set<string>} */
let selectedStyleIds = new Set();
let styleEditorMode = "create"; // create | edit
let styleCardsCache = [];
let pendingApprovalId = null;
let pendingApprovalCallId = null;
let pendingAskUserId = null;
let pendingAskUserCallId = null;
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
/** styleId -> { id, name, kind: "style", styleKind } — dragged style cards */
let attachedStyles = new Map();
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

/** Per-turn cost panel (steps + rough tokens + tool attribution). */
let turnCost = {
  step: 0,
  maxSteps: 0,
  usedTokens: null,
  budgetTokens: null,
  peakTokens: 0,
  level: "ok",
  taskTokens: null,
  maxTaskTokens: null,
  toolCounts: null,
  costSummary: null,
};

function formatTok(n) {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1000) return `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k`;
  return String(Math.round(v));
}

function formatToolCounts(counts) {
  if (!counts || typeof counts !== "object") return "";
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!entries.length) return "";
  return entries
    .slice(0, 4)
    .map(([n, c]) => `${n}×${c}`)
    .join(", ");
}

function renderCostPanel() {
  if (costSteps) {
    const cur = turnCost.step > 0 ? turnCost.step : "—";
    const max = turnCost.maxSteps > 0 ? turnCost.maxSteps : "—";
    costSteps.textContent = `${cur} / ${max} 步`;
  }
  if (costTokens) {
    if (turnCost.maxTaskTokens != null && turnCost.maxTaskTokens > 0) {
      const used = formatTok(turnCost.taskTokens ?? turnCost.usedTokens);
      const budget = formatTok(turnCost.maxTaskTokens);
      costTokens.textContent = `任务 ≈ ${used} / ${budget} tok`;
    } else if (turnCost.taskTokens != null) {
      costTokens.textContent = `任务 ≈ ${formatTok(turnCost.taskTokens)} tok`;
    } else {
      const used = formatTok(turnCost.usedTokens);
      const budget = formatTok(turnCost.budgetTokens);
      if (turnCost.usedTokens == null && turnCost.budgetTokens == null) {
        costTokens.textContent = "≈ — tok";
      } else if (turnCost.budgetTokens != null) {
        costTokens.textContent = `窗 ≈ ${used} / ${budget} tok`;
      } else {
        costTokens.textContent = `≈ ${used} tok`;
      }
    }
  }
  if (costPanel) {
    costPanel.dataset.level = turnCost.level || "ok";
    const peak =
      turnCost.peakTokens > 0 ? ` · 窗峰值 ${formatTok(turnCost.peakTokens)}` : "";
    const tools = formatToolCounts(turnCost.toolCounts);
    const toolBit = tools ? ` · 工具 ${tools}` : "";
    costPanel.title =
      (turnCost.maxTaskTokens > 0
        ? "本轮步数 + 任务累计 token 硬闸粗估（非 API 账单）"
        : "本轮步数 + token 粗估（任务硬闸关闭时显示上下文窗）") +
      peak +
      toolBit;
  }
}

function resetCostPanel(maxSteps) {
  turnCost = {
    step: 0,
    maxSteps: maxSteps != null ? Number(maxSteps) || 0 : 0,
    usedTokens: null,
    budgetTokens: null,
    peakTokens: 0,
    level: "ok",
    taskTokens: null,
    maxTaskTokens: null,
    toolCounts: null,
    costSummary: null,
  };
  renderCostPanel();
}

function noteCostStep(step, maxSteps) {
  if (step != null) turnCost.step = Number(step) || 0;
  if (maxSteps != null) turnCost.maxSteps = Number(maxSteps) || turnCost.maxSteps;
  renderCostPanel();
}

function noteCostUsage(data) {
  if (!data) return;
  if (data.used_tokens != null) {
    const used = Number(data.used_tokens);
    if (Number.isFinite(used)) {
      turnCost.usedTokens = used;
      if (used > turnCost.peakTokens) turnCost.peakTokens = used;
    }
  }
  if (data.budget_tokens != null) {
    const budget = Number(data.budget_tokens);
    if (Number.isFinite(budget)) turnCost.budgetTokens = budget;
  }
  if (data.level) turnCost.level = data.level;
  renderCostPanel();
}

function noteTaskBudget(data) {
  if (!data) return;
  if (data.tokens_used != null || data.tokens_total_est != null) {
    const used = Number(data.tokens_used ?? data.tokens_total_est);
    if (Number.isFinite(used)) turnCost.taskTokens = used;
  }
  if (data.max_task_tokens != null) {
    const cap = Number(data.max_task_tokens);
    if (Number.isFinite(cap)) turnCost.maxTaskTokens = cap;
  }
  if (data.tool_counts && typeof data.tool_counts === "object") {
    turnCost.toolCounts = data.tool_counts;
  }
  if (data.level) turnCost.level = data.level;
  if (data.peak_context_tokens != null) {
    const peak = Number(data.peak_context_tokens);
    if (Number.isFinite(peak) && peak > turnCost.peakTokens) turnCost.peakTokens = peak;
  }
  if (data.step != null) noteCostStep(data.step, data.max_steps);
  else renderCostPanel();
}

function noteCostReport(data) {
  if (!data) return;
  noteTaskBudget(data);
  if (data.summary) turnCost.costSummary = data.summary;
  if (data.steps != null) noteCostStep(data.steps, data.max_steps);
  renderCostPanel();
}

function costSummaryMeta() {
  const parts = [];
  if (turnCost.costSummary) {
    return turnCost.costSummary;
  }
  if (turnCost.step > 0) {
    parts.push(
      turnCost.maxSteps
        ? `${turnCost.step}/${turnCost.maxSteps} 步`
        : `${turnCost.step} 步`
    );
  }
  if (turnCost.taskTokens != null) {
    if (turnCost.maxTaskTokens > 0) {
      parts.push(
        `任务 ≈ ${formatTok(turnCost.taskTokens)}/${formatTok(turnCost.maxTaskTokens)} tok`
      );
    } else {
      parts.push(`任务 ≈ ${formatTok(turnCost.taskTokens)} tok`);
    }
  } else if (turnCost.peakTokens > 0) {
    parts.push(`窗峰值 ≈ ${formatTok(turnCost.peakTokens)} tok`);
  } else if (turnCost.usedTokens != null) {
    parts.push(`≈ ${formatTok(turnCost.usedTokens)} tok`);
  }
  const tools = formatToolCounts(turnCost.toolCounts);
  if (tools) parts.push(`工具 ${tools}`);
  return parts.join(" · ");
}

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

function hideAskUserBar() {
  pendingAskUserId = null;
  pendingAskUserCallId = null;
  if (askUserBar) askUserBar.classList.add("hidden");
  if (askUserInput) askUserInput.value = "";
  if (btnAskUserReply) btnAskUserReply.disabled = false;
}

function showAskUserRequest(data) {
  pendingAskUserId = data.request_id;
  pendingAskUserCallId = data.call_id || null;
  if (askUserQuestion) askUserQuestion.textContent = data.question || "";
  if (askUserInput) {
    askUserInput.value = "";
    askUserInput.disabled = false;
  }
  if (askUserBar) askUserBar.classList.remove("hidden");
  if (btnAskUserReply) btnAskUserReply.disabled = false;
  markToolAwaiting(pendingAskUserCallId, true);
  setStatus("running", "等待你的回答…");
  askUserBar?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  askUserInput?.focus();
}

async function respondAskUser() {
  if (!pendingAskUserId || !running || !askUserInput) return;
  const answer = askUserInput.value.trim();
  if (!answer) {
    askUserInput.focus();
    return;
  }
  const requestId = pendingAskUserId;
  if (btnAskUserReply) btnAskUserReply.disabled = true;
  if (askUserInput) askUserInput.disabled = true;
  try {
    const res = await fetch("/api/ask_reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id: requestId, answer }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    setStatus("running", "已收到回答，继续执行…");
  } catch (err) {
    addInfoBubble(`回答提交失败：${err}`);
    if (btnAskUserReply) btnAskUserReply.disabled = false;
    if (askUserInput) askUserInput.disabled = false;
  }
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
  if (approvalTool) {
    approvalTool.textContent = data.tool_name || "tool";
  }
  if (approvalSummary) {
    const bits = [data.summary || ""];
    if (risk === "high") {
      bits.push("（High：安装/网络/破坏性操作 — 确认后再允许）");
    }
    approvalSummary.textContent = bits.filter(Boolean).join("\n");
  }
  if (approvalBar) {
    approvalBar.classList.toggle("is-high", risk === "high");
    approvalBar.classList.remove("hidden");
  }
  if (btnApprovalAllow) btnApprovalAllow.disabled = false;
  if (btnApprovalDeny) btnApprovalDeny.disabled = false;
  markToolAwaiting(pendingApprovalCallId, true);
  setStatus("running", risk === "high" ? "等待 High 授权…" : "等待授权…");
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

function updateContextMeter(data, opts) {
  if (!contextRing || !data) return;
  const syncCost = !opts || opts.syncCost !== false;
  lastContextUsage = data;
  if (syncCost) noteCostUsage(data);
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
  updateContextMeter(
    {
      remaining_pct: 100,
      used_pct: 0,
      level: "ok",
      scope: "turn",
      hint: "发送任务后显示用量",
      used_tokens: 0,
      budget_tokens: null,
    },
    { syncCost: false }
  );
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
  resetCostPanel(Number(maxStepsInput && maxStepsInput.value) || 30);
  resetContextMeter();
  clearAttachedPaths();
  hideApprovalBar();
  hideAskUserBar();
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
    interrupted: "已按你的要求停止",
    loop_detected: "检测到重复操作，已停止",
    cycle_detected: "检测到交替转圈，已停止",
    stagnation_detected: "检测到输出停滞，已停止",
    retry_exhausted: "多次失败后已停止",
    goal_met_forced: "测试已通过，已自动结束",
    budget_exhausted: "任务 token 预算耗尽，已停止",
  };
  return map[reason] || reason || "";
}

function setRunControls(isRunning) {
  running = isRunning;
  if (sendBtn) {
    // Only hide Send while running — do NOT disable it.
    // A disabled submit button blocks Enter from submitting the form,
    // which broke mid-run steer.
    sendBtn.disabled = false;
    sendBtn.classList.toggle("hidden", isRunning);
    sendBtn.title = "发送";
    sendBtn.setAttribute("aria-label", "Send");
  }
  if (stopBtn) {
    stopBtn.classList.toggle("hidden", !isRunning);
    stopBtn.disabled = false;
  }
  if (chatInput) {
    chatInput.placeholder = isRunning
      ? "运行中可插话纠偏（回车发送），例如：不要改别的文件，只修测试…"
      : "Ask something… 可拖入右侧文件或 Styles 卡片";
  }
}

async function requestStop() {
  if (!running || !stopBtn) return;
  stopBtn.disabled = true;
  setStatus("running", "正在停止…");
  try {
    const res = await fetch("/api/stop", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      addInfoBubble(data.detail || `停止失败：HTTP ${res.status}`);
      stopBtn.disabled = false;
      setStatus("running", "运行中…");
    }
  } catch (err) {
    addInfoBubble(String(err));
    stopBtn.disabled = false;
    setStatus("running", "运行中…");
  }
}

async function sendSteer(taskText) {
  const typed = (taskText || chatInput.value || "").trim();
  const task = buildTaskWithAttachments(typed);
  if (!task) return false;
  if (!running) {
    addInfoBubble("当前没有运行中的任务，无法纠偏。请先发送任务。");
    return false;
  }

  chatInput.value = "";
  clearAttachedPaths();
  addUserBubble(`纠偏：${task}`);
  setStatus("running", "已插入纠偏，下一步生效…");

  try {
    const res = await fetch("/api/steer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: task }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail =
        typeof data.detail === "string"
          ? data.detail
          : data.detail
            ? JSON.stringify(data.detail)
            : `纠偏失败：HTTP ${res.status}`;
      addInfoBubble(
        res.status === 409
          ? "纠偏失败：没有进行中的任务（可能已结束或页面已刷新）。请重新发送任务。"
          : detail
      );
      setStatus("running", "运行中…");
      return false;
    }
    return true;
  } catch (err) {
    addInfoBubble(String(err));
    setStatus("running", "运行中…");
    return false;
  }
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

/** Guardrail / policy bubble (completion evidence, deny, soft-dedup). */
function addGuardBubble(tag, text, kind = "guard") {
  const row = document.createElement("div");
  row.className = "bubble-row agent";
  const msg = document.createElement("div");
  msg.className = `msg info guard-bubble guard-${kind}`;
  msg.innerHTML =
    `<div class="tag">${escapeHtml(tag)}</div>` +
    `<div class="rich">${renderRich(String(text || ""))}</div>`;
  row.appendChild(msg);
  timeline.appendChild(row);
  scrollChat();
}

function formatCompletionGateBubble(data) {
  const reason = String(data.reason || "");
  const text = String(data.text || "").trim();
  if (reason === "fake_green" || reason === "fake_green_warn" || text.includes("[fake_green]")) {
    return {
      tag: "FAKE_GREEN",
      kind: "fake",
      body:
        text ||
        "假绿拦截：仅改了测试文件却测绿，请先改源文件再验证。",
    };
  }
  if (data.blocked) {
    return {
      tag: "EVIDENCE",
      kind: "evidence",
      body:
        text ||
        `完成被拒（${reason || "missing evidence"}）：请先跑测试 / 补齐 Mustlist 证据。` +
          (data.nudge != null ? `（催促 ${data.nudge}/${data.max_nudges || "?"}）` : ""),
    };
  }
  return {
    tag: "EVIDENCE",
    kind: "warn",
    body: text || `完成闸放行：${reason || "ok"}`,
  };
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
      if (data.approval === "ask") {
        addGuardBubble(
          "POLICY",
          "Web 审批：**Medium/High** 工具需点允许；`.env` / 密钥等敏感路径与 hard-deny **始终拒绝**；`pip`/`curl` 等为 High。",
          "policy"
        );
      }
      break;
    case "run_start":
      resetCostPanel(data.max_steps || Number(maxStepsInput && maxStepsInput.value) || 30);
      if (data.context_token_budget != null) {
        noteCostUsage({
          budget_tokens: data.context_token_budget,
          used_tokens: turnCost.usedTokens,
          level: "ok",
        });
      }
      break;
    case "skill_loaded":
      {
        const via = data.via === "keyword_router" ? "关键词预注入" : "load_skill";
        const hits = Array.isArray(data.matched) && data.matched.length
          ? ` · hits: ${data.matched.slice(0, 4).join(", ")}`
          : "";
        const score = data.score != null ? ` · score=${data.score}` : "";
        addInfoBubble(`Skill · **${data.name || "?"}**（${via}${score}${hits}）`);
      }
      break;
    case "steer":
      addInfoBubble(
        `纠偏已生效（第 ${data.step || "?"} 步）：${escapeHtml(data.text || "")}`
      );
      setStatus("running", "运行中…");
      break;
    case "step_start":
      noteCostStep(data.step, data.max_steps);
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
      addGuardBubble(
        "APPROVAL",
        `等待授权 · **${data.risk_level || "medium"}** · \`${data.tool_name || "tool"}\`\n` +
          `${data.summary || ""}`,
        (data.risk_level || "").toLowerCase() === "high" ? "high" : "policy"
      );
      break;
    case "approval_resolved":
      markToolAwaiting(data.call_id || pendingApprovalCallId, false);
      if (!data.request_id || data.request_id === pendingApprovalId) {
        hideApprovalBar();
      }
      if (data.allowed === false || data.reason === "cancelled") {
        addGuardBubble(
          "APPROVAL",
          data.reason === "cancelled"
            ? "授权已取消（任务停止）"
            : "你已拒绝该工具调用，Agent 将收到拒绝结果并继续。",
          "deny"
        );
      } else if (data.allowed) {
        addGuardBubble("APPROVAL", "已允许，继续执行。", "ok");
      }
      if (running) setStatus("running", "运行中…");
      break;
    case "ask_user_request":
      showAskUserRequest(data);
      addGuardBubble(
        "ASK",
        `Agent 提问：\n\n${data.question || ""}`,
        "policy"
      );
      break;
    case "ask_user_resolved":
      markToolAwaiting(data.call_id || pendingAskUserCallId, false);
      if (!data.request_id || data.request_id === pendingAskUserId) {
        hideAskUserBar();
      }
      if (data.answered) {
        addGuardBubble("ASK", "你已回答，Agent 将继续。", "ok");
      } else if (data.reason === "cancelled") {
        addGuardBubble("ASK", "提问已取消（任务停止）。", "deny");
      } else {
        addGuardBubble("ASK", "未收到有效回答（超时或空回复）。", "deny");
      }
      if (running) setStatus("running", "运行中…");
      break;
    case "ask_user":
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
        if (!data.allowed) {
          const risk = data.risk_level ? ` · ${data.risk_level}` : "";
          addGuardBubble(
            "DENY",
            `权限门拒绝 \`${data.name || "tool"}\`${risk}\n${data.reason || ""}`,
            "deny"
          );
        }
      }
      break;
    case "completion_gate":
      {
        const g = formatCompletionGateBubble(data);
        addGuardBubble(g.tag, g.body, g.kind);
        setStatus("running", data.blocked ? "证据不足，继续…" : "运行中…");
      }
      break;
    case "soft_dedup":
      addGuardBubble(
        "DEDUP",
        data.text ||
          `[soft-dedup] 文件未变，复用上次读取（${data.path || "?"}）`,
        "dedup"
      );
      break;
    case "action_dedup":
      addGuardBubble(
        "DEDUP",
        `同一步内重复调用 \`${data.name || "tool"}\`，复用结果。`,
        "dedup"
      );
      break;
    case "strategy_blocked":
      addGuardBubble(
        "BLOCK",
        `策略已耗尽并硬拦截：\`${data.name || "tool"}\`（勿重复同一失败 fingerprint）。`,
        "deny"
      );
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
    case "task_budget":
      noteTaskBudget(data);
      break;
    case "cost_report":
      noteCostReport(data);
      break;
    case "budget_warn":
      addInfoBubble(data.text || "预算偏低，请收束探索。");
      break;
    case "budget_exhausted":
      noteTaskBudget(data);
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
      hideAskUserBar();
      if (data.cost_report) noteCostReport(data.cost_report);
      if (data.steps != null) noteCostStep(data.steps, turnCost.maxSteps);
      {
        const costBit = costSummaryMeta();
        addFinalBubble(
          data.final_text || "",
          `${formatStoppedReason(data.stopped_reason)}` +
            (costBit ? ` · ${costBit}` : ` · ${data.steps} 步`) +
            (data.transcript_id ? ` · ${data.transcript_id}` : "")
        );
      }
      if (data.stopped_reason === "interrupted") {
        setStatus("idle", "已停止");
      } else {
        setStatus("idle", "完成");
      }
      sessionActive = true;
      break;
    case "error":
      hideApprovalBar();
      hideAskUserBar();
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
  // If server still holds a run lock (e.g. after refresh), show Stop
  try {
    const h = await fetch("/api/health");
    const health = await h.json();
    if (health && health.busy && !running) {
      setRunControls(true);
      setStatus("running", "后台仍有任务在跑…");
      addInfoBubble("检测到未结束的上一轮任务。可点红色「停止」后再发新任务。");
    }
  } catch (_) {
    /* ignore */
  }
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

function addAttachedStyle(card) {
  if (!card || !card.id) return;
  attachedStyles.set(card.id, {
    id: card.id,
    name: card.name || card.id,
    kind: "style",
    styleKind: card.kind || "writing",
  });
  selectedStyleIds.add(card.id);
  persistSelectedStyles();
  renderAttachChips();
  renderStylesList();
}

function removeAttachedStyle(id) {
  attachedStyles.delete(id);
  renderAttachChips();
}

function clearAttachedPaths() {
  attachedPaths = new Map();
  attachedStyles = new Map();
  renderAttachChips();
}

function renderAttachChips() {
  if (!attachChips) return;
  attachChips.innerHTML = "";
  if (!attachedPaths.size && !attachedStyles.size) {
    attachChips.classList.add("hidden");
    return;
  }
  attachChips.classList.remove("hidden");
  for (const item of attachedStyles.values()) {
    const chip = document.createElement("span");
    chip.className = "attach-chip is-style";
    chip.title = `style:${item.id}`;
    const label = document.createElement("span");
    label.className = "attach-chip-name";
    const badge = item.styleKind === "code" ? "💻" : "🎨";
    label.textContent = `${badge} ${item.name} (${item.id})`;
    const x = document.createElement("button");
    x.type = "button";
    x.className = "attach-chip-x";
    x.setAttribute("aria-label", `Remove style ${item.id}`);
    x.textContent = "×";
    x.addEventListener("click", () => removeAttachedStyle(item.id));
    chip.appendChild(label);
    chip.appendChild(x);
    attachChips.appendChild(chip);
  }
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
  const hasFiles = attachedPaths.size > 0;
  const hasStyles = attachedStyles.size > 0;
  if (!hasFiles && !hasStyles) return text;

  const lines = [];
  if (hasStyles) {
    lines.push("本回合请遵循以下风格卡片（已附加；写代码/文案时对齐）：");
    for (const s of attachedStyles.values()) {
      lines.push(`- [style:${s.styleKind}] ${s.id} — ${s.name}`);
    }
  }
  if (hasFiles) {
    if (lines.length) lines.push("");
    lines.push("请重点关注以下附件（相对 workdir；请先 read_file / list_dir 了解内容）：");
    for (const item of attachedPaths.values()) {
      lines.push(`- ${item.kind === "dir" ? "[dir] " : ""}${item.path}`);
    }
  }
  if (text) {
    lines.push("");
    lines.push("用户需求：");
    lines.push(text);
  } else {
    lines.push("");
    lines.push(
      hasStyles && !hasFiles
        ? "请按上述风格等待具体改写/编码指示；若需求已隐含，请合理处理。"
        : "请根据上述附件理解上下文并等待进一步指示；若需求已隐含在文件中，请合理处理。"
    );
  }
  return lines.join("\n");
}

function collectStyleIdsForRun() {
  const ids = new Set(selectedStyleIds);
  for (const id of attachedStyles.keys()) ids.add(id);
  return Array.from(ids);
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

      const styleRaw = e.dataTransfer?.getData("application/x-codeagent-style");
      if (styleRaw) {
        try {
          const parsed = JSON.parse(styleRaw);
          if (parsed && parsed.id) {
            addAttachedStyle(parsed);
            chatInput?.focus();
            return;
          }
        } catch (_) {
          /* fall through */
        }
      }

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
      // Ignore plain style:id text if somehow set
      if (path && !path.startsWith("style:")) {
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
  if (!task) return;

  // Running → mid-flight steer instead of starting a second run
  if (running) {
    await sendSteer(task);
    return;
  }

  // Clear composer immediately so sent text / chips do not linger
  chatInput.value = "";
  clearAttachedPaths();

  setRunControls(true);
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
    resetCostPanel(Number(maxStepsInput.value) || 30);
    resetContextMeter();
  }

  addUserBubble(task);

  const payload = {
    task,
    workdir: workdirInput.value.trim() || "demos",
    max_steps: Number(maxStepsInput.value) || 30,
    session_id: sessionId,
    style_ids: collectStyleIdsForRun(),
  };

  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const detail = formatApiDetail(err, res);
      if (res.status === 409) {
        addInfoBubble(
          detail ||
            "上一轮任务仍在执行。请先点红色「停止」，或等本轮结束后再发。"
        );
        // Page may have been refreshed while a run was active — show Stop / try clear
        setRunControls(true);
        setStatus("running", "检测到未结束任务…");
        try {
          await fetch("/api/stop", { method: "POST" });
          addInfoBubble("已请求停止上一轮。请再发一次任务。");
        } catch (_) {
          /* ignore */
        }
        setStatus("err", "请重试");
      } else {
        addInfoBubble(detail || `HTTP ${res.status}`);
        setStatus("err", "已阻塞");
      }
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
    addInfoBubble(formatRunNetworkError(err));
    setStatus("err", "错误");
  } finally {
    hideApprovalBar();
    hideAskUserBar();
    setRunControls(false);
    if (
      runStatus.textContent === "运行中…" ||
      runStatus.textContent === "等待授权…" ||
      runStatus.textContent === "正在停止…"
    ) {
      setStatus("idle", "空闲");
    }
    loadHistory();
    loadFileTree();
    loadStyles();
  }
}

/** Map opaque browser fetch failures to an actionable Chinese message. */
function formatRunNetworkError(err) {
  const raw = String(err && err.message != null ? err.message : err || "");
  const lower = raw.toLowerCase();
  const looksNetwork =
    err instanceof TypeError ||
    /network\s*error|failed to fetch|fetch failed|networkerror|load failed/.test(
      lower
    );
  if (looksNetwork) {
    return (
      "网络中断：与本机 Web 服务的流式连接断开（常见原因：DeepSeek/API 超时、代理/VPN、服务重启、或长任务被中间设备掐断）。" +
      "请确认 uvicorn 仍在运行、.env 的 BASE_URL/API key 可用，然后点「停止」后再重试。" +
      (raw ? `（原始：${raw}）` : "")
    );
  }
  return raw || String(err);
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
    loadStyles();
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
  if (stylesPane) stylesPane.classList.toggle("hidden", name !== "styles");
  capsPane.classList.toggle("hidden", name !== "caps");
  if (name === "caps") loadCapabilities();
  if (name === "styles") loadStyles();
}

function styleStorageKey() {
  const wd = (workdirInput && workdirInput.value.trim()) || "demos";
  return `codeagent.activeStyles:${wd}`;
}

function restoreSelectedStyles() {
  selectedStyleIds = new Set();
  try {
    const raw = localStorage.getItem(styleStorageKey());
    if (!raw) return;
    const arr = JSON.parse(raw);
    if (Array.isArray(arr)) arr.forEach((id) => selectedStyleIds.add(String(id)));
  } catch (_) {
    /* ignore */
  }
}

function persistSelectedStyles() {
  try {
    localStorage.setItem(
      styleStorageKey(),
      JSON.stringify(Array.from(selectedStyleIds))
    );
  } catch (_) {
    /* ignore */
  }
}

async function loadStyles() {
  if (!stylesList) return;
  const wd = (workdirInput && workdirInput.value.trim()) || "demos";
  restoreSelectedStyles();
  try {
    const res = await fetch(`/api/styles?workdir=${encodeURIComponent(wd)}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    styleCardsCache = data.items || [];
    // Drop selections that no longer exist
    const valid = new Set(styleCardsCache.map((c) => c.id));
    selectedStyleIds = new Set(
      [...selectedStyleIds].filter((id) => valid.has(id))
    );
    persistSelectedStyles();
    renderStylesList();
  } catch (err) {
    stylesList.innerHTML = `<div class="styles-empty">加载失败：${escHtml(
      err.message || err
    )}</div>`;
    if (stylesEmpty) stylesEmpty.classList.add("hidden");
  }
}

function renderStylesList() {
  if (!stylesList) return;
  if (!styleCardsCache.length) {
    stylesList.innerHTML = "";
    if (stylesEmpty) stylesEmpty.classList.remove("hidden");
    return;
  }
  if (stylesEmpty) stylesEmpty.classList.add("hidden");
  stylesList.innerHTML = styleCardsCache
    .map((c) => {
      const checked = selectedStyleIds.has(c.id) ? "checked" : "";
      const kindLabel = c.kind || "writing";
      return `<div class="style-card-row" data-id="${escHtml(c.id)}" draggable="true" title="拖到输入栏附加此风格">
        <label class="style-check">
          <input type="checkbox" data-style-toggle="${escHtml(c.id)}" ${checked} />
          <span class="style-check-mark"></span>
        </label>
        <button type="button" class="style-card-main" data-style-edit="${escHtml(c.id)}">
          <span class="style-card-title">${escHtml(c.name || c.id)} <em class="style-kind-tag">${escHtml(kindLabel)}</em></span>
          <span class="style-card-id">${escHtml(c.id)} · 可拖到输入栏</span>
          <span class="style-card-desc">${escHtml(c.description || "")}</span>
        </button>
      </div>`;
    })
    .join("");

  stylesList.querySelectorAll(".style-card-row").forEach((row) => {
    row.addEventListener("dragstart", (e) => {
      const id = row.getAttribute("data-id");
      const card = styleCardsCache.find((c) => c.id === id);
      if (!card || !e.dataTransfer) return;
      const payload = JSON.stringify({
        id: card.id,
        name: card.name,
        kind: card.kind || "writing",
      });
      e.dataTransfer.setData("application/x-codeagent-style", payload);
      e.dataTransfer.setData("text/plain", `style:${card.id}`);
      e.dataTransfer.effectAllowed = "copy";
      row.classList.add("is-dragging");
    });
    row.addEventListener("dragend", () => row.classList.remove("is-dragging"));
  });

  stylesList.querySelectorAll("[data-style-toggle]").forEach((el) => {
    el.addEventListener("change", () => {
      const id = el.getAttribute("data-style-toggle");
      if (!id) return;
      if (el.checked) selectedStyleIds.add(id);
      else selectedStyleIds.delete(id);
      persistSelectedStyles();
    });
  });
  stylesList.querySelectorAll("[data-style-edit]").forEach((el) => {
    el.addEventListener("click", () => {
      const id = el.getAttribute("data-style-edit");
      const card = styleCardsCache.find((c) => c.id === id);
      if (card) openStyleEditor(card);
    });
  });
}

function openStyleEditor(card) {
  if (!styleModal) return;
  styleEditorMode = card ? "edit" : "create";
  if (styleModalTitle) {
    styleModalTitle.textContent = card ? "编辑风格卡片" : "新建风格卡片";
  }
  if (styleIdInput) {
    styleIdInput.value = card ? card.id : "";
    styleIdInput.readOnly = !!card;
  }
  if (styleTitleInput) styleTitleInput.value = card ? card.name || "" : "";
  if (styleDescInput) styleDescInput.value = card ? card.description || "" : "";
  if (styleKindInput) styleKindInput.value = card ? card.kind || "writing" : "writing";
  if (styleBodyInput) styleBodyInput.value = card ? card.body || "" : "";
  if (btnStyleDelete) btnStyleDelete.classList.toggle("hidden", !card);
  styleModal.classList.remove("hidden");
}

async function saveStyleFromModal() {
  const wd = (workdirInput && workdirInput.value.trim()) || "demos";
  const id = (styleIdInput && styleIdInput.value.trim()) || "";
  const title = (styleTitleInput && styleTitleInput.value.trim()) || id;
  const description = (styleDescInput && styleDescInput.value.trim()) || "";
  const kind = (styleKindInput && styleKindInput.value.trim()) || "writing";
  const body = (styleBodyInput && styleBodyInput.value.trim()) || "";
  if (!id || !body) {
    alert("请填写 ID 和规则正文");
    return;
  }
  try {
    const res = await fetch("/api/styles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workdir: wd,
        id,
        title,
        description,
        body,
        kind,
        overwrite: true,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    styleModal.classList.add("hidden");
    selectedStyleIds.add(id);
    persistSelectedStyles();
    await loadStyles();
    addInfoBubble(`已保存风格卡 \`${id}\``);
  } catch (err) {
    alert("保存失败：" + (err.message || err));
  }
}

async function deleteStyleFromModal() {
  const wd = (workdirInput && workdirInput.value.trim()) || "demos";
  const id = (styleIdInput && styleIdInput.value.trim()) || "";
  if (!id) return;
  if (!confirm(`删除风格卡「${id}」？`)) return;
  try {
    const res = await fetch(
      `/api/styles/${encodeURIComponent(id)}?workdir=${encodeURIComponent(wd)}`,
      { method: "DELETE" }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    selectedStyleIds.delete(id);
    persistSelectedStyles();
    styleModal.classList.add("hidden");
    await loadStyles();
    addInfoBubble(`已删除风格卡 \`${id}\``);
  } catch (err) {
    alert("删除失败：" + (err.message || err));
  }
}

function escHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderCapabilities(data) {
  if (!capsPolicies || !capsToolBody || !capsBoundaries) return;
  const pol = data.policies || {};
  const web = data.web_defaults || {};
  const shellWarn = pol.shell_mode === "allowlist";
  const rows = [
    ["workdir", data.workdir],
    ["tools", String(data.tool_count || 0)],
    ["approval (env)", pol.approval],
    ["approval (web run)", web.approval || "ask"],
    ["network_policy", pol.network_policy],
    ["shell_mode", pol.shell_mode, shellWarn],
    ["tool_visibility", pol.tool_visibility],
    ["completion_mode", pol.completion_mode],
    ["fake_green_mode", pol.fake_green_mode],
    ["deny_high (env)", String(pol.deny_high)],
  ];
  capsPolicies.innerHTML = rows
    .map(([k, v, warn]) => {
      const cls = warn ? "cap-v warn" : "cap-v";
      return `<div class="cap-row"><span class="cap-k">${escHtml(k)}</span><span class="${cls}">${escHtml(v)}</span></div>`;
    })
    .join("");
  if (pol.shell_mode === "allowlist" && Array.isArray(pol.shell_allowlist_prefixes)) {
    capsPolicies.innerHTML += `<div class="caps-allowlist">allowlist: ${escHtml(pol.shell_allowlist_prefixes.join(", "))}</div>`;
  }
  capsToolBody.innerHTML = (data.tools || [])
    .map((t) => {
      const risk = t.risk_level || "medium";
      const hints = [];
      if (t.destructive) hints.push("dest");
      if (t.network) hints.push("net");
      if (t.open_world) hints.push("open");
      return `<tr>
        <td>${escHtml(t.name)}</td>
        <td>${escHtml(t.category || "")}</td>
        <td class="risk-${escHtml(risk)}">${escHtml(risk)}</td>
        <td>${t.is_readonly ? "Y" : "—"}</td>
        <td>${hints.length ? escHtml(hints.join(",")) : "—"}</td>
      </tr>`;
    })
    .join("");
  capsBoundaries.innerHTML = (data.boundaries || [])
    .map((b) => `<li>${escHtml(b)}</li>`)
    .join("");
  if (capsLoading) capsLoading.classList.add("hidden");
  if (capsContent) capsContent.classList.remove("hidden");
}

async function loadCapabilities() {
  if (!capsPane) return;
  const wd = (workdirInput && workdirInput.value.trim()) || "demos";
  if (capsLoading) {
    capsLoading.classList.remove("hidden");
    capsLoading.textContent = "Loading capabilities…";
  }
  if (capsContent) capsContent.classList.add("hidden");
  try {
    const res = await fetch(`/api/capabilities?workdir=${encodeURIComponent(wd)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.status);
    renderCapabilities(data);
  } catch (err) {
    if (capsLoading) {
      capsLoading.classList.remove("hidden");
      capsLoading.textContent = `Failed: ${err.message || err}`;
    }
  }
}

function closeModal(which) {
  if (which === "folder") folderModal.classList.add("hidden");
  if (which === "style" && styleModal) styleModal.classList.add("hidden");
  if (which === "code" || which === "diff") {
    codeModal.classList.add("hidden");
    disposeMonacoEditors();
  }
}

composer.addEventListener("submit", (e) => {
  e.preventDefault();
  startRun();
});

// Belt-and-suspenders: Enter always steers/sends even if submit is quirky
if (chatInput) {
  chatInput.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    startRun();
  });
}
if (stopBtn) {
  stopBtn.addEventListener("click", () => requestStop());
}

if (contextRing) {
  contextRing.addEventListener("click", (e) => {
    e.preventDefault();
    toggleContextRingDetail();
  });
}

workdirInput.addEventListener("change", () => {
  workdirInput.dataset.touched = "1";
  loadFileTree();
  loadStyles();
  if (capsPane && !capsPane.classList.contains("hidden")) loadCapabilities();
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
if (btnAskUserReply) {
  btnAskUserReply.addEventListener("click", () => respondAskUser());
}
if (askUserInput) {
  askUserInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      respondAskUser();
    }
  });
}
if (btnDiffRedo) {
  btnDiffRedo.addEventListener("click", () => applyDiffToDisk("modified"));
}

document.querySelectorAll(".rp-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchRightTab(tab.dataset.tab));
});

if (btnStyleNew) {
  btnStyleNew.addEventListener("click", () => openStyleEditor(null));
}
if (btnStyleRefresh) {
  btnStyleRefresh.addEventListener("click", () => loadStyles());
}
if (btnStyleSave) {
  btnStyleSave.addEventListener("click", () => saveStyleFromModal());
}
if (btnStyleDelete) {
  btnStyleDelete.addEventListener("click", () => deleteStyleFromModal());
}

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
loadStyles();
resetCostPanel(Number(maxStepsInput && maxStepsInput.value) || 30);
resetContextMeter();
chatInput.focus();
