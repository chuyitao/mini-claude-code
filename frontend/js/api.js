// 与后端通信的唯一出口：REST 调用 + 一条 SSE 事件流。
import { API_BASE } from "./config.js";

async function request(method, path, body) {
  const response = await fetch(API_BASE + path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : `请求失败（${response.status}）`;
    throw Object.assign(new Error(detail), { status: response.status });
  }
  return data;
}

export const api = {
  listSessions: () => request("GET", "/api/sessions"),
  createSession: () => request("POST", "/api/sessions", {}),
  deleteSession: (id) => request("DELETE", `/api/sessions/${id}`),
  sessionEvents: (id) => request("GET", `/api/sessions/${id}/events`),
  sendMessage: (id, text) => request("POST", `/api/sessions/${id}/messages`, { text }),
  stop: () => request("POST", "/api/stop"),
  answerPermission: (id, allow, always = false) => request("POST", `/api/permissions/${id}`, { allow, always }),
  setPermissionMode: (mode) => request("PUT", "/api/settings/permission-mode", { mode }),
  state: (sessionId) => request("GET", `/api/state${sessionId ? `?session_id=${sessionId}` : ""}`),
  tools: () => request("GET", "/api/tools"),
  setGoal: (condition, sessionId) => request("PUT", "/api/goal", { condition, session_id: sessionId }),
  clearGoal: (sessionId) => request("DELETE", `/api/goal${sessionId ? `?session_id=${sessionId}` : ""}`),
  cancelCron: (id) => request("DELETE", `/api/crons/${id}`),
  connectMcp: (name) => request("POST", `/api/mcp/${name}/connect`),
  memory: (filename) => request("GET", `/api/memory/${encodeURIComponent(filename)}`),
  skill: (name) => request("GET", `/api/skills/${encodeURIComponent(name)}`),
};

// 打开事件流。浏览器会自动重连；每次（重新）连上都会回调 onStatus(true)。
export function openEvents(onEvent, onStatus) {
  const source = new EventSource(`${API_BASE}/api/events`);
  source.onopen = () => onStatus(true);
  source.onerror = () => onStatus(false);
  source.onmessage = (message) => onEvent(JSON.parse(message.data));
  return source;
}
