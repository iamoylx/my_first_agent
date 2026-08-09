# AGENT · 记忆增强型终端 Agent（练手项目）

基于 DeepSeek Function Calling 实现的终端对话 Agent，重点练习两大能力：**工具调用** 与 **记忆系统**。

本项目是**学习/练手性质**，架构以「简单、可理解、零额外成本」为先。长期记忆以 **结构化档案卡（JSON）** 为主；若要做生产级 Agent，应转向 **向量数据库 + RAG**（区别见第五节）。

> **记忆机制与数据彻底分离**：`memory/` 是纯机制代码（硬板接口），`memory_data/` 是纯本地记忆数据（硬盘，可整体复制提取），二者互不混杂（见第六节）。

---

## 一、整体架构：三层记忆 + 档案卡

| 层 | 作用 | 存储 | 触发时机 | 机制 | 状态 |
|----|------|------|----------|------|------|
| **STM 短期** | 单会话内上下文过长时压缩 | 内存 `messages` | token 达窗口 80% | token 滑动窗口，丢最旧普通消息 | 已实现 |
| **MTM 中期** | 关掉再开还能接住上次聊了啥 | `memory_data/users/<id>/sessions/` | 会话结束 / 启动 | 完整对话重载（续聊）+ 每轮静默落盘 + LLM 摘要锚点 | 已完成 |
| **LTM 长期·结构化** | 记住用户稳定事实/偏好 | `memory_data/users/<id>/profile.json` | 会话结束离线抽取 | 档案卡，**状态复写 latest-wins**；偏好标注 type=preference 并注入 system 调整回答；**渲染按 `updated_at` 倒序（最新优先），且对身份/角色/风格类事实常驻保护（不被预算截断）** | 已完成（本项目重点） |
| **LTM 长期·向量** | 语义召回历史会话（可选） | 向量库 | 当前问题相关时检索 | 仅索引会话摘要，非每条消息 | 可选 Phase 2 |

三层由 **统一记忆层 `memory/store.py`（MemoryStore）** 封装，对外提供一致的
**写入 / 读取 / 检索 / 过期清理** 接口，并按 `user_id`（用户）+ `session_id`（会话）维度隔离。
所有层的产物最终都通过 **system 提示词** 注入给 LLM。

---

## 二、目录结构

```
AGENT.py                      # 终端主程序：Function Calling 主循环 + try/except 容错
commands.py                   # 终端命令：/recall /load /sessions /profile /summary /forget /cleanup /help
core/
└─ agent_core.py              # headless 内核：调模型 + 工具循环 + 流式 + DSML 防御 + build/finalize
memory/                       # ★纯机制代码（硬板接口，进仓库，不含任何数据文件）
├─ __init__.py                #   包说明
├─ store.py                   #   统一记忆层 MemoryStore（路径由 base_dir 驱动，默认 memory_data/）
├─ token_window.py            #   STM：token 滑动窗口 prune()
├─ sessions.py                #   MTM 纯函数：sanitize 清洗 + summarize_session 摘要
└─ profile.py                 #   LTM 纯函数：merge_facts 合并 + to_context_text 渲染 + extract 抽取
memory_data/                  # ★纯本地记忆数据（硬盘，不进仓库，可整体复制提取）
├─ profile.json               #   旧扁平档案（向后兼容只读回退，不写入）
├─ sessions/                  #   旧扁平会话（向后兼容只读回退，不写入）
└─ users/<user_id>/
   ├─ profile.json            #   LTM 档案卡（长期事实，latest-wins）
   └─ sessions/{current,<时间戳>}.json   # MTM 会话（续聊 + 归档 + .summary.json 摘要）
skills/                       # 技能（自包含，注册即生效，主循环零改动）
├─ __init__.py                #   collect_tools() 聚合器
├─ basic_tools/               #   时间 get_current_time + 计算器 calculator
├─ code_tools/                #   读文件/列目录/搜文件/搜内容/跑命令（含危险指令拦截）
└─ web_search/                #   联网搜索（Tavily，URL 写死防 SSRF）
tests/                        # 单元测试（不依赖网络，用 temp 目录隔离，不污染真实记忆）
素材/                          # 桌宠阶段遗留立绘（终端版不加载，保留备用）
```

**为什么 memory/ 和 memory_data/ 分开**：机制代码（`memory/`）与本地记忆数据（`memory_data/`）彻底解耦——代码进版本库，数据不进；想迁移/备份记忆，直接 `cp -r memory_data/` 即可整体带走，无需挑拣（详见第六节）。

