// 对话轨迹：把后端事件流逐条翻译成界面。刷新页面时用同一套逻辑回放历史事件。
import { clear, formatNumber, h } from "../dom.js";
import { renderMarkdown } from "../markdown.js";
import { createToolCard } from "./toolcard.js";

const SOURCE_LABEL = { cron: "定时任务触发", team: "团队事件触发", background: "后台任务完成后触发" };
const COMPACT_LABEL = { snip: "归档旧消息", micro: "精简旧的工具结果", fit: "压缩大体积结果", summary: "生成摘要", manual: "手动压缩", reactive: "超出上限后被动压缩" };
const GOAL_LABEL = {
  set: "已设定目标", cleared: "目标已清除", block: "目标尚未达成，继续执行", achieved: "目标已达成",
  failed: "评估认为目标无法完成", limit: "连续多轮未达成，已暂停", error: "目标评估出错", defer: "后台任务仍在运行，稍后再评估",
};

const STARTERS = [
  ["写个脚本并运行", "创建 hello.py，运行它，再把问候语改成中文"],
  ["派子 Agent 调研", "用子 Agent 调研一下工作区里的 Python 文件"],
  ["拆任务并组建团队", "把工作拆成有依赖的任务，再派一位队友来做"],
  ["后台执行慢命令", "在后台运行一次构建，完成后告诉我"],
  ["设置定时任务", "每 30 分钟定时检查一次测试是否通过"],
  ["接入 MCP 工具", "连接 docs 这个 MCP server，然后搜索 agent loop"],
  ["运行工作流", "用 review-changes 工作流审查一段改动"],
  ["加载技能", "加载 code-review 技能"],
];

