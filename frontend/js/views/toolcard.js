// 工具调用卡片：一行摘要，点开看参数与输出；子 Agent 的工具调用嵌在父卡片里。
import { formatDuration, h } from "../dom.js";

const STATE_TEXT = { running: "运行中", waiting: "等待确认", denied: "已拒绝", error: "出错" };

// 线性图标：按工具类别区分，方便在一长串调用里一眼找到想看的那一步
const svg = (body) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const ICONS = {
  terminal: svg('<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>'),
  file: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><polyline points="14 3 14 8 19 8"/>'),
  edit: svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>'),
  search: svg('<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/>'),
  list: svg('<path d="M10 6h10M10 12h10M10 18h10"/><path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"/>'),
  branch: svg('<circle cx="6" cy="5" r="2.5"/><circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="8" r="2.5"/><path d="M6 7.5v9"/><path d="M18 10.5c0 3.5-3 4.5-6 4.5H6"/>'),
  book: svg('<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>'),
  users: svg('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M21.5 20a6.5 6.5 0 0 0-4-6"/>'),
  clock: svg('<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>'),
  plug: svg('<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4"/>'),
  layers: svg('<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>'),
  flow: svg('<rect x="3" y="4" width="6" height="6" rx="1.5"/><rect x="15" y="14" width="6" height="6" rx="1.5"/><path d="M9 7h4a2 2 0 0 1 2 2v5"/>'),
  other: svg('<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/>'),
};
const CHEVRON = svg('<polyline points="9 6 15 12 9 18"/>');
const TOOL_ICON = {
  bash: "terminal", read_file: "file", write_file: "edit", edit_file: "edit", glob: "search",
  todo_write: "list", task: "branch", load_skill: "book", compact: "layers", Workflow: "flow",
  create_task: "list", update_task: "list", list_tasks: "list", get_task: "list", claim_task: "list", complete_task: "list",
  schedule_cron: "clock", list_crons: "clock", cancel_cron: "clock",
  spawn_teammate: "users", list_teammates: "users", send_message: "users", request_shutdown: "users",
  request_plan: "users", review_plan: "users", submit_plan: "users", create_worktree: "branch", connect_mcp: "plug",
};
const iconFor = (name) => ICONS[name.startsWith("mcp__") ? "plug" : TOOL_ICON[name] || "other"];

// 每种工具在折叠状态下显示的一行摘要
function summarize(name, input) {
  const first = (value) => String(value ?? "").split("\n")[0];
  switch (name) {
    case "bash": return first(input.command) + (input.run_in_background ? "  （后台）" : "");
    case "read_file": case "write_file": case "edit_file": return input.path || "";
    case "glob": return input.pattern || "";
    case "todo_write": return `${(input.todos || []).length} 项待办`;
    case "task": return first(input.description);
    case "load_skill": case "connect_mcp": return input.name || "";
    case "create_task": return input.subject || "";
    case "update_task": return `${input.task_id} ← ${(input.addBlockedBy || []).join(", ")}`;
    case "spawn_teammate": return `${input.name}（${input.role}）`;
    case "send_message": return `→ ${input.to}: ${first(input.content)}`;
    case "schedule_cron": return `${input.cron}  ${first(input.prompt)}`;
    case "Workflow": return input.name || "";
    default: {
      const value = Object.values(input || {}).find((item) => typeof item === "string");
      return value ? first(value) : Object.keys(input || {}).length ? JSON.stringify(input) : "";
    }
  }
}

function section(title, body, copyText) {
  const copy = copyText == null ? null : h("button", {
    class: "copy",
    onclick: async (event) => {
      await navigator.clipboard?.writeText(copyText);
      event.target.textContent = "已复制";
      setTimeout(() => { event.target.textContent = "复制"; }, 1200);
    },
  }, "复制");
  return h("div", { class: "tool-section" }, h("h4", {}, title, copy), body);
}

function diff(oldText, newText) {
  const lines = (text, cls) => String(text ?? "").split("\n").map((line) => h("div", { class: cls }, line || " "));
  return h("div", { class: "diff" }, lines(oldText, "del"), lines(newText, "add"));
}

function checklist(todos) {
  return h("ul", { class: "checklist" }, (todos || []).map((todo) =>
    h("li", { class: todo.status }, h("span", { class: "box" }), h("span", {}, todo.content))));
}