---

## 二之一、统一记忆层 `MemoryStore`（接口与隔离）

`memory/store.py` 是记忆系统的统一入口，主程序只与它打交道。实例化时指定用户：

```python
from memory.store import MemoryStore
mem = MemoryStore(user_id=os.getenv("AGENT_USER_ID", "default"))  # 默认 default
# base_dir 默认指向项目根的 memory_data/；测试时可传 base_dir=tempfile 隔离
```

### 四类接口

| 类别 | 方法 | 说明 |
|------|------|------|
| **写入** | `autosave(messages)` | 每轮静默落盘 `current.json`（防关窗口丢记忆） |
| | `save_session(messages, session_id=None)` | 退出时完整保存：current + 一份时间戳归档 |
| | `update_profile(extracted)` | 合并新事实（latest-wins），仅变更才落盘 |
| **读取** | `load_last_session()` | 续聊：读回上次完整对话 |
| | `load_profile()` | 读档案卡（带缓存） |
| | `load_session(session_id)` | 按 id 读回某次归档会话 |
| | `list_sessions()` | 列出本用户所有会话元信息 |
| | `profile_context()` | 渲染档案卡为注入 system 的紧凑文本 |
| **检索** | `search_profile(keyword)` | 档案卡关键词命中 |
| | `search_sessions(keyword)` | 扫描归档会话含关键词的片段 |
| | `retrieve(query)` | 跨层综合检索（档案 + 会话） |
| **清理** | `cleanup_expired(days=30)` | 删除超龄归档会话（保留 current），仅显式调用 |

### 维度隔离

- **用户隔离**：每个 `user_id` 独立目录 `memory_data/users/<id>/`，互不串数据（多用户 Agent 直接换 `AGENT_USER_ID`）。
- **会话隔离**：同用户下，`save_session(msgs, session_id="20260808_...")` 生成独立归档，`load_session(id)` 精确回看。

### 向后兼容（数据安全保证）

首次运行、`memory_data/users/<id>/` 尚不存在时，`load_profile()` / `load_last_session()` 会
**从旧扁平路径（`memory_data/profile.json`、`memory_data/sessions/current.json`）只读回退**。
回退过程**不删除、不改写**旧文件；首次保存时才把数据写到新的隔离目录。
因此升级后你已有的对话历史与档案卡保持完整、零丢失。

---

## 二之二、终端命令（commands.py）

命令与「工具(tools)」是两套机制：**工具**交给模型自主调用；**命令**由你在终端显式输入，
用于**检视 / 操纵记忆**，且不消耗任何 LLM token（除离线抽取外的零成本操作）。

主循环在每轮读入后、调用模型前先做命令分发（`is_command` 识别、`run_command` 执行），
命中命令就跳过本轮模型调用。

| 命令 | 作用 | 是否进 LLM |
|------|------|-----------|
| `/help` | 列出全部命令 | 否 |
| `/sessions` | 列出本用户所有会话（ID / 消息数 / 修改时间） | 否 |
| `/profile` | 查看长期档案卡（LTM）全部事实 | 否 |
| `/recall <关键词>` | 跨层检索档案卡+历史会话并**展示**结果（只读，不注入上下文） | 否 |
| `/load <会话ID>` | 载入某历史会话并**继续对话**（替换当前上下文，再裁剪防撑爆） | 续聊后照常 |
| `/summary <会话ID> [--llm]` | 本地会话摘要（首问/轮数/工具，零 LLM 开销）；加 `--llm` 用模型生成压缩存档 `<id>.summary.json` | 仅 `--llm` 时 |
| `/forget <会话ID>` | 删除某条归档会话（`current` 不可删） | 否 |
| `/cleanup [天数]` | 清理超过 N 天（默认 30）的归档会话及其摘要（`current` 永不被删） | 否 |
| `exit` | 退出 | — |

> 设计取舍：`/recall` 只**展示**命中结果，不把历史内容塞回上下文——避免上下文膨胀与重复。
> 需要「接着上次聊」请用 `/load` 显式载入；命令名支持带斜杠（推荐）或裸词（如直接输入 `sessions`）。
> `/summary --llm` 生成的压缩摘要会在 `/load` 该会话时自动注入为上下文锚点，长会话续聊不必整段重载。
> **启动自动注入**：若上一段会话曾用 `/summary --llm` 生成过摘要，重启 agent 时会自动把【最近一份】摘要作为上下文锚点注入（无需手动 `/load`），实现跨会话连续性；无摘要则不注入。

