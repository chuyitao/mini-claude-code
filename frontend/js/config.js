// 后端地址。前后端分离部署时改这里，或在地址栏加 ?api=http://host:port 临时覆盖。
const override = new URLSearchParams(location.search).get("api");
export const API_BASE = (override || "http://127.0.0.1:8000").replace(/\/$/, "");
