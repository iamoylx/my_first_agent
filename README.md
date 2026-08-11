# AGENT · 记忆增强型终端 / 桌面 Agent

基于 DeepSeek Function Calling 的对话 Agent，重点练习「工具调用」与「记忆系统」。练手性质，架构以简单 / 可理解 / 低成本为先。长期记忆以结构化档案卡（JSON）为主，生产级才上向量库 + RAG。

> **记忆机制与数据彻底分离**：`memory/` 是纯机制代码（硬板接口），`memory_data/` 是纯本地记忆数据（硬盘），二者互不混杂。
>
> **档案卡 v3 板块化**：长期记忆按 5 个板块分类（用户身份 / Agent设定 / 用户偏好 / 行为规定 / 主动触发），写入自动分类 + 规范命名 + 相似去重；客户端档案卡支持板块折叠与直接编辑（`scripts/migrate_profile_v3.py` 完成 v2→v3 无丢失迁移）。
>
> **进化路线见 `plan.md`**：阶段0「思考可视化」✅ + A1「主动触发」✅ + A2「生活 skill」✅ + B1「MCP 框架」✅ + **A3「本地模型」✅**（Ollama + gemma3:4b 多模态，图片消息走本地视觉、文本走 DeepSeek 双模型）→ 阶段B2（健康数据）→ 阶段C（微信/常驻化）。客户端已支持**图片附件**并真正「看图」。

---

## 一、整体框架

双前端共享同一套核心：

- **终端前端**：`AGENT.py`（`on_token = print`）
- **桌面前端**：`desktop-client`（Tauri v2，前端 `invoke` → Rust → `agent-server.py :18789` → `core`）

两者都调用 `core.agent_core.process_turn`，都通过 `MemoryStore` 读写记忆，都聚合 `skills` 工具，行为一致。

**数据流**：

```
用户输入 → process_turn → detect_tool_call(DeepSeek) → [DSML 防御] → 工具循环(tool_map)
        → stream_final(DeepSeek) → 缓冲 + prune + autosave → 退出时 finalize(归档 + 离线抽取档案卡)
        └─ on_thinking 轨迹（记忆/工具/防御/生成）→ /chat 返回 thinking → 前端「💭 思考过程」折叠块

> **模型层基于 LangChain**：`core/agent_core.py` 用 `langchain_openai.ChatOpenAI`（DeepSeek 走 OpenAI 兼容端点）+ `bind_tools` 检测工具调用 + `astream` 流式输出，消息统一走 `langchain_core.messages`。保留自定义工程逻辑：DSML 防御、`mem` 注入、工具循环上限、增量抽取缓冲 / prune / autosave / finalize。
```

**记忆三层**：

| 层 | 存储 | 接口 | 状态 |
|----|------|------|------|
| STM 短期 | 内存 `messages` | `token_window.prune()` | 已实现 |
| MTM 中期 | `memory_data/users/<id>/sessions/` | `MemoryStore` 会话接口 | 已实现 |
| LTM 长期 | `memory_data/users/<id>/profile.json` | `MemoryStore` 档案接口 | 已实现 |

---

## 二、目录结构（扁平 · 一模块一目了然）

```
AGENT.py                      # 终端版主程序（on_token = print）
commands.py                   # 终端版命令（/recall /sessions /profile ...）
core/                         # agent 核心（LangChain 版，无 UI）
└─ agent_core.py              #   process_turn / stream_final / DSML 防御 / build / finalize
memory/                       # 记忆机制（纯代码，不含数据）
├─ store.py                   #   统一记忆层 MemoryStore（schema v3：5 板块档案卡）
├─ profile.py                 #   板块分类/命名规范/抽取/合并/渲染
├─ sessions.py / token_window.py
active/                       # 主动触发模块（A1）
├─ scheduler.py               #   周期 tick + 冷却去重 + 载体广播
├─ sources.py                 #   Clock/Idle/Reminder 事件源（作息/健身/牛奶/天气/空闲/任务）
├─ policy.py                  #   全屏免打扰
├─ carriers.py                #   桌宠WS/日志/企微推送
└─ config.py
skills/                       # 技能（注册即生效）：basic/code/web_search/memory_tools/reminder_tools/weather/health_record
mcp_bridge/                   # MCP 客户端框架（B1）：配置式连接外部 MCP server
mcp/                          # MCP 配置（config.json）
scripts/                      # 工具脚本 + 自检回归
├─ migrate_profile_v3.py      #   记忆 v2→v3 迁移
├─ migrate_memory_v2.py
└─ selfcheck/                 #   回归测试（python scripts/selfcheck/test_*.py）
desktop-client/               # Tauri v2 桌面客户端（主窗口 + 桌宠）
├─ agent-server.py            #   本地 HTTP/WS 后端（:18789），桥接 core/active/mcp
├─ src/                       #   前端（index.html/app.js/pet.html/pet.js/styles.css）
└─ src-tauri/                 #   Rust（main.rs/commands.rs/pet_manager.rs）
memory_data/                  # ★本地记忆数据（运行时，不进 git）
task_data/                    # 提醒任务数据（运行时，不进 git）
logs/                         # 运行时日志（thinking/active/uploads）
素材/                         # 桌宠素材（不进 git）
README.md / plan.md / requirement.txt
agent-desktop.exe / WebView2Loader.dll / agent-idle.ico   # 根目录发布产物
```
## 三、运行