---

## 二之三、工具技能 code_tools（看自己文件夹代码 + 命令行操作）

`skills/code_tools/` 让 agent 能直接审视**自己所在项目**的代码，并用命令行做简单操作。
同样遵循「自包含 + 注册即生效」模式：主循环分发逻辑零改动，仅在 `collect_tools` 加一对即可。

| 工具 | 作用 | 安全级别 |
|------|------|----------|
| `read_file(path, max_lines, start_line)` | 读取项目内文件内容（带行号标记），看源码/配置 | 只读 |
| `list_dir(path)` | 列出目录结构，确认文件布局 | 只读 |
| `search_files(name_pattern, path)` | 按文件名/通配符（如 `*.py`）查文件 | 只读 |
| `search_content(keyword, path, max_matches)` | 在文件内容中搜关键词/正则（类 grep），定位函数定义、变量引用 | 只读 |
| `run_command(command, timeout)` | 在 agent 运行目录执行 shell 命令并返回输出 | 受 `_DENY` 拦截 |

**安全边界（务必知悉）**：
- `run_command` **不是沙箱**，直接在 agent 进程的工作目录执行命令。内置 `_DENY` 正则拦截递归删除（`rm -rf`）、格式化（`format`/`mkfs`）、关机/重启、提权（`sudo`）、下载即执行（`curl|sh`）、fork bomb 等危险指令；命中即拒绝并返回提示。
- 这是**基础防护**，不是完整沙箱：仍可能执行有副作用的命令（如 `git push`、`pip install`、删除单文件）。要放开/收紧拦截，改 `skills/code_tools/skill.py` 的 `_DENY` 列表即可。
- 文件类工具相对路径按**项目根目录**解析；传入绝对路径则可访问任意位置（含系统目录），请按需在受信任环境使用。

> 设计取舍：文件浏览/搜索做成「只读、带目录跳过（.git/__pycache__ 等）」，避免把无关噪音喂给模型；命令执行则显式拦截高危操作，其余交给用户在可信任环境下授权。
> **输出清洗（防乱码）**：`read_file`/`search_content`/`run_command` 的返回统一经 `_sanitize` 去除 ANSI 转义与不可打印控制字符、经 `_decode` 按 `utf-8→cp936(GBK)→latin-1` 兜底解码，并限制长度（文件≤4000、搜索≤2500、命令≤2000 字符）。这是「聊到代码/本体程序就输出一段乱码」的根因修复——Windows 终端的彩色/GBK 输出若原样进上下文，模型会复述成乱码并连带丢失风格。

---

## 二之四、记忆正确性保障

针对「停留在昨天 / 偶丢风格 / 聊代码乱码」三个症状，做了根因修复。**仅改渲染·注入·清洗逻辑，未读写任何 profile 文件，现有记录零丢失**（`tests/test_memory_noloss.py` 只读校验）。

1. **今天的事被丢（根因：截断方向反了）**
   旧 `to_context_text` 按字典插入顺序（旧的在前）拼接后取 `text[:max_chars]`，导致**最新的（今天）事实在字典末尾、最先被切掉**。
   修复：普通事实按 `updated_at` **倒序（最新在前）**，预算只约束普通事实层；旧事实才可能被切掉。

2. **角色/风格偶丢（根因：核心事实未被保护）**
   `agent_role`/`agent_style`/`agent_name` 等早期写入的**没有 `type` 字段**，被当普通事实参与截断。
   修复：渲染时识别「核心人格事实」——`type∈{preference,role}` **或** key 含 `role/style/name/pref` 等，永远**置顶且不被预算截断**；极小预算下仍完整保留。

3. **停留在昨天（根因：上下文里没有"今天"）**
   系统提示词原本不含日期，agent 不知道已是新的一天；恢复的上次会话也无跨天提示。
   修复：启动即在 system 注入「当前时间：YYYY年MM月DD日 HH:MM（周X）」；若恢复的会话来自**上一天**，额外注入跨天提示，要求按当前时间理解。

4. **聊代码乱码 + 与风格丢失常同时发生（根因：工具返回值脏字符）**
   见上「输出清洗」——工具输出清洗后，脏字符不再污染上下文，模型既不会复述乱码，也不会被冲散风格。

---

## 二之五、DSML 工具调用防御（core/agent_core.py）

