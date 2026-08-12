// agent.js —— 微信 SDK 的 Agent 实现：微信消息 → 小满后端 /chat → 回复微信。
// 复用桌面端同一会话与记忆：微信里说的话 = 桌面端小满记得的话（同一条记忆链路）。
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { chatBackend, resetBackendSession, ensureBackend } from "./backend.js";

const PROVIDER = process.env.WECHAT_PROVIDER?.trim() || "deepseek"; // deepseek | local

/** 读取已登录账号 owner 的 userId（用于默认只回复账号本人） */
export function loadOwnerUserId() {
  try {
    const stateDir =
      process.env.OPENCLAW_STATE_DIR?.trim() ||
      process.env.CLAWDBOT_STATE_DIR?.trim() ||
      path.join(os.homedir(), ".openclaw");
    const weixinDir = path.join(stateDir, "openclaw-weixin");
    const index = JSON.parse(fs.readFileSync(path.join(weixinDir, "accounts.json"), "utf-8"));
    const id = Array.isArray(index) && index[0];
    if (!id) return null;
    const acct = JSON.parse(
      fs.readFileSync(path.join(weixinDir, "accounts", `${id}.json`), "utf-8")
    );
    return acct.userId || null;
  } catch {
    return null;
  }
}

/**
 * 授权过滤：
 *   - WECHAT_ALLOW_USER="*"   → 回复所有人（群/陌生人）
 *   - WECHAT_ALLOW_USER="id1,id2" → 只回复列出的微信用户
 *   - 未设置（默认）           → 只回复账号本人（最安全）
 */
function buildAllowSet() {
  const cfg = (process.env.WECHAT_ALLOW_USER || "").trim();
  if (cfg === "*") return null; // null = 不限制
  if (cfg) {
    return new Set(cfg.split(",").map((s) => s.trim()).filter(Boolean));
  }
  const owner = loadOwnerUserId();
  return owner ? new Set([owner]) : new Set();
}

function guessMime(p) {
  const ext = path.extname(p).toLowerCase();
  return {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
  }[ext] || "image/png";
}

function imageToDataUrl(filePath, mimeType) {
  const buf = fs.readFileSync(filePath);
  const mime = mimeType && mimeType !== "image/*" ? mimeType : guessMime(filePath);
  return `data:${mime};base64,${buf.toString("base64")}`;
}

export function createAgent({ log = console.log } = {}) {
  const allow = buildAllowSet();
  if (allow) log(`[wechat] 只回复授权用户（${[...allow].length} 个）`);
  else log("[wechat] 回复所有用户（WECHAT_ALLOW_USER=*）");

  return {
    /** SDK 每收到一条微信消息调用一次 */
    async chat(request) {
      const { conversationId = "", text = "", media } = request;
      const label = conversationId ? conversationId.slice(0, 12) : "?";
      if (allow !== null && !allow.has(conversationId)) {
        log(`[wechat] 忽略非授权用户 ${label}`);
        return { text: "" }; // 空回复 → SDK 不发送任何消息
      }

      // 只发图不带文字：补默认提示词（后端要求非空 message）
      let userText = (text || "").trim();
      if (!userText && media && media.type === "image") userText = "请描述这张图片的内容";
      log(`[wechat] ← ${label} text=${userText.slice(0, 40)} media=${media?.type || "无"}`);
      let imageBase64 = "";
      if (media && media.type === "image") {
        try {
          imageBase64 = imageToDataUrl(media.filePath, media.mimeType);
          log(`[wechat] 图片已读取约 ${Math.round((imageBase64.length * 3) / 4 / 1024)}KB`);
        } catch (e) {
          log(`[wechat] 图片读取失败：${e.message}`);
        }
      }

      const start = Date.now();
      let data;
      try {
        data = await chatBackend({ text: userText, imageBase64, provider: PROVIDER });
      } catch (e) {
        // 自愈：后端可能被桌面重启带走 → 重新拉起后端再试一次
        log(`[wechat] 后端调用失败（${e.message}），尝试重启后端后重试…`);
        try {
          await ensureBackend({ log });
          data = await chatBackend({ text: userText, imageBase64, provider: PROVIDER });
        } catch (e2) {
          log(`[wechat] 后端重试仍失败：${e2.message}`);
          return {
            text: `爸爸，小满这边连脑子的时候出了点小问题（${String(e2.message).slice(0, 80)}），你让我缓一下下再试一次好不好～`,
          };
        }
      }
      const reply = (data.reply || "").trim() || "（小满刚才没说出话来，再问一次好不好？）";
      log(
        `[wechat] → ${label} 回复 ${reply.length} 字 模型=${data.model || "?"} 耗时=${((Date.now() - start) / 1000).toFixed(1)}s`
      );
      return { text: reply };
    },

    /** 微信里发 /clear 时 SDK 会调用 → 重置后端会话 */
    async clearSession(conversationId) {
      log(`[wechat] /clear 会话重置（${conversationId?.slice(0, 12) || "?"}）`);
      try {
        await resetBackendSession();
      } catch (e) {
        log(`[wechat] 会话重置失败：${e.message}`);
      }
    },
  };
}
