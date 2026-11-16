// 入口：把接口、事件流和三块视图接在一起。
import { api, openEvents } from "./api.js";
import { API_BASE } from "./config.js";
import { h, toast } from "./dom.js";
import { createChat } from "./views/chat.js";
import { createInspector } from "./views/inspector.js";
import { renderEnv, renderSessions } from "./views/sidebar.js";

const $ = (id) => document.getElementById(id);
const COMMANDS = [
  ["/goal ", "/goal <完成条件>", "设定完成目标，没达成就不停"],
  ["/goal clear", "/goal clear", "清除当前目标"],
  ["/compact", "/compact", "立即压缩当前会话的上下文"],
  ["/mode auto", "/mode auto", "Shell 命令自动放行（黑名单除外）"],
  ["/mode ask", "/mode ask", "每条 Shell 命令执行前询问"],
];
// 这些事件发生后，右侧面板的数据可能变了
const REFRESH_ON = new Set(["tool_end", "todos", "team", "background", "cron", "goal", "turn_end",
  "permission_request", "permission_resolved", "compact", "session", "settings"]);

const store = { sessions: [], currentId: null, lastSeq: 0, running: null, loading: false, buffered: [], wasOnline: false };

const chat = createChat({
  root: $("trace"),
  scroller: $("conversation"),
  onPermission: (id, allow, always) => api.answerPermission(id, allow, always).catch((error) => toast(error.message, true)),
  onStarter: (prompt) => send(prompt),
});
const inspector = createInspector({ tabsRoot: $("tabs"), bodyRoot: $("tab-body"), onChange: () => refreshState(), sessionId: () => store.currentId });

// ---------------------------------------------------------------- 状态刷新

let refreshTimer = null;
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refreshState, 250);
}

async function refreshState() {
  try {
    const [state, tools] = await Promise.all([api.state(store.currentId), api.tools()]);
    store.running = state.running;
    inspector.update(state, tools);
    renderEnv($("env"), state.settings);
    setMode(state.settings.permission_mode);
    setContext(state.context.size, state.context.limit);
    syncComposer();
    drawSessions();
  } catch { /* 后端暂时不可达：连接状态会提示 */ }
}

async function refreshSessions() {
  store.sessions = await api.listSessions();
  drawSessions();
}

function drawSessions() {
  renderSessions($("session-list"), store.sessions, {
    currentId: store.currentId, runningId: store.running,
    onSelect: selectSession, onDelete: removeSession,
  });
  const current = store.sessions.find((session) => session.id === store.currentId);
  $("session-title").textContent = current?.title || "新会话";
}

function setMode(mode) {
  for (const button of $("mode-switch").children) button.classList.toggle("active", button.dataset.mode === mode);
}

function setContext(size, limit) {
  const ratio = limit ? Math.min(1, size / limit) : 0;
  $("context-fill").style.width = `${Math.round(ratio * 100)}%`;
  $("context-value").textContent = `${Math.round(ratio * 100)}%`;
  $("context-meter").classList.toggle("hot", ratio >= 0.8);
  $("context-meter").title = `上下文占用：${size.toLocaleString()} / ${limit.toLocaleString()} 字符，超过上限会自动压缩`;
}

function syncComposer() {
  const busy = store.running !== null;
  $("send").hidden = busy;
  $("stop").hidden = !busy;
  $("send").disabled = !$("input").value.trim();
}

// ---------------------------------------------------------------- 会话

async function selectSession(id) {
  store.currentId = id;
  store.loading = true;
  store.buffered = [];
  chat.reset();
  drawSessions();
  try {
    const events = await api.sessionEvents(id);
    chat.setReplaying(true);
    store.lastSeq = 0;
    for (const event of events) applyToChat(event);
    chat.setReplaying(false);
  } catch (error) {
    toast(error.message, true);
  }
  store.loading = false;
  for (const event of store.buffered.splice(0)) applyToChat(event);
  chat.showWelcome();
  refreshState();
  document.getElementById("app").classList.remove("show-sidebar");
  $("input").focus();
}

async function newSession() {
  const session = await api.createSession();
  await refreshSessions();
  await selectSession(session.id);
}

async function removeSession(id) {
  try {
    await api.deleteSession(id);
  } catch (error) { toast(error.message, true); return; }
  await refreshSessions();
  if (id === store.currentId) {
    if (store.sessions.length) await selectSession(store.sessions[0].id);
    else await newSession();
  }
}

