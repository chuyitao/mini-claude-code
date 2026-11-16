// 左栏：会话列表 + 运行环境
import { clear, h } from "../dom.js";

export function renderSessions(root, sessions, { currentId, runningId, onSelect, onDelete }) {
  clear(root);
  for (const session of sessions) {
    root.append(h("div", {
      class: session.id === currentId ? "session-item active" : "session-item",
      role: "button", tabindex: "0",
      onclick: () => onSelect(session.id),
      onkeydown: (event) => { if (event.key === "Enter") onSelect(session.id); },
    },
    h("span", { class: "title" }, session.title || "新会话"),
    session.id === runningId ? h("span", { class: "live", title: "运行中" }) : null,
    h("button", {
      class: "remove", title: "删除会话", "aria-label": "删除会话",
      onclick: (event) => { event.stopPropagation(); onDelete(session.id); },
    }, "×")));
  }
}

export function renderEnv(root, settings) {
  clear(root);
  if (!settings) return;
  root.append(
    h("div", {}, h("dt", {}, "模型"), h("dd", { title: settings.model, class: settings.mock ? "demo" : "" },
      settings.mock ? "离线演示模式" : settings.model)),
    h("div", {}, h("dt", {}, "工作区"), h("dd", { title: settings.workdir }, settings.workdir)));
}
