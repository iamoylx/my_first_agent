# HANDOFF 交接文档 — 小满 Agent

> 生成时间：2026-08-14 05:10（Asia/Shanghai）
> 项目根：D:\document\Myprojects\学习\AGENT
> 交接对象：后续接手本项目的任何人 / 任何 AI Agent
> 配套文档：README.md（架构/接口/用法）、plan.md（进化路线）、requirement.txt（pip 依赖）

---

## 1. 项目一句话

「小满」是一个记忆增强型终端 / 桌面陪伴 Agent：DeepSeek（LangChain）+ 本地 Ollama 双模型，
三层记忆（STM / MTM / LTM 档案卡 v3），技能系统（搜索/代码/提醒/天气/健康/生图），主动触发、
MCP 框架、微信 ClawBot 桥接、Tauri 桌面客户端 + 桌宠模式。

---

## 2. 运行中的服务（生成文档时刻）

| 端口 | 服务 | PID（可能变化） | 说明 / 重启方式 |
|---|---|---|---|
| 18789 | agent-server.py 后端 | 31124 | 重启：终止 18789 端口 python 进程后，以隐藏窗口方式重新启动 desktop-client/agent-server.py（工作目录为项目根） |
| 11434 | Ollama（qwen3-vl:8b） | 3024 | 模型在 D:\ollama；断网本地模式依赖 |
| 3080 | dsh（DeepSeek Harness）桌面版 | 18728 | D:\dsh，桌面快捷方式「dsh 桌面版」→ WScript 运行 D:\dsh\start-desktop.vbs（静默启动 + 自动开浏览器） |
| 18888 | 微信桥（wechat_bridge） | 30972 | Node 系统版 24.12.0；开机自启注册表 HKCU Run 项 XiaomanWechatBridge；iLink 协议 + weixin-agent-sdk |
| 7860 | Forge SD（当前未运行） | — | 生图时由 local_generate_image 技能自动后台拉起（start-api.bat）；手动启：D:\DevTools\stable-diffusion-webui-forge\start-api.bat |

---

## 3. 已安装模型 / 工具 / 依赖清单

### 3.1 文本 + 视觉模型（Ollama，端口 11434）
- **qwen3-vl:8b**（6.2GB）—— 当前唯一 Ollama 模型（旧的 4b 已按用户要求删除）。
  - 用途：本地模式全功能（原生工具调用 + 看图）；DeepSeek 模式下发图时临时走它做视觉。
  - 模型目录 D:\ollama（data/models + Modelfile-qwen3vl8 + 下载脚本 download_qwen3vl8.py）。
  - 参数：OLLAMA_KEEP_ALIVE=10m（闲置自动卸载显存）。

### 3.2 生图模型（Stable Diffusion · WebUI Forge，独立 venv）
- Forge 根目录：D:\DevTools\stable-diffusion-webui-forge；自动启动脚本 start-api.bat（已加防 GitHub 卡死环境变量 + --api --medvram --no-half-vae --skip-python-version-check）。
- 模型（models\Stable-diffusion\）：
  - **Juggernaut-XI-byRunDiffusion.safetensors**（6.62GB，sha256 33e58e8668…）—— 写实向，当前默认。
  - **animagine-xl-3.1.safetensors**（6.46GB）—— 动漫向，已下载但未接入自动切换（见遗留问题）。
- API：http://127.0.0.1:7860/sdapi/v1/（txt2img / progress / options）。
- 显存：RTX 4070 Laptop 8GB，medvram 模式；生图时 Forge 约占 6.6GB。

### 3.3 dsh（DeepSeek Harness）
- D:\dsh：node v24.12.0 便携版 + @deepseek-ai/dsh rc.6 + 黑鲸图标（whale.ico）。
- 桌面快捷方式「dsh 桌面版」→ WScript 运行 D:\dsh\start-desktop.vbs（静默启动，无窗口，自动开 http://localhost:3080）。
- PATH 已把 D:\dsh 放到最前；旧 D:\DevTools\npm-global 副本已卸载，C 盘 Roaming\npm 残留已清。
- 注意：系统全局 Node 保留（微信桥依赖），勿删。

### 3.4 Agnes AI（免费云生图 / 生视频 / 对话）
- API Key：desktop-client/.agnes_key（gitignore）或环境变量 AGNES_API_KEY，一个 key 全功能。
- 对话 agnes-2.5-flash（OpenAI 兼容，支持工具 + 看图）/ 生图 agnes-image-2.1-flash / 生视频 agnes-video-v2.0（异步轮询）。
- 平台侧有内容审核，成人内容无法绕过生成（集成不做绕过）。

