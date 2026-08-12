// index.js —— 小满 x 微信 ClawBot 桥接入口
//   node index.js login   → 微信扫码登录（打印二维码）
//   node index.js logout  → 退出登录
//   node index.js start   → 启动微信 bot（自动拉起后端）+ 主动触发推送服务
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { start, login, logout, isLoggedIn } from "weixin-agent-sdk";
import { createAgent } from "./agent.js";
import { ensureBackend } from "./backend.js";
import { startPushServer } from "./push-server.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const LOG_DIR = path.join(__dirname, "..", "logs");
fs.mkdirSync(LOG_DIR, { recursive: true });

function log(msg) {
  const line = `[${new Date().toLocaleString("zh-CN", { hour12: false })}] ${msg}`;
  console.log(line);
  try {
    fs.appendFileSync(path.join(LOG_DIR, "wechat-bridge.log"), line + "\n", "utf-8");
  } catch {}
}

const cmd = process.argv[2] || "start";

if (cmd === "login") {
  log("开始微信扫码登录…（约 8 分钟内有效，扫完在手机确认即可）");
  try {
    const accountId = await login({ log });
    log(`✅ 登录成功 account=${accountId}`);
  } catch (e) {
    log(`❌ 登录失败：${e.message}`);
    process.exit(1);
  }
} else if (cmd === "logout") {
  logout({ log });
} else if (cmd === "start") {
  if (!isLoggedIn()) {
    log("尚未登录微信：请先双击 login.bat 扫码登录，再运行 start.bat");
    process.exit(1);
  }
  // 1) 确保后端（复用桌面后端，或自动拉起）
  await ensureBackend({ log });

  // 2) 启动微信 bot
  const agent = createAgent({ log });
  const bot = start(agent, { log });
  log("🚀 微信 bot 已启动，等待消息…（在微信里给小满发消息即可）");

  // 3) 主动触发推送服务（后端 WeChatCarrier → 微信）
  const pushPort = Number(process.env.WECHAT_PUSH_PORT || "18888");
  const pushToken = process.env.WECHAT_PUSH_TOKEN || "xiaoman";
  startPushServer({ bot, log, port: pushPort, token: pushToken });

  // 3.5) 运行时注册：让后端主动触发能推到微信（幂等；复用桌面后端时也生效）。
  //     后端每次重启都会丢失运行时注册的 carrier → 每 5 分钟幂等重注册一次（静默自愈）。
  let carrierRegistered = false;
  const registerCarrier = async () => {
    try {
      const res = await fetch(`http://127.0.0.1:${process.env.AGENT_PORT || "18789"}/carriers/wechat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          push_url: `http://127.0.0.1:${pushPort}/push`,
          token: pushToken,
        }),
      });
      if (res.ok) {
        if (!carrierRegistered) log("[push] 已向后端注册微信推送 carrier");
        carrierRegistered = true;
      } else if (carrierRegistered) {
        log(`[push] 推送注册失效（HTTP ${res.status}），等待后端恢复…`);
        carrierRegistered = false;
      }
      return res.ok;
    } catch (e) {
      if (carrierRegistered) {
        log(`[push] 推送注册暂时失效（${e.message}）`);
        carrierRegistered = false;
      }
      return false;
    }
  };
  (async () => {
    await registerCarrier();
    const timer = setInterval(registerCarrier, 300_000);
    timer.unref?.();
  })();

  // 4) 保持进程存活；收到退出信号时退出（后端按需保留）
  const shutdown = () => {
    log("收到退出信号，微信桥关闭");
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  await bot.wait();
} else {
  console.log("用法: node index.js <login|logout|start>");
  process.exit(1);
}
