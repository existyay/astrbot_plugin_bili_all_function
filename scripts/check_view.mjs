/**
 * 校验插件页面视图：内联脚本语法 + 用真实语言包与样例快照做一次渲染。
 *
 * 用法：
 *     node scripts/check_view.mjs
 *
 * 退出码 0 表示通过；非 0 时输出具体原因。
 * 该脚本只在本地/CI 使用，不参与插件运行。
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const HTML_PATH = join(ROOT, "views", "overview", "index.html");
const LOCALE_PATH = join(ROOT, ".astrbot-plugin", "i18n", "zh-CN.json");

const html = readFileSync(HTML_PATH, "utf8");

/** 提取内联 <script> 块（跳过带 src 的 bridge 引入）。 */
function extractInlineScripts(source) {
  const scripts = [];
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  let match;
  while ((match = re.exec(source)) !== null) {
    scripts.push(match[1]);
  }
  return scripts;
}

const scripts = extractInlineScripts(html);
const failures = [];

function check(condition, message) {
  if (!condition) {
    failures.push(message);
  }
}

check(scripts.length > 0, "视图未包含内联脚本");
check(
  html.includes('/api/plugin/page/bridge-sdk.js'),
  "视图缺少桥接 SDK 引入",
);
check(
  !/(?:src|href)\s*=\s*["']https?:\/\//i.test(html),
  "视图引用了外部资源",
);

// 1) 语法检查：任何一段内联脚本都必须能编译
for (const [index, code] of scripts.entries()) {
  try {
    new vm.Script(code, { filename: `inline-${index}.js` });
  } catch (error) {
    failures.push(`内联脚本 ${index} 语法错误：${error.message}`);
  }
}

// 2) 渲染冒烟：用最小 DOM 桩 + 真实语言包执行视图脚本
class FakeNode {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.className = "";
    this.hidden = false;
    this.listeners = {};
    this._text = "";
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this._text = value === undefined || value === null ? "" : String(value);
    if (this._text === "") {
      this.children = [];
    }
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }
}

const ids = new Map();
for (const id of [
  "metrics",
  "panels",
  "skeleton",
  "status",
  "status-text",
  "error",
  "footer",
  "title",
  "subtitle",
  "refresh",
  "auto",
  "auto-label",
]) {
  ids.set(id, new FakeNode());
}

const documentStub = {
  documentElement: { lang: "" },
  getElementById: (id) => ids.get(id) || null,
  createElement: (tag) => new FakeNode(tag),
};

const locale = JSON.parse(readFileSync(LOCALE_PATH, "utf8"));

function lookup(key) {
  return key.split(".").reduce((node, part) => (node == null ? undefined : node[part]), locale);
}

// 版本号从 metadata.yaml 读取，免得每次发版都要改这个冒烟脚本
const SAMPLE_VERSION = /^\s*version:\s*v?([0-9][^\s#]*)/m.exec(
  readFileSync(join(ROOT, "metadata.yaml"), "utf8"),
)?.[1];
if (!SAMPLE_VERSION) {
  throw new Error("无法从 metadata.yaml 解析 version");
}

const snapshot = {
  plugin: "astrbot_plugin_bili_all_function",
  display_name: "bili_all",
  version: SAMPLE_VERSION,
  uptime_seconds: 3725,
  healthy: true,
  poll_running: true,
  sessions: 2,
  subscriptions: 3,
  schema_version: 2,
  cookie_configured: true,
  cookie_masked: "SESSDATA=abcd***",
  features: { parse: true, video_info: true, download: false, llm_tool: true },
  poll: {
    interval_seconds: 300,
    concurrency: 3,
    dynamic_limit: 3,
    min_request_interval_seconds: 0.2,
  },
  stats: {
    http_requests: 128,
    http_failures: 1,
    http_silent: 2,
    poll_cycles: 41,
    poll_errors: 0,
    dynamic_pushes: 7,
    live_pushes: 2,
    downloads: 2,
    download_bytes: 3 * 1024 * 1024,
    send_failures: 0,
  },
};

const bridge = {
  ready: async () => ({ pluginName: snapshot.plugin, locale: "zh-CN", pageTitle: "B站插件概览" }),
  getContext: async () => ({ pluginName: snapshot.plugin, locale: "zh-CN" }),
  getLocale: async () => "zh-CN",
  getI18n: async () => locale,
  t: (key, fallback, vars) => {
    let text = lookup(key);
    if (typeof text !== "string") {
      text = fallback === undefined ? key : fallback;
    }
    if (vars) {
      for (const [name, value] of Object.entries(vars)) {
        text = text.split(`{${name}}`).join(String(value));
      }
    }
    return text;
  },
  onContext: () => {},
  apiGet: async () => ({ status: "ok", data: snapshot }),
  apiPost: async () => ({}),
};

const sandbox = {
  window: { AstrBotPluginView: bridge },
  document: documentStub,
  console,
  setInterval: () => 1,
  clearInterval: () => {},
  setTimeout,
  clearTimeout,
  Promise,
  Object,
  Number,
  Math,
  String,
  RegExp,
  Error,
  JSON,
};
sandbox.globalThis = sandbox;

for (const [index, code] of scripts.entries()) {
  try {
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox, { filename: `inline-${index}.js` });
  } catch (error) {
    failures.push(`内联脚本 ${index} 执行失败：${error.message}`);
  }
}

// 等待 load() 的 Promise 链完成
await new Promise((resolve) => setTimeout(resolve, 50));

const metricsText = ids.get("metrics").textContent;
const panelsText = ids.get("panels").textContent;
const footerText = ids.get("footer").textContent;
const statusClass = ids.get("status").className;

check(!ids.get("skeleton").hidden === false, "骨架屏未被隐藏");
check(ids.get("metrics").hidden === false, "指标区未显示");
check(ids.get("panels").hidden === false, "面板区未显示");
check(statusClass.includes("ok"), `状态徽章类别异常：${statusClass}`);
check(metricsText.includes("3"), "订阅数未渲染");
check(metricsText.includes("128"), "请求数未渲染");
check(panelsText.includes("SESSDATA=abcd***"), "Cookie 掩码未渲染");
check(!panelsText.includes("SESSDATA=abcdef"), "页面泄露了完整 Cookie");
check(panelsText.includes("300秒"), "轮询周期未渲染");
check(footerText.includes(SAMPLE_VERSION), "版本号未渲染");
check(ids.get("error").hidden !== false || !ids.get("error").textContent, "错误框不应显示");

if (failures.length > 0) {
  console.error("[失败] 插件页面校验失败：");
  for (const message of failures) {
    console.error(`  - ${message}`);
  }
  process.exit(1);
}

console.log("[通过] 插件页面校验通过（语法 + 渲染冒烟）");