// 参数区：常用工具给出更易读的展示，其余工具直接显示 JSON
function inputSections(name, input) {
  switch (name) {
    case "bash": return [section("命令", h("pre", { class: "term command" }, input.command || ""), input.command || "")];
    case "write_file": return [section(`写入 ${input.path || ""}`, h("pre", {}, input.content || ""), input.content || "")];
    case "edit_file": return [section(`修改 ${input.path || ""}`, diff(input.old_text, input.new_text))];
    case "todo_write": return [section("待办清单", checklist(input.todos))];
    case "task": return [section("交给子 Agent 的任务", h("pre", {}, input.description || ""))];
    default: {
      if (!input || !Object.keys(input).length) return [];
      const json = JSON.stringify(input, null, 2);
      return [section("参数", h("pre", {}, json), json)];
    }
  }
}

export function createToolCard(start) {
  const input = start.input || {};
  const stateLabel = h("span", { class: "tool-state" }, STATE_TEXT.running);
  const body = h("div", { class: "tool-body" }, inputSections(start.name, input));
  const children = h("div", { class: "tool-children" });
  const head = h("button", { class: "tool-head", "aria-expanded": "false" },
    h("span", { class: "tool-icon", html: iconFor(start.name) }),
    h("span", { class: "tool-name" }, start.name),
    start.agent && start.agent !== "lead" ? h("span", { class: "tool-agent" }, start.agent) : null,
    h("span", { class: "tool-summary" }, summarize(start.name, input)),
    stateLabel,
    h("span", { class: "tool-chevron", html: CHEVRON }));
  const el = h("div", { class: "step tool", dataset: { status: "running", id: start.id } }, head, body, children);
  let permission = null;
  let progress = null;

  const setOpen = (open) => {
    el.classList.toggle("open", open);
    head.setAttribute("aria-expanded", String(open));
  };
  head.addEventListener("click", () => setOpen(!el.classList.contains("open")));

  return {
    el,
    children,
    name: start.name,
    isRunning: () => el.dataset.status === "running" || el.dataset.status === "waiting",

    finish(end) {
      el.dataset.status = end.status;
      stateLabel.textContent = STATE_TEXT[end.status] || formatDuration(end.duration_ms);
      if (end.status !== "ok" && STATE_TEXT[end.status]) stateLabel.textContent += ` · ${formatDuration(end.duration_ms)}`;
      const output = end.output || "";
      if (output) {
        const title = end.truncated ? "输出（过长，已截断显示）" : "输出";
        const classes = [start.name === "bash" ? "term" : "", end.status === "ok" ? "" : "fail"].join(" ").trim();
        body.append(section(title, h("pre", { class: classes }, output), output));
      }
      if (end.status === "error" || end.status === "denied") setOpen(true);
    },

    // 需要用户确认：卡片自动展开，给出 允许 / 始终允许 / 拒绝
    askPermission(request, answer) {
      el.dataset.status = "waiting";
      stateLabel.textContent = STATE_TEXT.waiting;
      const allow = h("button", { class: "btn btn-solid btn-small", onclick: () => answer(true, false) }, "允许");
      permission = h("div", { class: "permission" },
        h("p", {}, `${request.title}，是否允许？`),
        h("pre", {}, request.detail || ""),
        h("div", { class: "actions" },
          allow,
          h("button", { class: "btn btn-small", onclick: () => answer(true, true) }, `本次运行始终允许 ${request.tool}`),
          h("button", { class: "btn btn-small", onclick: () => answer(false, false) }, "拒绝")));
      body.prepend(permission);
      setOpen(true);
      return allow;
    },

    resolvePermission(resolved) {
      if (el.dataset.status === "waiting") {
        el.dataset.status = "running";
        stateLabel.textContent = STATE_TEXT.running;
      }
      if (!permission) return;
      const text = resolved.reason === "timeout" ? "等待超时，已按拒绝处理"
        : resolved.allow ? (resolved.always ? "已允许（本次运行内不再询问）" : "已允许") : "已拒绝";
      permission.className = "permission done";
      permission.replaceChildren(text);
      permission = null;
      if (resolved.allow) setOpen(false);
    },

    // 工作流等长任务的进度行
    addProgress(text) {
      if (!progress) {
        progress = h("pre", {});
        body.append(section("进度", progress));
      }
      progress.textContent += (progress.textContent ? "\n" : "") + text;
    },
  };
}
