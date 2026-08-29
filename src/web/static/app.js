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
const workspace = document.querySelector(".workspace");
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

let running = false;
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
let monacoReady = null;
let monacoEditor = null;
let monacoDiff = null;
let monacoMode = null; // "code" | "diff"

const ICONS = ["🌐", "📈", "📄"];

function newSessionId() {
  if (crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "").slice(0, 24);
  return `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

function setStatus(mode, text) {
  runStatus.className = `run-status ${mode}`;
  runStatus.textContent = text;
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
  setStatus("idle", "Idle");
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
    btn.title = "View diff in VS Code editor";
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
}

async function openCodeViewer(path, content, { modeBadge = "Preview" } = {}) {
  const lang = langFromPath(path);
  codeTitle.textContent = path;
  codeTabName.textContent = path.split(/[/\\]/).pop() || path;
  codeModeBadge.textContent = modeBadge;
  codeLangLabel.textContent = lang;
  codeModal.classList.remove("hidden");
  disposeMonacoEditors();
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
  try {
    const monaco = await loadMonaco();
    monacoMode = "diff";
    const original = monaco.editor.createModel(ch.old_content ?? "", lang);
    const modified = monaco.editor.createModel(ch.new_content ?? "", lang);
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
      chatInput.value = item.prompt;
      chatInput.focus();
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
    case "file_change":
      recordFileChange(data);
      break;
    case "todo_update":
      renderTodos(data.todos || []);
      break;
    case "step_end":
      markStepDone(data.step);
      break;
    case "final":
      break;
    case "done":
      addFinalBubble(
        data.final_text || "",
        `${data.stopped_reason} · ${data.steps} steps` +
          (data.transcript_id ? ` · ${data.transcript_id}` : "")
      );
      setStatus("idle", "Done");
      sessionActive = true;
      break;
    case "error":
      addInfoBubble(data.message || "Unknown error");
      setStatus("err", "Error");
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

  const changes = data.file_changes || [];
  for (const ch of changes) recordFileChange(ch);

  addFinalBubble(
    data.final_text || "",
    `${data.stopped_reason || ""} · ${data.steps || step} steps (replay)`
  );
  setStatus("idle", "Replay");
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
    fileTree.innerHTML = "";
    (data.nodes || []).forEach((node) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `file-node${node.kind === "dir" ? " is-dir" : ""}`;
      btn.style.paddingLeft = `${8 + (node.depth || 0) * 14}px`;
      btn.innerHTML = `<span class="icon">${node.kind === "dir" ? "📁" : "📄"}</span><span>${escapeHtml(node.name)}</span>`;
      if (node.kind === "file") {
        btn.addEventListener("click", () => openFileWindow(node.path));
      }
      fileTree.appendChild(btn);
    });
    if (!(data.nodes || []).length) {
      fileTree.innerHTML = `<div class="todo-empty">Empty folder</div>`;
    }
  } catch (err) {
    fileTree.innerHTML = `<div class="todo-empty">${escapeHtml(String(err))}</div>`;
  }
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
  const task = (taskText || chatInput.value || "").trim();
  if (!task || running) return;

  running = true;
  sendBtn.disabled = true;
  setStatus("running", "Running…");
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
  }

  addUserBubble(task);

  const payload = {
    task,
    workdir: workdirInput.value.trim() || "demos",
    max_steps: Number(maxStepsInput.value) || 20,
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
      setStatus("err", "Blocked");
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
    setStatus("err", "Error");
  } finally {
    running = false;
    sendBtn.disabled = false;
    chatInput.value = "";
    if (runStatus.textContent === "Running…") setStatus("idle", "Idle");
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

workdirInput.addEventListener("change", () => {
  workdirInput.dataset.touched = "1";
  loadFileTree();
});
workdirInput.addEventListener("input", () => {
  workdirInput.dataset.touched = "1";
});

document.getElementById("btnNew").addEventListener("click", () => {
  showHome();
  chatInput.focus();
});

document.querySelector('.nav-item[data-view="home"]').addEventListener("click", () => {
  showHome();
});

document.getElementById("btnRefreshHistory").addEventListener("click", loadHistory);
document.getElementById("btnResetDemos").addEventListener("click", resetDemos);
document.getElementById("btnOpenFolder").addEventListener("click", openFolderModal);
document.getElementById("btnFolderGo").addEventListener("click", () => browseFolder(folderPathInput.value.trim()));
document.getElementById("btnFolderSelect").addEventListener("click", selectFolder);
document.getElementById("btnToggleRight").addEventListener("click", () => {
  workspace.classList.toggle("right-collapsed");
});

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

loadMeta();
loadHistory();
chatInput.focus();
