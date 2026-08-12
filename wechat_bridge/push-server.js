// push-server.js —— 本地 HTTP 端点，接收后端主动触发消息并转发到微信。
// 后端 WeChatCarrier 把主动消息 POST 到 http://127.0.0.1:18888/push，
// 这里调用 bot.sendMessage() 推送到用户微信。
import http from "node:http";

export function startPushServer({
  bot = null,
  log = console.log,
  port = Number(process.env.WECHAT_PUSH_PORT || "18888"),
  token = process.env.WECHAT_PUSH_TOKEN || "xiaoman",
} = {}) {
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    if (req.method !== "POST" || url.pathname !== "/push") {
      res.writeHead(404, { "Content-Type": "text/plain" });
      res.end("not found");
      return;
    }
    // 鉴权：与后端 WECHAT_PUSH_TOKEN 保持一致
    const auth = req.headers["x-push-token"] || "";
    if (token && auth !== token) {
      res.writeHead(401, { "Content-Type": "text/plain" });
      res.end("unauthorized");
      return;
    }
    let body = "";
    try {
      for await (const chunk of req) body += chunk;
      const msg = JSON.parse(body || "{}");
      const text = String(msg.text || "").trim();
      if (!text) {
        res.writeHead(400, { "Content-Type": "text/plain" });
        res.end("empty text");
        return;
      }
      log(`[push] 主动触发 → ${text.slice(0, 40)}`);
      if (!bot) {
        res.writeHead(503, { "Content-Type": "text/plain" });
        res.end("bot not ready");
        return;
      }
      try {
        await bot.sendMessage(text);
        res.writeHead(200, { "Content-Type": "text/plain" });
        res.end("ok");
      } catch (e) {
        // 常见原因：还没收到过微信消息（context_token 未缓存），或登录过期
        log(`[push] 发送失败：${e.message}`);
        res.writeHead(502, { "Content-Type": "text/plain" });
        res.end(String(e.message));
      }
    } catch (e) {
      res.writeHead(400, { "Content-Type": "text/plain" });
      res.end("bad request");
    }
  });

  server.listen(port, "127.0.0.1", () => {
    log(`[push] 推送服务 http://127.0.0.1:${port}/push 已启动`);
  });
  return server;
}
