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
const todoPanel = document.getElementById("todoPanel");
const todoList = document.getElementById("todoList");
const runStatus = document.getElementById("runStatus");

let running = false;
let historyCache = [];
let stepCards = new Map(); // step -> DOM element
let activeStep = null;

const ICONS = ["🌐", "📈", "📄"];

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

function showHome() {
  homeView.classList.remove("hidden");
  chatView.classList.add("hidden");
  timeline.innerHTML = "";
  stepCards = new Map();
  activeStep = null;
  todoPanel.classList.add("hidden");
  todoList.innerHTML = "";
  setStatus("idle", "Idle");
}

function scrollChat() {
  chatView.scrollTop = chatView.scrollHeight;
}

function focusActiveCard(card) {
  if (!card) return;
  // Center the running step in the chat viewport
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

function expandStepCard(card) {
  if (!card) return;
  card.classList.remove("is-collapsed");
}

function setActiveStep(step, maxSteps) {
  if (activeStep != null && activeStep !== step) {
    const prev = stepCards.get(activeStep);
    if (prev && !prev.classList.contains("is-collapsed")) {
      // keep expanded until markStepDone collapses it
    }
  }
  activeStep = step;
  const card = ensureStepCard(step, maxSteps);
  stepCards.forEach((c, s) => {
    if (s === step) {
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
  // Collapse any still-open active step
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
  if (stepCards.has(step)) {
    const existing = stepCards.get(step);
    if (maxSteps) {
      const badge = existing.querySelector(".step-badge");
      if (badge) badge.textContent = `Step ${step}/${maxSteps}`;
    }
    return existing;
  }
  const card = document.createElement("div");
  card.className = "step-card is-active";
  card.dataset.step = String(step);
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
  stepCards.set(step, card);
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
  // Rich text for tool results that look like markdown / todo lists
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

function markStepDone(step, kind) {
  const card = stepCards.get(step);
  if (!card) return;
  const state = card.querySelector(".step-state");
  state.textContent = kind === "final" ? "done" : "done";
  state.classList.add("done");
  collapseStepCard(card);
  card.querySelector(".step-head")?.setAttribute("aria-expanded", "false");
}

function renderTodos(todos) {
  if (!todos || !todos.length) {
    todoPanel.classList.add("hidden");
    return;
  }
  todoPanel.classList.remove("hidden");
  todoList.innerHTML = "";
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
    btn.innerHTML = `<span class="bubble">💬</span><span>${escapeHtml(item.title)}</span>`;
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
    case "todo_update":
      renderTodos(data.todos || []);
      break;
    case "step_end":
      markStepDone(data.step, data.kind);
      break;
    case "final":
      // done event also carries final; avoid double if both fire
      break;
    case "done":
      addFinalBubble(
        data.final_text || "",
        `${data.stopped_reason} · ${data.steps} steps` +
          (data.transcript_id ? ` · ${data.transcript_id}` : "")
      );
      setStatus("idle", "Done");
      break;
    case "error":
      addInfoBubble(data.message || "Unknown error");
      setStatus("err", "Error");
      break;
    case "log":
      // Structured events preferred; skip noisy raw logs in UI.
      break;
    default:
      break;
  }
}

/** Replay a saved transcript into the same card UI (no API cost). */
function replayTranscript(data) {
  showChat();
  timeline.innerHTML = "";
  stepCards = new Map();
  activeStep = null;
  todoPanel.classList.add("hidden");
  todoList.innerHTML = "";

  const task = data.task || "";
  addUserBubble(task);
  addInfoBubble(`Replay · ${data.created_at || ""} · ${data.stopped_reason || ""}`);

  const messages = data.messages || [];
  let step = 0;
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
      markStepDone(step, "tools");
    }
  }
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
  renderSuggestions(data.suggestions || []);
}

async function loadHistory() {
  const res = await fetch("/api/history");
  const data = await res.json();
  historyCache = data.items || [];
  renderHistory(historyCache);
}

async function resetDemos() {
  try {
    const res = await fetch("/api/demos/reset", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.status);
    alert("Demos reset:\n" + (data.reset || []).join("\n"));
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
  // Keep previous run visible above? Clear for clarity on new run.
  timeline.innerHTML = "";
  stepCards = new Map();
  activeStep = null;
  todoPanel.classList.add("hidden");
  todoList.innerHTML = "";
  addUserBubble(task);

  const payload = {
    task,
    workdir: workdirInput.value.trim() || "demos",
    max_steps: Number(maxStepsInput.value) || 20,
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
  }
}

composer.addEventListener("submit", (e) => {
  e.preventDefault();
  startRun();
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
historySearch.addEventListener("input", () => renderHistory(historyCache));

loadMeta();
loadHistory();
chatInput.focus();
