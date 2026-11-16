// 右侧运行面板：把 /api/state 的快照渲染成若干标签页。
import { api } from "../api.js";
import { clear, h, toast } from "../dom.js";

const TABS = [
  ["plan", "计划"], ["tasks", "任务"], ["team", "团队"], ["jobs", "后台"],
  ["knowledge", "知识"], ["tools", "工具"], ["logs", "日志"],
];
const TASK_STATUS = { pending: "等待依赖", in_progress: "进行中", completed: "已完成" };
const MEMORY_TYPE = { user: "用户偏好", feedback: "工作反馈", project: "项目事实", reference: "参考资料" };

const section = (title, hint, ...content) =>
  h("div", { class: "panel-section" }, h("h3", {}, title, hint ? h("small", {}, hint) : null), content);
const empty = (html) => h("p", { class: "empty", html });
const rows = (items) => h("ul", { class: "rows" }, items);

export function createInspector({ tabsRoot, bodyRoot, onChange, sessionId }) {
  let active = "plan";
  let state = null;
  let tools = [];
  const logs = [];
  const opened = new Map();          // 展开的详情：key -> 文本

  async function toggleDetail(key, load) {
    if (opened.has(key)) opened.delete(key);
    else {
      try { opened.set(key, await load()); } catch (error) { toast(error.message, true); return; }
    }
    render();
  }
  const detail = (key) => (opened.has(key) ? h("pre", { class: "detail" }, opened.get(key)) : null);

  const act = (promise, done) => promise.then(() => { if (done) toast(done); onChange(); })
    .catch((error) => toast(error.message, true));

  const panels = {
    plan() {
      const goal = state.goal;
      let goalBody;
      if (goal.active) {
        goalBody = h("div", { class: "goal-active" },
          h("p", {}, goal.condition),
          h("div", { class: "row-meta" }, `已评估 ${goal.iterations} 次${goal.last_reason ? ` · ${goal.last_reason}` : ""}`),
          h("button", { class: "btn btn-small", onclick: () => act(api.clearGoal(sessionId()), "目标已清除") }, "清除目标"));
      } else {
        const input = h("input", { placeholder: "例如：pytest 全部通过", "aria-label": "完成条件" });
        const submit = () => { if (input.value.trim()) act(api.setGoal(input.value.trim(), sessionId()), "目标已设定"); };
        input.addEventListener("keydown", (event) => { if (event.key === "Enter") submit(); });
        goalBody = [h("div", { class: "goal-form" }, input, h("button", { class: "btn btn-small", onclick: submit }, "设定")),
          h("p", { class: "row-meta" }, "设定后，模型每次想停下时，都会由另一个模型检查条件是否达成，没达成就继续干。")];
      }
      const todos = state.todos.length
        ? h("ul", { class: "checklist" }, state.todos.map((todo) =>
          h("li", { class: todo.status }, h("span", { class: "box" }), h("span", {}, todo.content))))
        : empty("还没有待办。遇到多步任务时，模型会先在这里列出计划。");
      return [section("完成目标", null, goalBody), section("待办清单", null, todos)];
    },

    tasks() {
      if (!state.tasks.length) return [section("任务图", null, empty("还没有任务。让模型“把工作拆成任务”，带依赖关系的任务会出现在这里。"))];
      return [section("任务图", `${state.tasks.length} 个`, rows(state.tasks.map((task) => {
        const label = task.ready ? "可开始" : TASK_STATUS[task.status] || task.status;
        const cls = task.status === "in_progress" ? "badge live" : task.status === "completed" ? "badge done" : task.ready ? "badge ready" : "badge";
        return h("li", {},
          h("div", { class: "row-head" }, h("span", { class: "row-title" }, task.subject), h("span", { class: cls }, label)),
          h("div", { class: "row-meta" }, h("code", {}, task.id),
            task.owner ? ` · 负责人 ${task.owner}` : "",
            task.blockedBy.length ? ` · 依赖 ${task.blockedBy.join(", ")}` : "",
            task.worktree ? ` · worktree ${task.worktree}` : ""),
          task.description ? h("div", { class: "row-meta" }, task.description) : null);
      })))];
    },

    team() {
      const mates = state.team.teammates;
      const requests = state.team.requests;
      return [
        section("队友", mates.length ? `${mates.length} 位在线` : null, mates.length
          ? rows(mates.map((mate) => h("li", {},
            h("div", { class: "row-head" }, h("span", { class: "row-title" }, mate.name),
              h("span", { class: mate.status === "working" ? "badge live" : "badge" }, mate.status)),
            h("div", { class: "row-meta" }, mate.task_id ? ["任务 ", h("code", {}, mate.task_id)] : "暂无任务",
              mate.plan !== "not_required" ? ` · 计划审批：${mate.plan}` : ""))))
          : empty("还没有队友。让模型“派一位队友”来并行处理任务。")),
        section("待处理的协议请求", null, requests.length
          ? rows(requests.map((request) => h("li", {},
            h("div", { class: "row-head" }, h("span", { class: "row-title" }, `${request.type}：${request.sender} → ${request.target}`),
              h("span", { class: "badge" }, request.status)),
            h("div", { class: "row-meta" }, h("code", {}, request.request_id), request.payload ? ` · ${request.payload}` : ""))))
          : empty("没有等待中的关机或计划审批请求。")),
      ];
    },

    jobs() {
      const jobs = state.crons.jobs;
      return [
        section("后台命令", null, state.background.length
          ? rows(state.background.map((task) => h("li", {},
            h("div", { class: "row-head" }, h("span", { class: "row-title mono" }, task.command),
              h("span", { class: task.status === "running" ? "badge live" : "badge" }, task.status)),
            h("div", { class: "row-meta" }, h("code", {}, task.id)))))
          : empty("没有正在运行的后台命令。")),
        section("定时任务", state.crons.queued ? `${state.crons.queued} 个待执行` : null, jobs.length
          ? rows(jobs.map((job) => h("li", {},
            h("div", { class: "row-head" }, h("span", { class: "row-title" }, job.prompt),
              h("button", { class: "btn btn-small", onclick: () => act(api.cancelCron(job.id), "已取消定时任务") }, "取消")),
            h("div", { class: "row-meta" }, h("code", {}, job.cron), job.recurring ? " · 重复" : " · 仅一次", job.durable ? " · 重启后保留" : ""))))
          : empty("没有定时任务。")),
      ];
    },

    knowledge() {
      return [
        section("长期记忆", state.memory.length ? `${state.memory.length} 条` : null, state.memory.length
          ? rows(state.memory.map((record) => h("li", {},
            h("div", { class: "row-head" },
              h("button", { class: "row-open row-title", onclick: () => toggleDetail(`memory:${record.filename}`, async () => (await api.memory(record.filename)).body) }, record.name),
              h("span", { class: "badge" }, MEMORY_TYPE[record.type] || record.type)),
            h("div", { class: "row-meta" }, record.description),
            detail(`memory:${record.filename}`))))
          : empty("还没有记忆。对话里出现长期有效的偏好或项目事实时，会被自动提取并保存到 <code>.memory/</code>。")),
        section("技能", state.skills.length ? `${state.skills.length} 个` : null, state.skills.length
          ? rows(state.skills.map((skill) => h("li", {},
            h("div", { class: "row-head" },
              h("button", { class: "row-open row-title", onclick: () => toggleDetail(`skill:${skill.name}`, async () => (await api.skill(skill.name)).content) }, skill.name)),
            h("div", { class: "row-meta" }, skill.description),
            detail(`skill:${skill.name}`))))
          : empty("没有可用技能。在 <code>backend/skills/&lt;名称&gt;/SKILL.md</code> 下添加后重启后端。")),
      ];
    },

    tools() {
      const connected = new Map(state.mcp.connected.map((server) => [server.name, server.tools]));
      return [
        section("MCP server", null, rows(state.mcp.available.map((name) => h("li", {},
          h("div", { class: "row-head" }, h("span", { class: "row-title mono" }, name),
            connected.has(name) ? h("span", { class: "badge ready" }, "已连接")
              : h("button", { class: "btn btn-small", onclick: () => act(api.connectMcp(name), `已连接 ${name}`) }, "连接")),
          connected.has(name) ? h("div", { class: "row-meta" }, `提供：${connected.get(name).join("、")}`) : null)))),
        section("工具池", `${tools.length} 个`, rows(tools.map((tool) => h("li", {},
          h("div", { class: "row-head" },
            h("button", { class: "row-open row-title mono", onclick: () => toggleDetail(`tool:${tool.name}`, async () => JSON.stringify(tool.input_schema, null, 2)) }, tool.name)),
          h("div", { class: "row-meta" }, tool.description),
          detail(`tool:${tool.name}`))))),
      ];
    },

    logs() {
      if (!logs.length) return [section("运行日志", null, empty("钩子、重试、压缩等内部过程会记录在这里。"))];
      return [section("运行日志", `最近 ${logs.length} 条`, h("div", { class: "logs" }, logs.map((entry) =>
        h("div", {}, h("time", {}, new Date(entry.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })),
          entry.agent !== "lead" ? h("span", { class: "who" }, entry.agent) : null, entry.text))))];
    },
  };

  function counts() {
    if (!state) return {};
    const openTodos = state.todos.filter((todo) => todo.status !== "completed").length;
    const running = state.background.filter((task) => task.status === "running").length;
    return {
      plan: openTodos ? [openTodos, state.todos.some((todo) => todo.status === "in_progress")] : null,
      tasks: state.tasks.length ? [state.tasks.length, false] : null,
      team: state.team.teammates.length ? [state.team.teammates.length, true] : null,
      jobs: running + state.crons.jobs.length ? [running + state.crons.jobs.length, running > 0] : null,
    };
  }

  function render() {
    const badge = counts();
    clear(tabsRoot);
    for (const [id, label] of TABS) {
      tabsRoot.append(h("button", {
        class: id === active ? "tab active" : "tab", role: "tab", "aria-selected": String(id === active),
        onclick: () => { active = id; render(); },
      }, label, badge[id] ? h("span", { class: badge[id][1] ? "count hot" : "count" }, badge[id][0]) : null));
    }
    clear(bodyRoot);
    if (!state) { bodyRoot.append(empty("正在连接后端…")); return; }
    bodyRoot.append(...panels[active]());
  }

  return {
    update(nextState, nextTools) {
      state = nextState;
      if (nextTools) tools = nextTools;
      render();
    },
    addLog(event) {
      logs.push(event);
      if (logs.length > 400) logs.shift();
      if (active === "logs") render();
    },
    render,
  };
}
