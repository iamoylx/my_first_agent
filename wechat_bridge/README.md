# 小满 × 微信 ClawBot 桥接（wechat_bridge）

让「小满」带着**同一份记忆**出现在你的个人微信里——微信里说的话 = 桌面端小满记得的话，
对话走的是同一个后端（`desktop-client/agent-server.py`），记忆、档案卡、工具、主动触发全部复用。

## 原理

- 微信官方 ClawBot 走 **iLink 协议**（`https://ilinkai.weixin.qq.com`，bot_type=3），本质是 ACP/OpenClaw 通道。
- 本桥使用 `weixin-agent-sdk`（由官方插件改造的桥接 SDK），**直接实现 `Agent.chat()` 接口**即可接入任意 AI 后端，无需自建 OpenClaw 网关：
  ```
  微信消息 → SDK 长轮询 → agent.chat(request) → 小满后端 POST /chat → 回复文本回微信
  ```
- 图片：微信图片由 SDK 自动下载解密，桥读取后以 base64 传给后端，走 qwen3-vl:8b 视觉 skill（DeepSeek 模式也能"看图"）。
- 主动触发：后端 `WeChatCarrier` 把提醒/主动消息 POST 到 `http://127.0.0.1:18888/push`，桥调用 `bot.sendMessage()` 推到微信。

## 首次使用

1. 确认本机 Node ≥ 22（本项目已用 v24 验证）。
2. 双击 `login.bat` → 终端打印二维码 → 用**微信扫一扫**扫码 → 手机上确认。
3. 双击 `start.bat` → 后台启动桥（日志在 `../logs/wechat-bridge.log`、`wechat-bridge.out.log`、`wechat-bridge.err.log`）。
4. 在微信里给小满发消息即可。**主动触发推送需要你先给 bot 发过至少一条消息**（SDK 的 context_token 机制，约 24h 有效）。

> 命令行方式：`node index.js login` / `node index.js start` / `node index.js logout`
> 登录状态保存在 `~/.openclaw/openclaw-weixin/`，logout 会清除。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `AGENT_PORT` | `18789` | 小满后端端口 |
| `WECHAT_PROVIDER` | `deepseek` | `deepseek` / `local`（本地 qwen3-vl:8b） |
| `WECHAT_ALLOW_USER` | 账号本人 | 只回复的微信用户 id，逗号分隔；`*` = 回复所有人 |
| `WECHAT_PUSH_PORT` | `18888` | 主动触发推送本地端点端口 |
| `WECHAT_PUSH_TOKEN` | `xiaoman` | 推送端点鉴权 token（与后端 `WECHAT_PUSH_TOKEN` 一致） |
| `AGENT_PYTHON` | `python` | 后端 Python 解释器（可选） |

## 说明与注意

- 后端未运行时桥会自动拉起（隐藏窗口）；桌面客户端运行时直接复用同一后端 → 微信与桌面共享同一会话与记忆。
- 微信内 `/clear` 可重置当前会话（记忆档案不受影响）。
- **主动触发推送到微信的前提**：桥自己拉起的后端会自动注入 `WECHAT_PUSH_URL`；
  若后端由桌面客户端启动，则需在系统环境变量里也设置 `WECHAT_PUSH_URL=http://127.0.0.1:18888/push`（与 `WECHAT_PUSH_TOKEN`）。
- 封号风险：这是微信官方 ClawBot（iLink）通道，非协议外挂；仍建议小号试用、遵守微信规范。

## 冒烟测试（可选）

不起微信也能验证「桥→后端」链路：

```powershell
# 1) 起一个隔离后端（临时端口 18800 + 临时记忆目录，不碰真实记忆）
$env:AGENT_PORT="18800"
$env:AGENT_MEMORY_DIR="tmp\smoke-mem"
$env:AGENT_TASK_DIR="tmp\smoke-tasks"
python desktop-client/agent-server.py
# 另开终端：
$env:AGENT_PORT="18800"; $env:WECHAT_PROVIDER="local"; $env:WECHAT_ALLOW_USER="*"
node wechat_bridge/smoke.mjs
```