DeepSeek 偶尔不返回原生 `tool_calls`，而是把工具调用写成 `DSML` 文本塞进 `content`
（形如 `<!--｜DSML｜｜invoke name="read_file">…</｜DSML｜｜invoke>`）。
若直接当正文打印，会把内部标记泄露给用户、且工具不会真正执行。

`core/agent_core.py` 在工具调用循环前加了一道防御：
- **能解析** → 转成原生 `tool_calls` **真正执行**（参数做 int/float 类型转换，兼容 `string="true/false"` 写法）；
- **解析不了** → 清洗后直接落盘，**绝不把内部标记泄露给用户、也不死循环**。

正则用 `｜{1,2}` 兼容两类 DSML 写法（变体A `<!--｜DSML｜｜...>` 含 HTML 注释前缀、变体B `｜｜DSML｜｜...>` 无前缀）。
回归测试见 `tests/test_dsml_guard.py`。

---

## 二之六、记忆机制/数据分离（硬板接口 vs 硬盘）★重要约定

记忆系统的**机制代码**与**本地记忆数据**彻底分离，互不混杂：

| | memory/ | memory_data/ |
|---|---|---|
| **角色** | 硬板接口（机制） | 硬盘（数据） |
| **内容** | 纯 .py 代码：store/profile/sessions/token_window | 纯 .json 数据：profile.json / sessions/ / users/ |
| **是否进仓库** | 进 | 不进（.gitignore） |
| **路径硬编码** | 无（路径由 `MemoryStore(base_dir)` 驱动） | — |
| **提取方式** | — | `cp -r memory_data/ <目标>` 整体带走 |

**实现要点**：
- `store.py` 新增模块级常量 `DATA_DIR = 项目根/memory_data`，`base_dir` 默认指向它；所有读写路径都从 `base_dir` 派生，`memory/` 包内**不再有任何指向数据位置的硬编码**。
- 旧版 `profile.py`/`sessions.py` 里被 `MemoryStore` 同名方法取代的直接函数（`load_profile`/`save_profile`/`autosave`/`save_session` 等）已删除——它们曾硬编码 `os.path.dirname(__file__)` 指向 `memory/`，是数据混入接口包的根源。
- **用户底线**：记忆数据绝不许丢。任何改动前先 `cp -r memory_data memory_data.bak.时间戳`，改完逐文件 md5 对比。

---

## 三、核心设计原则（踩坑点）

1. **工具消息保护**：`system` 提示词、`含 tool_calls 的 assistant`、`tool` 角色消息 **永不删除**。
   原因：删了会导致工具调用链断裂或 DeepSeek 返回 400。滑动窗口只丢普通 user/assistant。
2. **状态复写（latest-wins）**：档案卡合并按「事实类型」做 key 去重，新值覆盖旧值。
   用户先说「在北京」后说「搬去上海」，卡里应是上海，不是两条并存。
   规则：记 `updated_at`；**不允许模型用低置信度猜测覆盖已存事实**（加置信度阈值）。
3. **写读分离**：记忆的抽取与整合放在**会话结束的离线任务**里，绝不在用户发言轮次同步做。
   原因：避免每次对话增加 LLM 调用延迟（这是摘要方案被否定的根因）。
4. **上下文预算竞争**：System / 档案卡 / MTM / STM 抢的是同一笔 token 预算。
   四块都无脑注入 = 又撑爆上下文。对策：档案卡小（≤15%）可常驻；MTM/向量按当前问题相关性检索后才注入。
5. **token 滑动窗口替代条数截断**：用 token 估算（优先 `tiktoken`，无依赖则字符启发式），
   到窗口 80% 开始丢最旧的普通消息，而非粗暴按消息条数截断。

---

## 四、工具层（skills）

每个技能是**自包含模块**，对外只暴露两样东西：
- `TOOLS`：给 LLM 看的工具描述（OpenAI/DeepSeek 兼容 schema）
- `TOOL_MAP`：实际可调用函数

主程序启动时用 `collect_tools()` 把各技能的 `(TOOLS, TOOL_MAP)` 聚合成统一清单，
新增技能 = 在注册处加一对，**主循环分发逻辑零改动**（注册即生效）。

当前注册的 8 个工具：`web_search` / `get_current_time` / `calculator` / `read_file` / `list_dir` / `search_files` / `search_content` / `run_command`。

---

## 五、与生产级 Agent 的区别（为何本项目不做向量库）

- **练手（本项目）**：数据量小、单人、本地运行。结构化事实用档案卡足够，
  精确存取、零 embedding 成本、无检索误差。「档案卡为主」是刻意的练习取舍，不是架构缺陷。
