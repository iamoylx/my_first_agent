# AGENT · 记忆增强型终端 Agent（练手项目）

基于 DeepSeek Function Calling 实现的终端对话 Agent，重点练习两大能力：**工具调用** 与 **记忆系统**。

本项目是**学习/练手性质**，架构以「简单、可理解、零额外成本」为先。长期记忆以 **结构化档案卡（JSON）** 为主；若要做生产级 Agent，应转向 **向量数据库 + RAG**（区别见第六节）。

---

## 一、整体架构：三层记忆 + 档案卡

| 层 | 作用 | 存储 | 触发时机 | 机制 | 状态 |
|----|------|------|----------|------|------|
| **STM 短期** | 单会话内上下文过长时压缩 | 内存 `messages` | token 达窗口 80% | token 滑动窗口，丢最旧普通消息 | 已实现 |
| **MTM 中期** | 关掉再开还能接住上次聊了啥 | `memory/users/<id>/sessions/` | 会话结束 / 启动 | 完整对话重载（续聊）+ 每轮静默落盘 | 已完成（摘要 Phase 待做） |
| **LTM 长期·结构化** | 记住用户稳定事实/偏好 | `memory/users/<id>/profile.json` | 会话结束离线抽取 | 档案卡，**状态复写 latest-wins**；偏好标注 type=preference 并注入 system 调整回答；**渲染按 `updated_at` 倒序（最新优先），且对身份/角色/风格类事实常驻保护（不被预算截断）** | 已完成（本项目重点） |
| **LTM 长期·向量** | 语义召回历史会话（可选） | 向量库 | 当前问题相关时检索 | 仅索引会话摘要，非每条消息 | 可选 Phase 2 |

三层由 **统一记忆层 `memory/store.py`（MemoryStore）** 封装，对外提供一致的
**写入 / 读取 / 检索 / 过期清理** 接口，并按 `user_id`（用户）+ `session_id`（会话）维度隔离。
所有层的产物最终都通过 **system 提示词** 注入给 LLM。

---

## 二、目录结构

### 当前（已落地）

```
AGENT.py                      # 主程序：Function Calling 主循环 + try/except 容错
commands.py                   # 终端命令：/recall /load /sessions /profile /summary /forget /cleanup /help
memory/
├─ store.py                   # 统一记忆层 MemoryStore：封装三层 + 用户/会话隔离 + 检索/清理接口
├─ token_window.py            # STM：token 滑动窗口 prune()
├─ sessions.py                # MTM 底层：落盘/读回/每轮 autosave/sanitize（被 store 复用）
└─ profile.py                 # LTM 底层：档案卡读写 + latest-wins 合并 + 离线抽取（被 store 复用）
# 运行时生成的隔离目录（已被 .gitignore 排除，不进仓库）：
# memory/users/<user_id>/profile.json
# memory/users/<user_id>/sessions/{current,<时间戳>}.json
skills/
├─ __init__.py                # 技能注册表 collect_tools()
├─ basic_tools/               # 内置工具：时间 / 计算器
│  ├─ __init__.py             #   对外暴露 TOOLS + TOOL_MAP
│  ├─ schema.py               #   给 LLM 看的工具描述
│  └─ skill.py                #   工具实现
├─ web_search/                # 联网搜索技能（需 TAVILY_API_KEY）
│  ├─ __init__.py             #   对外暴露 TOOLS + TOOL_MAP
│  ├─ config.py               #   地址/超时/上限/Key（写死安全参数）
│  ├─ schema.py               #   工具描述
│  ├─ skill.py                #   主逻辑：校验→请求→重试→降级
│  └─ parser.py               #   安全解析 + 清洗（去 HTML/控长度）
└─ code_tools/                # 本地代码/命令技能：看自己文件夹代码 + 命令行简单操作
   ├─ __init__.py             #   对外暴露 TOOLS + TOOL_MAP
   ├─ schema.py               #   工具描述（5 个工具）
   └─ skill.py                #   读文件 / 列目录 / 搜文件 / 搜内容 / 执行命令
tests/                        # 单元测试（不依赖网络）：test_sessions.py / test_profile.py / test_store.py / test_commands.py / test_p2.py / test_p3.py / test_code_tools.py / test_memory_noloss.py
```

### 规划（剩余项）

- **终端命令层（已落地）**：`commands.py` 提供 `/recall /load /sessions /profile /summary /forget /help`，可在不消耗 LLM token 的前提下检视与操纵记忆（见「二之二」）。
- **MTM 摘要（已落地 `/summary --llm`）**：`/summary` 本地零成本摘要（首问/轮数/工具）保留；加 `--llm` 用模型压缩会话为结构化摘要并存档 `<id>.summary.json`，`/load` 时若有摘要则注入为上下文锚点（无需整段重载）。
- **LTM 向量库（可选 Phase 2）**：仅索引会话摘要的语义召回，非每条消息 embedding。

