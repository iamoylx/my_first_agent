// backend.js —— 小满 Python 后端客户端（agent-server.py）
// 职责：健康检查 / 按需拉起后端 / /chat 对话 / /reset 会话重置。
// 复用桌面客户端同一后端 → 自动走【记忆 → 档案卡 → 工具 → 主动触发】全链路。
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");
const SERVER_SCRIPT = path.join(PROJECT_ROOT, "desktop-client", "agent-server.py");
const BACKEND_PORT = process.env.AGENT_PORT || "18789";
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

let spawned = null;

/** 后端健康检查（1.5s 超时） */
export async function backendHealth() {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 1500);
  try {
    const res = await fetch(`${BACKEND_URL}/health`, { signal: ctrl.signal });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(t);
  }
}

function findPython() {
  if (process.env.AGENT_PYTHON && process.env.AGENT_PYTHON.trim()) {
    return process.env.AGENT_PYTHON.trim();
  }
  return "python"; // 用户 PATH 上已有 Python 3.12
}

/**
 * 确保后端可用：
 *   - 已在运行（桌面客户端/其他实例）→ 直接复用；
 *   - 未运行 → 以隐藏窗口拉起 agent-server.py，注入 WECHAT_PUSH_URL 以便主动触发推到微信。
 * @returns {{ reused: boolean }}
 */
export async function ensureBackend({ log = console.log } = {}) {
  if (await backendHealth()) {
    log(`[backend] 已连接后端 ${BACKEND_URL}（复用）`);
    return { reused: true };
  }
  log(`[backend] 后端未运行，启动 ${SERVER_SCRIPT} ...`);
  const pushUrl =
    process.env.WECHAT_PUSH_URL?.trim() ||
    `http://127.0.0.1:${process.env.WECHAT_PUSH_PORT || "18888"}/push`;
  const child = spawn(findPython(), [SERVER_SCRIPT], {
    cwd: path.dirname(SERVER_SCRIPT),
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, WECHAT_PUSH_URL: pushUrl },
  });
  spawned = child;
  child.stdout.on("data", (d) => {
    if (process.env.WECHAT_BRIDGE_DEBUG === "1") log(`[backend] ${String(d).trim()}`);
  });
  child.stderr.on("data", (d) => log(`[backend:err] ${String(d).trim()}`));
  child.on("exit", (code) => log(`[backend] python 进程退出 code=${code}`));

  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (await backendHealth()) {
      log("[backend] 后端已就绪");
      return { reused: false };
    }
    if (child.exitCode !== null) {
      throw new Error("后端启动失败：python 进程已退出");
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("后端启动超时（60s）");
}

/** 调用 /chat（与桌面端同一入口，非流式） */
export async function chatBackend({ text, imageBase64, provider }) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 300_000);
  try {
    const res = await fetch(`${BACKEND_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        provider: provider || "deepseek",
        image_base64: imageBase64 || "",
      }),
      signal: ctrl.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
    return data;
  } finally {
    clearTimeout(t);
  }
}

/** 会话重置（对应微信 /clear 指令） */
export async function resetBackendSession() {
  const res = await fetch(`${BACKEND_URL}/reset`, { method: "POST" });
  return res.ok;
}