### 终端版

```powershell
cd "D:\document\Myprojects\学习\AGENT"
python AGENT.py
```

依赖与环境变量见 `requirement.txt`。

### 桌面版（desktop-client）

前置：Rust + Cargo（MSVC 工具链）、Node + npm、系统 WebView2 运行时（本机依赖 Windows 自带 EdgeWebView）。

```powershell
cd desktop-client
npm install                 # 装 Tauri CLI（或 npx tauri）
npx tauri build             # 在 src-tauri/target/release 产出 agent-desktop.exe
# 双击 exe 启动；它会自动拉起 agent-server.py，聊天走 Rust invoke → :18789 → core
```

> 构建与 WebView2 黑屏规避细节见 `desktop-client/README.md`「常见问题」及项目记忆（`MEMORY.md` 桌面客户端小节）。

---

## 四、模块接口定义与必要路径

### 4.1 统一记忆层 `MemoryStore`（`memory/store.py`）

主程序只与它打交道。实例化：`MemoryStore(user_id=os.getenv("AGENT_USER_ID","default"))`，`base_dir` 默认指向项目根 `memory_data/`（测试可传 `base_dir=tempfile` 隔离）。

| 类别 | 方法 | 说明 |
|------|------|------|
| **写入** | `autosave(messages)` | 每轮静默落盘 `current.json` |
| | `add_profile_item(key,value,type,conf,category)` | 新增档案项（自动分类 + key 规范化） |
| | `update_profile_item(key,value,category,conf)` | 编辑档案项（内容/板块/置信度） |
| | `save_session(messages, session_id=None)` | 退出时完整保存（current + 时间戳归档） |
| | `update_profile(extracted)` | 合并新事实（latest-wins），仅变更才落盘 |
| | `save_summary` / `buffer_round` / `reset_extract_buffer` / `extract` | 摘要、增量抽取缓冲与离线抽取 |
| **读取** | `load_last_session()` / `load_profile()` / `load_session(id)` / `list_sessions()` / `profile_context()` / `load_summary()` / `get_recent_summary()` / `get_last_session_date()` | 续聊 / 读档案 / 读归档 / 渲染 / 摘要锚点 |
| **检索** | `search_profile(kw)` / `search_sessions(kw)` / `retrieve(query)` | 档案 + 会话跨层检索 |
| | `list_profile_items()` | 按 5 板块分组返回（供管理页展示） |
| **清理** | `cleanup_expired(days=30)` / `delete_session(id)` | 过期清理（`current` 永不可删） |

**维度隔离**：`user_id` → `memory_data/users/<id>/`；`session_id` → `sessions/<id>.json`。

### 4.2 Headless 核心 `core.agent_core`

对前端只暴露：

- `build_initial_messages(mem) -> (messages, system_msg)`
- `process_turn(*, messages, user_text, mem, tools, tool_map, api_key, on_token=None, on_thinking=None) -> messages`（`on_thinking(ev)` 每步思考轨迹触发：记忆/工具/防御/生成）
- `finalize(messages, mem, api_key) -> changed`（保存会话 + 离线抽取档案卡）
- `detect_tool_call` / `stream_final`（DSML 防御在内部）

`core` 不 import 任何具体技能，只通过回调 `on_token` 发事件。

### 4.3 技能 `skills`（自包含 + 注册即生效）

每个技能对外暴露 `TOOLS`（给 LLM 的 schema）与 `TOOL_MAP`（可调函数）。`skills.collect_tools(*pairs)` 聚合成统一清单，主循环分发逻辑零改动。

当前注册（15 个）：`web_search` / `get_current_time` / `calculator` / `read_file` / `list_dir` / `search_files` / `search_content` / `run_command` / `write_memory` / `create_reminder` / `list_reminders` / `delete_reminder` / `get_weather` / `record_health` / `health_records`。