### 3.5 Python 全局依赖（C:\Users\绿\AppData\Local\Programs\Python\Python312）
运行必需（已装版本）：aiohttp 3.13.4 / langchain-core 1.4.8 / langchain-openai 1.3.3 / langchain-anthropic 1.4.7 / openai 2.16.0 / mcp 1.26.0 / tiktoken 0.13.0。
环境残留（项目当前不直接 import，历史功能遗留）：pillow 12.2.0 / requests 2.33.0 / beautifulsoup4 4.12.3 / httpx 0.28.1 / edge-tts 7.2.8 / pydub 0.25.1 / soundfile 0.14.0 / torch 2.5.1+cu124 / torchaudio / torchvision / numpy 2.5.2。
- 注意：torch 是用户全局装的 CUDA GPU 版，别动（之前 TTS / 本地视觉调试用过，用户明确在意）。
- TTS 功能已整体移除（模型已删），edge-tts / soundfile / pydub / torchaudio 已无使用方。

### 3.6 Node / 其他
- 系统 Node v24.12.0（微信桥用）；Tauri CLI @tauri-apps/cli（desktop-client）。
- dsh 便携 node 在 D:\dsh\node。

---

## 4. 关键路径一览

| 路径 | 角色 |
|---|---|
| D:\document\Myprojects\学习\AGENT | 项目根 PROJECT_ROOT |
| desktop-client\agent-server.py | 后端 HTTP/WS（:18789） |
| core\agent_core.py | headless 内核（LangChain：detect / stream_final / DSML 防御 / finalize） |
| memory\store.py | MemoryStore（v3 五板块档案卡） |
| memory_data\users\<id>\profile.json | 长期档案卡（本地记忆，不进 git，改动前先备份） |
| skills\ | 技能：basic / code / web_search / memory_tools / reminder_tools / weather / health_record / agnes_gen / local_imggen |
| active\ | 主动触发（scheduler / sources / policy / carriers） |
| mcp_bridge\ + mcp\config.json | MCP 客户端框架 |
| wechat_bridge\ | 微信 ClawBot 桥（Node，iLink） |
| desktop-client\src\ | 前端（index.html / app.js / pet.html / pet.js / styles.css） |
| desktop-client\src-tauri\ | Rust（commands.rs / pet_manager.rs） |
| generated\ | 生图输出（本地 Forge / Agnes 落盘，gitignore） |
| logs\ | 运行时日志（thinking-YYYYMMDD.jsonl 黑匣子、active、uploads） |
| D:\DevTools\stable-diffusion-webui-forge | Forge 生图引擎 |
| D:\ollama | Ollama 数据 + qwen3-vl:8b |
| D:\dsh | dsh 桌面版（node 便携 + rc.6） |
| D:\document\AGENT_archives\ | 历史记忆备份 / 旧测试归档 |
| 桌面快捷方式 | 「小满.lnk」→ 根目录 agent-desktop.exe；「dsh 桌面版.lnk」→ D:\dsh\start-desktop.vbs |

---

## 5. 最近完成的工作（2026-08-13 ~ 08-14）

1. 图片发送 / 本地看图提速：Ollama 视觉走 astream 慢 5~20 倍，stream_final 加 non_stream 参数，本地看图自动走 ainvoke 快路径（7.5s vs 155s）。commit f87e5e3。
2. dsh 迁移 D 盘 + 桌面版：自包含 node + rc.6，静默启动 + 黑鲸图标快捷方式。
3. 本地生图（Forge）全链路修通：
   - 亲密模式自动后台拉起 Forge（隐私绝不上云），温馨模式回退 Agnes；
   - 超时 300s→900s 防丢图；base64 保存 bug 修复（此前把 base64 文本当 .png 写入导致图裂）；
   - 默认参数 steps 20 / DPM++ 2M Karras / 768x768 / cfg 7.0 / 质量负向词，内容零限制。
   - 新增模型 Animagine XL 3.1（已下载，未接入自动切换）。
4. 系统提示词修订：工具铁律（失败最多重试 2 次后止损告知，禁止自行 netstat / tasklist / 搜文件排查）；生图路由（亲密→本地 Forge、温馨→Agnes）。
5. 对话污染清理：Forge 调试串污染历史（回复 62~80s）已重置（记忆档案保留，恢复 3.4s）。

---

## 6. 遗留问题 / 已知 bug（优先处理）