- **生产级**：多用户、海量历史、需语义召回 → 向量数据库 + RAG
  （Chroma/FAISS + embedding 模型）。且通常**只索引会话摘要**，不对每条消息 embedding。
- **借鉴关系**：生产方案的「写/读分离 + 离线抽取」思路本项目已采用；
  但其「向量库」是百万级 MAU 的横向扩展需求，本项目用不上。

---

## 六、运行

依赖：
```bash
pip install aiohttp
# 可选：更准的 token 估算（不装则自动回退字符启发式）
pip install tiktoken
```

环境变量（建议写入用户环境变量，勿硬编码进代码）：
```powershell
$env:DEEPSEEK_API_KEY = "sk-你的key"     # 必填
$env:TAVILY_API_KEY   = "tvly-你的key"   # 联网搜索用，可选
$env:AGENT_USER_ID    = "default"        # 可选：记忆隔离的用户维度，默认 default
```

启动：
```powershell
cd "D:\document\Myprojects\学习\AGENT"
python AGENT.py
```

**档案卡（LTM）行为**：
- 启动时 `MemoryStore` 载入本用户档案卡（首次运行从旧 `memory_data/profile.json` 只读回退），
  打印 `[系统] 已载入用户档案卡（LTM）。`，并随 system 提示词常驻注入。
- 每次退出（exit / Ctrl+C / 关终端被杀）时，agent 在 `finally` 里**离线抽取**本轮对话中的稳定事实，
  自动写入本用户 `memory_data/users/<id>/profile.json`（latest-wins 合并）。
- 下次启动 agent 便"记得"你是谁（如姓名、城市、学习目标）。
- 直接编辑对应 `profile.json` 也可手动维护档案。
- **多用户**：设置不同 `AGENT_USER_ID` 即可让每位用户拥有独立记忆，互不干扰。

**备份/迁移记忆**：直接 `cp -r memory_data/ <目标位置>`，即可完整带走所有本地记忆（档案卡 + 全部会话历史 + 摘要）。

---

## 七、开发路线

| 阶段 | 内容 | 状态 |
|------|------|------|
| 1 | STM：token 滑动窗口 `prune()` | 已完成 |
| 2 | LTM 结构化档案卡 `profile.py`（含状态复写 latest-wins + 离线抽取） | 已完成 |
| 3 | MTM 跨重启续聊 `sessions.py`（落盘/读回 + 每轮 autosave） | 已完成 |
| 3.5 | 统一记忆层 `store.py`：三层封装 + user/session 隔离 + 检索/清理接口 + 旧数据只读回退 | 已完成 |
| 3.6 | 终端命令 `commands.py`：`/recall /load /sessions /profile /summary /forget /help` | 已完成 |
| 3.7 | P2 增量抽取（只发新增轮次省 token）+ LLM 会话摘要 `/summary --llm`（压缩存档，`/load` 注入锚点） | 已完成 |
| 3.8 | P3① 启动自动注入最近会话摘要锚点；② `/cleanup [天数]` 命令（显式过期清理） | 已完成 |
| 3.9 | 工具技能 `code_tools`：read_file / list_dir / search_files / search_content / run_command（含危险指令拦截） | 已完成 |
| 3.10 | 记忆正确性修复：渲染 recency 倒序 + 核心人格常驻保护 + system 注入当前日期/跨天提示 + 工具输出清洗 | 已完成 |
| 3.11 | headless 内核 `core/agent_core.py`：抽离主循环，终端与前端共用 + DSML 工具调用防御 | 已完成 |
| 3.12 | 记忆机制/数据分离：`memory/`（纯代码）与 `memory_data/`（纯数据）彻底解耦，可整体提取 | 已完成 |
| 4 | LTM 向量库（仅索引摘要，可选 Phase 2） | 可选 |

---

## 八、已知技术债务 / 待优化

- 长会话仍靠 `prune()` 控制窗口；`/summary --llm` 压缩存档 + `/load` 注入锚点是当前的连续性方案。
- 抽取已改为**增量**：每轮 `buffer_round` 累积新增轮次，会话结束 `extract` 只发新增内容（无缓冲时降级发整段），省 token。
- `memory/` 包内已无死代码（旧版 `profile.load_profile/save_profile`、`sessions.autosave/save_session` 等被 `MemoryStore` 取代的直接函数已删除）。
- `素材/` 为桌宠阶段遗留立绘，终端版不加载，保留备用。