- `reminder_tools`：对话中「提醒我明天下午3点开会」→ `create_reminder` 存任务 → `ReminderSource` 到点主动提醒（任务存独立 `task_data/`，与记忆完全分离）。
- `weather`：`get_weather` 查城市天气（Open-Meteo，免费无需 Key，支持中文城市名 + 未来3天预报）。
- `health_record`：`record_health` / `health_records` 记录睡眠/体重/步数/心率/饮水/心情 → 记忆 events（为健康 MCP 打底）。

- `web_search`：URL 写死 `https://api.tavily.com/search`，Key 仅读 `TAVILY_API_KEY`（防 SSRF / 投毒）。
- `code_tools`：`run_command` 经 `_DENY` 正则拦截 `rm -rf` / `format` / `shutdown` / `sudo` / `curl|sh` 等高危指令；文件类工具相对路径按项目根解析。

### 4.4 终端命令 `commands.py`

- `is_command(text) -> (name, arg)` 识别
- `run_command(name, arg, mem, messages, system_msg) -> (messages, handled)` 执行

命中命令跳过本轮 LLM。命令：`/help` `/sessions` `/profile` `/recall <kw>` `/load <id>` `/summary <id> [--llm]` `/forget <id>` `/cleanup [days]` `/exit`。

### 4.5 桌面端 HTTP 桥 `agent-server.py`（:18789）

| 路由 | 方法 | 作用 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/ws` | GET | WebSocket：主动触发消息推送（桌宠气泡 / 主窗口），只下发 |
| `/mcp/tools` | GET | 查看已注册的 MCP 工具（B1 调试/管理） |
| `/chat` | POST | 非流式回复 `{reply, history_len, thinking}`；可带 `image_base64` 图片（保存到 `logs/uploads/`，正文注入路径提示，为多模态预留） |
| `/chat/stream` | POST | SSE 流式（`{"token"}` 正文 + `{"type":"thinking"}` 轨迹事件） |
| `/history` | GET | 当前会话历史 |
| `/reset` | POST | 重置会话 |
| `/profile/items` | GET | 档案卡 5 板块分组（v3） |
| `/profile/toggle` | POST | 生效/停用一条档案项 |
| `/profile/update` | POST | 编辑一条档案项（内容/板块/置信度） |
| `/profile/delete` | POST | 删除一条档案项（先记 discarded 审计） |
| `/profile/add` | POST | 新增一条档案项（自动分类 + key 规范化） |
| `/assets/{path}` | GET | 素材代理（`PROJECT_ROOT/素材/`） |

Rust 侧 Tauri Commands（前端 `invoke` 名）：`start_python_server` / `stop_python_server` / `check_server_health` / `send_chat`（可带 `image_base64`） / `get_history` / `reset_chat_session` / `get_profile_items` / `profile_add` / `profile_update` / `profile_toggle` / `profile_delete` / `set_pet_state` / `switch_to_pet_mode` / `switch_to_main_window` / `show_pet` / `hide_pet` / `set_pet_position` / `get_app_info` / `exit_app` / `mark_boot`。

### 4.6 必要路径一览

| 路径 | 角色 | 说明 |
|------|------|------|
| `D:\document\Myprojects\学习\AGENT` | `PROJECT_ROOT` | 所有相对路径基准；`agent-server.py` 把其父目录加入 `sys.path` |
| `<root>/memory_data/` | `DATA_DIR` | 本地记忆根（`memory/store.py`：`DATA_DIR = <root>/memory_data`） |
| `<root>/memory_data/users/<id>/profile.json` | LTM | 长期档案卡 |
| `<root>/memory_data/users/<id>/sessions/` | MTM | `current.json` + 时间戳归档 + `<id>.summary.json` |
| `<root>/memory_data/profile.json`、`/sessions/` | 旧扁平回退 | 只读，绝不写入 |
| `<root>/素材/` | 桌宠立绘 | 被 `.gitignore` 忽略 |
| `<root>/desktop-client/src-tauri/` | Rust 后端 | `cargo build` 产出 `agent-desktop.exe` |
| `<root>/desktop-client/src/` | 前端 | `index.html` / `app.js` / `pet.html` / `pet.js` |

---

## 五、环境变量与依赖

完整清单见 `requirement.txt`。要点：

- **必填**：`DEEPSEEK_API_KEY`（缺失启动即报错）。
- **可选**：`TAVILY_API_KEY`（联网搜索）、`AGENT_USER_ID`（记忆隔离，默认 `default`）、`AGENT_PORT`（桌面后端端口，默认 `18789`）、`WECOM_WEBHOOK_URL`（企业微信群机器人 webhook，配置后主动消息同步推送企微）。
- **A3 本地模型**：`AGENT_LOCAL_BASE`（默认 `http://127.0.0.1:11434/v1`）、`AGENT_LOCAL_MODEL`（默认 `gemma3:4b`）；图片消息自动走本地视觉模型；`AGENT_LOCAL_TEXT=1` 时纯文本也走本地（工具调用会变弱）。
- **pip 依赖**：`aiohttp`（必需）；`tiktoken`（可选，更准的 token 估算）。

