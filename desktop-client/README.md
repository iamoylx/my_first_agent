# Agent Desktop — 桌面客户端

基于 **Tauri v2** 的独立桌面应用，封装 Python Agent 核心，支持**主窗口 + 桌宠悬浮**双模式。

> **模式参考**：桌宠交互参照 [OpenAI Codex Pets](https://openai.com/index/codex/) —— 全局悬浮、状态可视化（思考/完成/失败）、可拖拽可点击。

## 快速启动

### 前置条件（本机已全部就绪）

| 依赖 | 状态 | 路径 |
|------|------|------|
| Rust | ✅ 已装 | `D:\DevTools\rust\.cargo\bin` |
| MSVC BuildTools | ✅ 已装 | `D:\DevTools\vS_buildtool` |
| Windows SDK | ✅ 已装 | `D:\DevTools\WindowsKits` (或 C 盘) |
| Python 3.13 | ✅ 已装 | 托管版本（PATH 已配置） |
| API Key | ✅ 已设 | 用户级永久变量 `DEEPSEEK_API_KEY` |
| Node.js / Tauri CLI | ✅ 已装 | `desktop-client/node_modules` |

### 启动方式

```bash
# 方式一：根目录 exe（推荐日常使用，已配置桌面快捷方式）
# 根目录的 agent-desktop.exe（+ WebView2Loader.dll）即最新构建版，
# 桌面快捷方式「Agent 桌宠」指向它；重新构建后把 target/debug 里的
# agent-desktop.exe 和 WebView2Loader.dll 复制回根目录即可更新。
# （根目录 exe 会自动定位 desktop-client/agent-server.py 启动后端）

# 方式二：开发模式
cd desktop-client/src-tauri
cargo build    # 首次编译 ~7min，后续增量 ~30s
./target/debug/agent-desktop.exe   # 双击即用，无终端窗口

# 方式二：npm 一键启动（内部调用 cargo）
cd desktop-client
npm run tauri dev

# 方式三：打包发布版
npm run tauri build
# 输出 → src-tauri/target/release/bundle/
```

**启动后自动**：
1. 无终端黑框（GUI 应用模式）
2. 自动拉起 Python 后端（:18789，隐藏窗口）
3. 显示主窗口（左侧正视图立绘 + 右侧聊天区）
4. 关闭主窗口 → 自动切到桌宠悬浮模式

## 功能特性

| 特性 | 说明 |
|------|------|
| **主窗口** | 左侧正视图立绘（从三视图裁切）+ 右侧聊天区，黑白柔粉轻拟物风 |
| **桌宠模式** | 透明悬浮窗 + 6 状态精灵图（待机/睡觉/按住/左滑/右滑/回复完成） |
| **控制栏** | 桌宠右上角悬停显示：返回主窗口(□) + 退出应用(×) |
| **聊天气泡** | AI 回复时头顶弹出气泡，8 秒自动消失 |
| **拖拽定位** | 拖动桌宠到桌面任意位置；单击唤出主窗口；双击同上 |
| **无终端** | GUI 应用子系统，不弹 cmd 黑框；Python 子进程也隐藏 |
| **模型切换** | 聊天头部 DeepSeek / 本地 一键切换（localStorage 记忆）：本地模式走 Ollama qwen3-vl:8b（断网可用、原生工具+看图）；DeepSeek 模式文本走 DeepSeek、发图走本地视觉描述 |
| **本地按需启动** | 每次登录后第一次选「本地」弹确认框「是否启动本地模型」；确认才启动 Ollama+模型（预加载），切回 DeepSeek / 退出应用时自动卸载释放显存；平时不选本地零占用 |
| **图片发送** | 附件图片发送前自动降采样压缩（最大 1280px），请求体上限 30MB；可只发图片不带文字 |
| **技能按钮** | 聊天输入框左侧 ⚡ 按钮：列出全部已安装技能，自选后命令小满执行（类似 codex 的 @ 技能） |
| **主动触发 v2** | 档案「主动触发」支持任意「时间+内容」+ 重复模式（每天/周X/工作日/周末/每N天/日期），含「提醒」自动取提醒时间 |
| **连通性检测** | 启动后自动检测：后端/DeepSeek/Ollama 连通状态；连不上不显示「在线」，header 实时显示模型与离线状态 |
| **WebView2 自适应** | 自动搜索系统 EdgeWebView 运行时，无需手动安装 |

## 项目结构

```
AGENT/                          # 项目根目录
├── desktop-client/             # ★ 桌面客户端（本项目）
│   ├── src-tauri/              # Rust/Tauri 后端
│   │   ├── Cargo.toml          # [已改] 加 windows_subsystem = "windows"
│   │   ├── tauri.conf.json     # 双窗口配置（main + pet）
│   │   └── src/
│   │       ├── main.rs         # [已改] WebView2 自动检测 + 窗口管理
│   │       ├── commands.rs     # [已改] IPC 命令 + CREATE_NO_WINDOW + exit_app + provider 透传
│   │       └── pet_manager.rs  # 6 种状态枚举定义
│   ├── src/                    # 前端静态文件（Tauri 编译期嵌入 exe）
│   │   ├── index.html         # [已改] 正视图裁切容器
│   │   ├── styles.css         # [已改] 三视图 CSS 裁切
│   │   ├── app.js             # invoke() IPC 模式 + 安全 Tauri 访问
│   │   ├── pet.html           # [已重写] 控制栏 + 精灵图 + 气泡
│   │   ├── pet.css            # [已重写] 控制栏样式 + 精灵图 3×2 切换
│   │   └── pet.js             # [已重写] 安全 API + 拖拽/点击/状态机
│   ├── agent-server.py        # Python HTTP Bridge（:18789）
│   ├── package.json            # npm/Tauri CLI 配置
│   └── README.md              # 本文档
├── 素材/                      # 角色素材（不进仓库，.gitignore）
│   ├── 3573...png             # 完整参考表（三视图+配色+状态图+对话框）
│   ├── 三视图/1000148096.jpg   # 正|侧|背 三视图 → 主窗口左侧（CSS 裁取正面）
│   ├── 对话框/1000148098.png   # 气泡样式参考（透明 PNG）
│   └── 状态图/1000148095.png   # ★ 桌宠 6 状态精灵图（透明 PNG, 3×2 网格）
├── core/                      # Agent 核心逻辑（共享）
├── memory/                    # 记忆机制代码（纯接口，不含数据）
├── memory_data/               # ★ 记忆数据（用户资产，严格只读）
└── skills/                    # Agent 技能插件
```

## 素材说明

| 文件 | 用途 | 加载方式 |
|------|------|----------|
| `三视图/1000148096.jpg` | 主窗口左侧立绘（只显示正视图） | HTTP 代理 `/assets/三视图/...` + CSS 裁切左 1/3 |
| `状态图/1000148095.png` | 桌宠 6 状态精灵图（透明 PNG） | HTTP 代理 `/assets/状态图/...` + CSS background-position |
| `对话框/1000148098.png` | 气泡样式参考 | CSS 复刻（圆角+模糊+尾巴） |
| `3573...png` | 完整参考表（开发用） | 不嵌入产品 |

### 替换素材

1. 将新文件放入对应 `素材/` 子目录
2. **保持文件名不变**（或同步改 HTML/CSS 中的引用 URL）
3. 主窗口立绘：改 `index.html` 中 `<img>` 的 `src`
4. 桌宠精灵图：改 `pet.css` 中 `--sprite-url` 变量
5. 若新立绘是已抠好的透明 PNG：去掉 `.illustration-wrap` 的 `overflow:hidden`，将 `.illustration` width 改回 `100%`

## 技术架构

```
┌─────────────────────────────────────────────┐
│           agent-desktop.exe (Rust)          │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐ │
│  │ Main Win │  │ Pet Win   │  │ Commands │ │
│  │ 1100×700 │  │ 260×300  │  │ (IPC)    │ │
│  │ 装饰窗口  │  │ 透明无边框│→ │ invoke() │ │
│  └────┬─────┘  └─────┬─────┘  └────┬─────┘ │
│       │               │             │       │
│  index.html       pet.html      subprocess │
│  (invoke 模式)    (6状态精灵图)  (隐藏窗口)  │
└───────┼───────────────┼─────────────┼───────┘
        │               │             │
        ▼               ▼             ▼
   ┌──────────────────────────────────────┐
   │     agent-server.py (Python :18789) │
   │  ├─ core.agent_core (DeepSeek 对话)  │
   │  ├─ memory.MemoryStore (只读记忆)    │
   │  ├─ skills/ (工具插件)              │
   │  └─ /assets/* (素材 HTTP 代理)       │
   └──────────────────────────────────────┘
                        │
                        ▼
              DeepSeek API (HTTPS)
```

## 已知问题 & 待改进

- [ ] **正视图抠图**：当前用 CSS 裁切三视图 JPG 的左 1/3（白底仍在）。理想方案是用 PIL/rembg 抠出透明 PNG 替换
- [ ] **打包适配**：`tauri build` 打包版需将 Python 打包为 sidecar 或要求用户安装 Python；当前仅 debug 模式可用
- [ ] **WebView2 Loader**：`target/debug/WebView2Loader.dll` 目前由 webview2-com-sys 构建脚本输出到 build 目录，`cargo clean` 后需确认是否自动拷贝到 exe 同目录
- [x] **记忆持久化闭环**：core.process_turn 每轮 autosave 写 `current.json`（防关窗丢记忆）；退出时桌面端调用 `/finalize` 触发时间戳归档 + LTM 档案抽取，与终端行为对齐

## 常见问题

**Q: 启动后黑屏？**
A: WebView2 运行时缺失。本程序会自动搜索系统 EdgeWebView。若仍黑屏，确认 `C:\Program Files (x86)\Microsoft\EdgeWebView\Application\` 存在。

**Q: 素材图片不显示？**
A: 检查 Python 后端是否正常：`curl http://127.0.0.1:18789/health` 应返回 `{"status":"ok"}`。确认 `素材/` 目录下文件存在。

**Q: 桌宠拖不动？**
A: 确保鼠标按下在角色本体区域（非控制栏/气泡区域）。控制栏 `-webkit-app-region: no-drag` 不参与拖拽。

**Q: 如何完全退出？**
A: 悬停桌宠 → 点右上角 × 按钮。或在主窗口按 Alt+F4。