// ---------------------------------------------------------------- 事件

function applyToChat(event) {
  if (event.seq <= store.lastSeq) return;      // 回放时已处理过
  store.lastSeq = event.seq;
  chat.apply(event);
  if (event.type === "context") setContext(event.size, event.limit);
}

function onEvent(event) {
  if (event.type === "log") { inspector.addLog(event); return; }
  if (event.type === "turn_start") store.running = event.session_id;
  if (event.type === "turn_end") store.running = null;
  if (event.type === "session") refreshSessions().catch(() => {});
  if (event.type === "settings" && event.permission_mode) setMode(event.permission_mode);
  if (REFRESH_ON.has(event.type)) scheduleRefresh();
  if (event.type === "turn_start" || event.type === "turn_end") { syncComposer(); drawSessions(); }
  if (event.type === "turn_start" || event.type === "turn_end") refreshSessions().catch(() => {});

  if (event.session_id && event.session_id !== store.currentId) return;   // 其他会话的事件
  if (store.loading) store.buffered.push(event);
  else applyToChat(event);
}

function onStatus(online) {
  const node = $("conn");
  node.className = online ? "conn online" : "conn offline";
  node.lastElementChild.textContent = online ? "已连接" : "连接断开";
  node.title = online ? `已连接 ${API_BASE}` : `无法连接 ${API_BASE}，请确认后端已启动`;
  if (online && !store.wasOnline) bootstrap();   // 首次连上或断线重连：重新同步
  store.wasOnline = online;
}

async function bootstrap() {
  try {
    await refreshSessions();
    const keep = store.sessions.find((session) => session.id === store.currentId);
    if (keep) await selectSession(keep.id);
    else if (store.sessions.length) await selectSession(store.sessions[0].id);
    else await newSession();
  } catch (error) {
    toast(error.message, true);
  }
}

// ---------------------------------------------------------------- 输入

async function send(text) {
  const value = (text ?? $("input").value).trim();
  if (!value || !store.currentId) return;
  if (store.running !== null && !value.startsWith("/")) { toast("上一轮还在执行，可以先点“停止”。", true); return; }
  $("input").value = "";
  autosize();
  hideSlash();
  syncComposer();
  try {
    await api.sendMessage(store.currentId, value);
  } catch (error) {
    $("input").value = value;
    autosize();
    syncComposer();
    toast(error.message, true);
  }
}

function autosize() {
  const input = $("input");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
}

function hideSlash() { $("slash-menu").hidden = true; }

function drawSlash() {
  const value = $("input").value;
  const menu = $("slash-menu");
  const matches = value.startsWith("/") && !value.includes("\n")
    ? COMMANDS.filter(([insert]) => insert.startsWith(value.split(" ")[0]) && insert.trim() !== value.trim()) : [];
  menu.hidden = matches.length === 0;
  menu.replaceChildren(...matches.map(([insert, label, hint]) => h("button", {
    onclick: () => { $("input").value = insert; hideSlash(); $("input").focus(); syncComposer(); },
  }, h("code", {}, label), h("span", {}, hint))));
}

// ---------------------------------------------------------------- 绑定

$("input").addEventListener("input", () => { autosize(); drawSlash(); syncComposer(); });
$("input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
  if (event.key === "Escape") hideSlash();
});
$("send").addEventListener("click", () => send());
$("stop").addEventListener("click", () => api.stop().catch((error) => toast(error.message, true)));
$("new-session").addEventListener("click", () => newSession().catch((error) => toast(error.message, true)));
$("mode-switch").addEventListener("click", (event) => {
  const mode = event.target.dataset.mode;
  if (mode) api.setPermissionMode(mode).then(() => setMode(mode)).catch((error) => toast(error.message, true));
});
$("toggle-sidebar").addEventListener("click", (event) => { event.stopPropagation(); $("app").classList.toggle("show-sidebar"); });
$("toggle-inspector").addEventListener("click", (event) => { event.stopPropagation(); $("app").classList.toggle("show-inspector"); });
// 窄屏下两侧面板是抽屉：点对话区域收起
document.querySelector(".main").addEventListener("click", () => $("app").classList.remove("show-sidebar", "show-inspector"));

inspector.render();
syncComposer();
openEvents(onEvent, onStatus);
setInterval(() => { if (store.running !== null) refreshState(); }, 4000);