---

## 六、设计原则（踩坑点）

1. **工具消息保护**：`system` + 含 `tool_calls` 的 `assistant` + `tool` 角色永不删。
2. **状态复写 latest-wins**：档案卡按事实类型 key 去重，新覆盖旧，禁低置信度覆盖。
3. **写读分离**：抽取 / 整合放会话结束离线任务，不在用户轮次同步做。
4. **上下文预算竞争**：四块抢同一 token 预算，档案卡常驻（≤15%），MTM / 向量按需注入。
5. **token 滑动窗口**替代条数截断。

---

## 七、本地记忆保护约定（★铁律）

记忆数据（`memory_data/`）绝不许丢失或污染。任何改动前先备份，改完 md5 对比。测试一律用 `base_dir=tempfile` 隔离，**严禁**针对真实用户记忆跑训练 / 测试。

> **历史备份归档**：历次记忆备份与旧版测试已移出项目，统一存放在 `D:\document\AGENT_archives\`（需要时取回；项目目录保持干净）。

---

## 八、开发路线

| 阶段 | 内容 | 状态 |
|------|------|------|
| 0 | 思考过程可视化（Thinking Trace：后端轨迹 + 前端折叠灰字 + 日志落盘） | 已完成 |
| A1 | 主动触发机制（作息/健身/空闲关心 → 桌宠气泡+主窗口；全屏免打扰；MCP/微信接口预留） | 已完成 |
| A2 | 生活 skill（reminder/todo 提醒任务 + weather 天气 + health_record 健康记录） | 已完成 |
| A2.5 | 客户端图片附件（输入框选图/粘贴→base64→后端保存 logs/uploads） | 已完成 |
| A3 | 本地模型接入（Ollama + gemma3:4b 多模态：图片走本地视觉、文本走 DeepSeek 双模型；detect 对不支持工具的模型自动降级） | 已完成 |
| 3.13 | 档案卡 v3 板块化（5 板块 + 自动分类/命名规范/去重 + 前端折叠/编辑 + 无丢失迁移） | 已完成 |
| B1 | 通用 MCP 框架（mcp_bridge 配置式 client + 工具动态注册 + /mcp/tools）+ Obsidian 笔记接入 + 企业微信推送 Carrier | 已完成 |
| 1 | STM：token 滑动窗口 `prune()` | 已完成 |
| 2 | LTM 结构化档案卡 + 离线抽取 | 已完成 |
| 3 | MTM 跨重启续聊 | 已完成 |
| 3.5 | 统一记忆层 `MemoryStore` | 已完成 |
| 3.6 | 终端命令 | 已完成 |
| 3.7 | 增量抽取 + `/summary --llm` | 已完成 |
| 3.8 | 启动自动注入摘要锚点 + `/cleanup` | 已完成 |
| 3.9 | `code_tools` 工具技能 | 已完成 |
| 3.10 | 记忆正确性修复（recency 倒序 + 核心人格常驻 + 日期注入 + 输出清洗） | 已完成 |
| 3.11 | headless 内核 `core.agent_core`（含 DSML 防御） | 已完成 |
| 3.12 | 记忆机制 / 数据分离 | 已完成 |
| 4 | LTM 向量库（可选 Phase 2） | 可选 |

---

## 九、已知技术债务

- 长会话仍靠 `prune()` 控制窗口；`/summary --llm` + `/load` 注入锚点是当前连续性方案。
- 抽取已改为增量（`buffer_round` 累积新增轮次，会话结束只发新增）。
- `memory/` 包内无死代码。
- 桌面客户端已清理：诊断文件改写入系统临时目录、`find_python` 不再硬编码用户路径（支持 `AGENT_PYTHON` / PATH / 托管目录 glob）、`pet_manager` 状态机已接线（`set_pet_state` 命令 + `pet://state-changed` 事件）、桌面退出会触发 `finalize`（时间戳归档 + LTM 档案抽取）。