---

## 二之一、统一记忆层 `MemoryStore`（接口与隔离）

`memory/store.py` 是记忆系统的统一入口，主程序只与它打交道。实例化时指定用户：

```python
from memory.store import MemoryStore
mem = MemoryStore(user_id=os.getenv("AGENT_USER_ID", "default"))  # 默认 default
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

- **用户隔离**：每个 `user_id` 独立目录 `memory/users/<id>/`，互不串数据（多用户 Agent 直接换 `AGENT_USER_ID`）。
- **会话隔离**：同用户下，`save_session(msgs, session_id="20260808_...")` 生成独立归档，`load_session(id)` 精确回看。

### 向后兼容（数据安全保证）

首次运行、`memory/users/<id>/` 尚不存在时，`load_profile()` / `load_last_session()` 会
**从旧扁平路径（`memory/profile.json`、`memory/sessions/current.json`）只读回退**。
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

## 二之四、记忆正确性保障（严格体检后修正）

针对「停留在昨天 / 偶丢风格 / 聊代码乱码」三个症状，做了根因修复。**本次仅改渲染·注入·清洗逻辑，未读写任何 profile 文件，现有记录零丢失**（附 `tests/test_memory_noloss.py` 只读校验）。

1. **今天的事被丢（根因：截断方向反了）**
   旧 `to_context_text` 按字典插入顺序（旧的在前）拼接后取 `text[:max_chars]`，导致**最新的（今天）事实在字典末尾、最先被切掉**。
   修复：普通事实按 `updated_at` **倒序（最新在前）**，预算只约束普通事实层；旧事实才可能被切掉。

2. **角色/风格偶丢（根因：核心事实未被保护）**
   `agent_role`/`agent_style`/`agent_name` 等早期写入的**没有 `type` 字段**，被当普通事实参与截断。
   修复：渲染时识别「核心人格事实」——`type∈{preference,role}` **或** key 含 `role/style/name/pref` 等，永远**置顶且不被预算截断**；极小预算下仍完整保留。

3. **停留在昨天（根因：上下文里没有“今天”）**
   系统提示词原本不含日期，agent 不知道已是新的一天；恢复的上次会话也无跨天提示。
   修复：启动即在 system 注入「当前时间：YYYY年MM月DD日 HH:MM（周X）」；若恢复的会话来自**上一天**，额外注入跨天提示，要求按当前时间理解。

4. **聊代码乱码 + 与风格丢失常同时发生（根因：工具返回值脏字符）**
   见上「输出清洗」——工具输出清洗后，脏字符不再污染上下文，模型既不会复述乱码，也不会被冲散风格。

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
- 启动时 `MemoryStore` 载入本用户档案卡（首次运行从旧 `memory/profile.json` 只读回退），
  打印 `[系统] 已载入用户档案卡（LTM）。`，并随 system 提示词常驻注入。
- 每次退出（exit / Ctrl+C / 关终端被杀）时，agent 在 `finally` 里**离线抽取**本轮对话中的稳定事实，
  自动写入本用户 `memory/users/<id>/profile.json`（latest-wins 合并）。
- 下次启动 agent 便"记得"你是谁（如姓名、城市、学习目标）。
- 直接编辑对应 `profile.json` 也可手动维护档案。
- **多用户**：设置不同 `AGENT_USER_ID` 即可让每位用户拥有独立记忆，互不干扰。

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
| 3.10 | 记忆正确性修复：渲染 recency 倒序 + 核心人格常驻保护 + system 注入当前日期/跨天提示 + 工具输出清洗（去 ANSI/控制字符、GBK 兜底解码、限长），消除“停留在昨天/丢风格/聊代码乱码” | 已完成 |
| 4 | LTM 向量库（仅索引摘要，可选 Phase 2） | 可选 |

---

## 八、已知技术债务 / 待优化

- `memory/sessions.py` 已支持「完整对话续聊」+「LLM 会话摘要」（`/summary --llm` 压缩存档、`/load` 注入锚点）两种模式；长会话仍靠 `prune()` 控制窗口。
- 抽取已改为**增量**：每轮 `buffer_round` 累积新增轮次，会话结束 `extract` 只发新增内容（无缓冲时降级发整段），省 token。
- `safe_trim()` 此前被记为死代码，经核查 `AGENT.py` 中已不存在，本条作废。
