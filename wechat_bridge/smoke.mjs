// smoke.mjs —— 端到端冒烟测试：验证 桥→后端 链路（需先起一个隔离后端）
import { createAgent } from "./agent.js";

const agent = createAgent({ log: (m) => console.log(m) });
const resp = await agent.chat({
  conversationId: "wxtestuser",
  text: "你好小满，简单介绍一下你自己",
});
console.log("=== REPLY ===");
console.log(resp.text || "(empty)");
console.log("=== REPLY_LEN ===", (resp.text || "").length);
process.exit(0);
