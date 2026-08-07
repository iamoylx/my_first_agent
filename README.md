# AGENT · 记忆增强型终端 Agent（练手项目）

基于 DeepSeek Function Calling 实现的终端对话 Agent，重点练习两大能力：**工具调用** 与 **记忆系统**。

本项目是**学习/练手性质**，架构以「简单、可理解、零额外成本」为先。长期记忆以 **结构化档案卡（JSON）** 为主；若要做生产级 Agent，应转向 **向量数据库 + RAG**（区别见第六节）。

---

## 一、整体架构：三层记忆 + 档案卡

| 层 | 作用 | 存储 | 触发时机 | 机制 | 状态 |
|----|------|------|----------|------|------|
| **STM 短期** | 单会话内上下文过长时压缩 | 内存 `messages` | token 达窗口 80% | token 滑动窗口，丢最旧普通消息 | 已实现 |
| **MTM 中期** | 关掉再开还能接住上次聊了啥 | 本地文件 `memory/sessions/` | 会话结束 / 启动 | 完整对话重载（续聊）+ 每轮静默落盘 | 已完成（摘要 Phase 待做） |
| **LTM 长期·结构化** | 记住用户稳定事实/偏好 | `memory/profile.json` | 会话结束离线抽取 | 档案卡，**状态复写 latest-wins** | 已完成（本项目重点） |
| **LTM 长期·向量** | 语义召回历史会话（可选） | 向量库 | 当前问题相关时检索 | 仅索引会话摘要，非每条消息 | 可选 Phase 2 |

所有层的产物最终都通过 **system 提示词** 注入给 LLM。

---

## 二、目录结构

### 当前（已落地）

```
AGENT.py                      # 主程序：Function Calling 主循环 + try/except 容错
memory/
├─ token_window.py            # STM：token 滑动窗口 prune()
├─ sessions.py                # MTM：跨重启续聊（current.json 落盘/读回 + 时间戳归档 + 每轮静默 autosave）
└─ profile.py                 # LTM 结构化档案卡（profile.json 读写 + latest-wins 合并 + 离线抽取 extract_facts）
skills/
├─ __init__.py                # 技能注册表 collect_tools()
├─ basic_tools/               # 内置工具：时间 / 计算器
│  ├─ __init__.py             #   对外暴露 TOOLS + TOOL_MAP
│  ├─ schema.py               #   给 LLM 看的工具描述
│  └─ skill.py                #   工具实现
└─ web_search/                # 联网搜索技能（需 TAVILY_API_KEY）
   ├─ __init__.py             #   对外暴露 TOOLS + TOOL_MAP
   ├─ config.py               #   地址/超时/上限/Key（写死安全参数）
   ├─ schema.py               #   工具描述
   ├─ skill.py                #   主逻辑：校验→请求→重试→降级
   └─ parser.py               #   安全解析 + 清洗（去 HTML/控长度）
tests/                        # 单元测试（不依赖网络）：test_sessions.py / test_profile.py
```

### 规划（剩余项）

- **MTM 摘要 Phase**：`sessions.py` 已实现「完整对话续聊」，后续可加「会话摘要」模式（压缩后存档，启动时检索注入，而非整段重载）。
- **LTM 向量库（可选 Phase 2）**：仅索引会话摘要的语义召回，非每条消息 embedding。

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
```

启动：
```powershell
cd "D:\document\Myprojects\学习\AGENT"
python AGENT.py
```

**档案卡（LTM）行为**：
- 启动时若 `memory/profile.json` 已有内容，会打印 `[系统] 已载入用户档案卡（LTM）。`，并把档案随 system 提示词常驻注入。
- 每次退出（exit / Ctrl+C / 关终端被杀）时，agent 在 `finally` 里**离线抽取**本轮对话中的稳定事实，自动写入 `profile.json`（latest-wins 合并）。
- 下次启动 agent 便"记得"你是谁（如姓名、城市、学习目标）。
- 直接编辑 `profile.json` 也可手动维护档案。

---

## 七、开发路线

| 阶段 | 内容 | 状态 |
|------|------|------|
| 1 | STM：token 滑动窗口 `prune()` | 已完成 |
| 2 | LTM 结构化档案卡 `profile.py`（含状态复写 latest-wins + 离线抽取） | 已完成 |
| 3 | MTM 跨重启续聊 `sessions.py`（落盘/读回 + 每轮 autosave） | 已完成 |
| 4 | MTM 会话摘要 / LTM 向量库（仅索引摘要，可选 Phase 2） | 可选 |

---

## 八、已知技术债务 / 待优化

- `memory/sessions.py` 目前是「完整对话续聊」模式，尚未做「会话摘要」压缩；长会话会随轮次累积，靠 `prune()` 控制窗口。
- `extract_facts` 每次退出都重发完整 `messages` 给模型抽取（latest-wins 幂等，但略费 token）；后续可只抽「本次新增轮次」以优化。
- `safe_trim()` 此前被记为死代码，经核查 `AGENT.py` 中已不存在，本条作废。