1. **Animagine XL 3.1 未接入自动切换**：模型已就位，但 skills/local_imggen/skill.py 仍默认 Juggernaut；动漫风请求不会自动切 Animagine。需：技能加「动漫/写实」路由（按 prompt 关键词判断）或对话指令手动切（改 options.sd_model_checkpoint）。
2. **显式 NSFW 能力弱**（用户刚在确认）：Juggernaut XI 只能 softcore（作者自述训练集裸体少、male genitalia 难出）；Animagine 3.1 官方 SFW。要硬核需 Pony Diffusion V6 XL（动漫，6.9GB，8G 显存可跑）或 Juggernaut + 专用 LoRA（写实）。方向待用户拍板。
3. **Forge 内存吃满**：生图时 Forge ~6.6GB + 后端等，16GB 整机易崩。建议：空闲自动卸载模型 / 一键释放内存快捷方式（用户未确认）。
4. **聊天与生图互斥**：生图 2~3 分钟内聊天阻塞等待。可选改后台异步（先回复再推送），需后端任务队列 + 前端推送改造（用户未拍板）。
5. **微信桥稳定性**：历史反馈过「暂无法连接 openclaw」、换微信号后不回消息（可能要重新扫码 / 重启桥进程）；iLink 通道稳定性需实测。
6. **对话历史污染风险**：agent 在对话里跑 run_command 排查（netstat / dir 等）会拖慢回复并污染 thinking 日志；提示词已加铁律，需回归确认不再发生。
7. **图片发送回归**：历史 bug「图片卡在输入框 / 本地读不到图」已修（non_stream + base64），需在客户端 + 微信桥两侧回归。
8. **模型切换残留文案**：本地模型选项对话框/切换文本曾过期（已修过，改动 UI 时注意同步）。

---

## 7. 未完成事项（按优先级，详见 plan.md）

| 优先级 | 事项 | 说明 |
|---|---|---|
| P0 | 生图模型增强 | Animagine 自动切换；Pony V6 XL / 写实 LoRA（用户待决策）；Forge 内存管理；异步生图 |
| P1 | B2 健康数据 | 等用户买表；推荐 华为手环 9（约190-210元）+ huawei-health-mcp（开源，华为健康云 API）；备选华米 Amazfit + Zepp MCP。用户确认后需整理：注册开发者账号 → 开通 Health Kit → MCP server 配置 |
| P1 | C1 微信对话入口 | 企微应用消息 / 服务号 / 个人微信（ClawBot 已通，需稳定性完善） |
| P2 | C2 后台常驻服务化 | 开机自启 + 通知，桌面为前台 UI |
| P2 | C3 多端界面 | 手机端 / Web 面板（会话、档案、健康可视化） |
| P2 | MCP 启用项 | browser-use（需 playwright install chromium）、音乐（扫码登录）、日历（飞书 OAuth）——模板就绪配好即用 |
| P3 | 小增强 | winotify Toast、SSE 真流式思考、记忆 value 级模糊相似索引、桌宠气泡点击联动主窗口 |
| P3 | 性能优化 | 记忆上下文按板块注入、模型调用缓存、MCP 连接池、图片前端压缩、前端虚拟滚动、日志轮转、启动并行化（详见 plan.md 十一） |

---

## 8. 技术备忘 / 坑（务必先看）

1. 命令拦截很敏感：PowerShell 的 env 变量写法（美元符号前缀）、start 加 powershell 的 -C 参数、百分号包裹变量等均可能被 block（skills/code_tools/skill.py 的 _DENY 正则）。写文件用 Python 或 System.IO 写文件 API，启动隐藏进程用 PowerShell 的 Start-Process（带隐藏窗口参数），PowerShell 脚本必须 UTF-8 with BOM（否则中文路径乱码）。
2. 镜像：pypi 清华 https://pypi.tuna.tsinghua.edu.cn/simple；GitHub 走 ghproxy.net / gitee 镜像。
3. 隐私约定：亲密模式（档案 rule_intimacy_level=intimate）发的图 / 生图只走本地 Forge，绝不上云；温馨模式日常图才给 Agnes。
4. 记忆铁律：memory_data/ 绝不许丢失 / 污染。任何改动先备份，改完 md5 对比；测试一律 base_dir=tempfile 隔离；历史备份在 D:\document\AGENT_archives\。
5. 称呼用户「你/您」，绝不叫「爸爸」（用户明确踩过坑）。
6. 破甲 / 亲密模式注入由用户自行维护（用户自己改的提示词行，报错已由用户自行跑通）；agent 侧只保证生图路由与隐私，不负责维护注入文本。
7. Forge 禁用扩展不要启用：forge_legacy_preprocessors_disabled / sd_forge_controlnet_disabled 会卡 GitHub 下载；STABLE_DIFFUSION_COMMIT_HASH=c12d960d1ee4f9134c2516862ef991ec52d3f59e 已写入 start-api.bat。
8. git 现状：main 分支，origin = git@github.com:iamoylx/my_first_agent.git（SSH）；工作区应保持干净。