export function createChat({ root, scroller, onPermission, onStarter }) {
  let turn = null;
  let replaying = false;
  const tools = new Map();
  const messages = new Map();

  // 用户停在底部时，新内容出现就跟着滚动；用户往上翻看历史时不打扰
  let pinned = true;
  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 140;
  scroller.addEventListener("scroll", () => { pinned = nearBottom(); });
  const stick = () => { if (pinned || replaying) scroller.scrollTop = scroller.scrollHeight; };

  function newTurn(source) {
    root.querySelector(".welcome")?.remove();
    const steps = h("div", { class: "steps" });
    const el = h("section", { class: "turn" },
      SOURCE_LABEL[source] ? h("p", { class: "turn-source" }, SOURCE_LABEL[source]) : null, steps);
    root.append(el);
    turn = { el, steps };
    return turn;
  }
  const current = () => turn || newTurn("user");

  function note(content, alert = false) {
    const el = h("div", { class: alert ? "step note alert" : "step note" }, content);
    current().steps.append(el);
    return el;
  }

  function renderMessage(message) {
    message.frame = null;
    message.md.innerHTML = renderMarkdown(message.buffer);
  }

  const handlers = {
    turn_start(event) { newTurn(event.source); },

    user_message(event) {
      const system = event.source === "team";
      current().steps.before(h("div", { class: system ? "user-msg system" : "user-msg" }, event.text));
    },

    assistant_start(event) {
      const md = h("div", { class: "md" });
      const el = h("div", { class: "step assistant streaming" }, md);
      current().steps.append(el);
      messages.set(event.id, { el, md, buffer: "", frame: null });
    },

    text_delta(event) {
      const message = messages.get(event.id);
      if (!message) return;
      message.buffer += event.text;
      if (!message.frame) message.frame = requestAnimationFrame(() => renderMessage(message));
    },

    assistant_abort(event) {
      messages.get(event.id)?.el.remove();
      messages.delete(event.id);
    },

    assistant_end(event) {
      const message = messages.get(event.id);
      if (!message) return;
      cancelAnimationFrame(message.frame);
      message.el.classList.remove("streaming");
      if (!event.text && !event.thinking) { message.el.remove(); return; }
      message.buffer = event.text || "";
      renderMessage(message);
      if (event.thinking) {
        message.el.prepend(h("details", { class: "thinking" },
          h("summary", {}, "思考过程"), h("pre", {}, event.thinking)));
      }
      if (!event.has_tools && event.usage) {
        message.el.append(h("div", { class: "usage" },
          `输入 ${formatNumber(event.usage.input)} · 输出 ${formatNumber(event.usage.output)} tokens`));
      }
    },

    tool_start(event) {
      const card = createToolCard(event);
      tools.set(event.id, card);
      const parent = event.parent && tools.get(event.parent);
      (parent ? parent.children : current().steps).append(card.el);
    },

    tool_end(event) { tools.get(event.id)?.finish(event); },

    permission_request(event) {
      const card = tools.get(event.tool_id);
      if (!card) return;
      const allow = card.askPermission(event, (ok, always) => onPermission(event.id, ok, always));
      if (!replaying) allow.focus({ preventScroll: true });
    },

    permission_resolved(event) { tools.get(event.tool_id)?.resolvePermission(event); },

    compact(event) {
      note(["上下文已压缩（", COMPACT_LABEL[event.kind] || event.kind, "）：",
        h("b", {}, `${formatNumber(event.before)} → ${formatNumber(event.after)}`), " 字符"]);
    },

    notice(event) { note(event.text); },

    notification(event) { note(["后台任务有结果了", h("pre", {}, event.text)]); },

    goal(event) {
      const detail = event.action === "set" ? event.condition : event.reason;
      note([h("b", {}, GOAL_LABEL[event.action] || event.action), detail ? `：${detail}` : ""],
        ["failed", "error", "limit"].includes(event.action));
    },

    cron(event) { if (event.event === "inject") note([h("b", {}, "定时任务触发"), `：${event.prompt}`]); },

    team(event) {
      if (event.event === "spawned") note([h("b", {}, `队友 ${event.name}`), `（${event.role}）已加入${event.task_id ? `，负责 ${event.task_id}` : ""}`]);
      else if (event.event === "finished") note([h("b", {}, `队友 ${event.name}`), " 已结束工作"]);
      else if (event.event === "message") note([h("b", {}, `${event.from} → ${event.to}`), `（${event.message_type}）：${event.content}`]);
    },

    workflow(event) {
      const card = [...tools.values()].reverse().find((item) => item.name === "Workflow" && item.isRunning());
      const data = Object.entries(event.data || {}).map(([key, value]) => `${key}=${value}`).join("  ");
      card?.addProgress(`${event.event}  ${data}`);
    },

    background(event) {
      if (event.status !== "running") note([h("b", {}, `后台任务 ${event.id}`), event.status === "completed" ? " 已完成" : " 失败"], event.status !== "completed");
    },

    error(event) { note(event.message, true); },

    turn_end(event) {
      for (const message of messages.values()) message.el.classList.remove("streaming");
      if (event.result === "cancelled") note("已中断本轮执行。", true);
    },
  };

  return {
    reset() {
      clear(root);
      pinned = true;
      turn = null;
      tools.clear();
      messages.clear();
    },

    showWelcome() {
      if (root.children.length) return;
      root.append(h("div", { class: "welcome" },
        h("h2", {}, "今天要让 Agent 做什么？"),
        h("p", {}, "它可以在工作区里读写文件、执行命令、拆分任务、派出队友。每一次工具调用都会出现在对话里，点开就能看到参数和输出。"),
        h("div", { class: "starters" }, STARTERS.map(([title, prompt]) =>
          h("button", { class: "starter", onclick: () => onStarter(prompt) },
            h("strong", {}, title), h("span", {}, prompt))))));
    },

    setReplaying(value) {
      replaying = value;
      if (!value) { pinned = true; scroller.scrollTop = scroller.scrollHeight; }
    },

    apply(event) {
      const handler = handlers[event.type];
      if (!handler) return;
      handler(event);
      stick();
    },
  };
}
