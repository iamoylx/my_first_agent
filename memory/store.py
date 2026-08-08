# memory/store.py
# 统一记忆层（MemoryStore）——在现有三层实现之上，提供一套干净的对外接口。
#
# 设计目标（对应本次完善需求）：
#   1) 持久化：仍基于 JSON 落盘，但路径按 user/session 维度隔离。
#   2) 分层存储：STM(内存 token 窗口) / MTM(会话文件) / LTM(档案卡) 三类接口统一。
#   3) 维度隔离：user_id 隔离不同用户；session_id 隔离同用户的不同会话。
#   4) 四类接口：写入(write) / 读取(read) / 检索(retrieve) / 过期清理(cleanup)。
#   5) 向后兼容（关键）：首次运行时从旧扁平路径（memory/profile.json、
#      memory/sessions/）只读回退已有数据，绝不删除或改写旧文件，
#      保证本地已有对话历史与档案卡不丢失、不被修改。
#
# 复用底层纯函数，避免重复实现：
#   - profile.merge_facts / profile.to_context_text / profile.extract_facts
#   - sessions.sanitize
#   - token_window.prune
import json
import os
from datetime import datetime, timedelta

from memory.token_window import prune as _prune
from memory.profile import (merge_facts, to_context_text, extract_facts,
                             extract_facts_from_text)
from memory.sessions import sanitize


class MemoryStore:
    """
    统一记忆存储。每个用户拥有独立目录 memory/users/<user_id>/：
        ├─ profile.json          # LTM 档案卡（长期事实）
        └─ sessions/
           ├─ current.json       # 当前会话（续聊用）
           └─ <时间戳>.json       # 历史归档（检索/摘要用）
    """

    def __init__(self, base_dir: str = None, user_id: str = "default"):
        # base_dir 默认即 memory/ 自身；旧扁平文件（profile.json / sessions/）
        # 也位于 base_dir 下，用于向后兼容只读回退。
        self.base_dir = base_dir or os.path.dirname(__file__)
        self.user_id = user_id
        self.user_dir = os.path.join(self.base_dir, "users", user_id)
        self.profile_path = os.path.join(self.user_dir, "profile.json")
        self.sessions_dir = os.path.join(self.user_dir, "sessions")
        # 旧（扁平）路径：仅用于向后兼容“只读回退”，任何情况下都不写入
        self._legacy_profile = os.path.join(self.base_dir, "profile.json")
        self._legacy_sessions_dir = os.path.join(self.base_dir, "sessions")
        self._profile_cache = None   # 运行时缓存，避免反复读盘
        self._pending_extract = []   # 增量抽取缓冲：累积本轮新增的 user/assistant 文本

    # ===================== 路径与目录 =====================
    def _ensure(self):
        """懒创建本用户目录（首次保存时才建），避免空目录污染仓库。"""
        os.makedirs(self.sessions_dir, exist_ok=True)

    @staticmethod
    def _read_json(path, default):
        """安全读 JSON：文件缺失/损坏都返回 default，不抛异常。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default

    # ===================== LTM：档案卡（长期记忆） =====================
    def load_profile(self, use_cache: bool = True) -> dict:
        """
        读取档案卡：优先新路径 users/<id>/profile.json；
        缺失则回退旧扁平 memory/profile.json（只读取，不改动旧文件）。
        """
        if use_cache and self._profile_cache is not None:
            return self._profile_cache
        if os.path.exists(self.profile_path):
            data = self._read_json(self.profile_path, {"version": 1, "facts": {}})
        elif os.path.exists(self._legacy_profile):          # 向后兼容：读旧位置
            data = self._read_json(self._legacy_profile, {"version": 1, "facts": {}})
        else:
            data = {"version": 1, "facts": {}}
        data.setdefault("facts", {})
        self._profile_cache = data
        return data

    def save_profile(self, profile: dict) -> dict:
        """原子写盘到【新路径】；绝不写旧扁平路径，保证旧数据不被覆盖。"""
        self._ensure()
        profile = dict(profile)
        profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
        tmp = self.profile_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.profile_path)            # 原子替换，防半截文件
        self._profile_cache = profile
        return profile

    def update_profile(self, extracted: list):
        """
        写入接口·合并新事实：latest-wins 状态复写。
        返回 (profile, changed)；仅当实际变更才落盘，避免无谓写盘。
        """
        profile = self.load_profile()
        _, changed = merge_facts(profile, extracted)
        if changed:
            self.save_profile(profile)
        return profile, changed

    def search_profile(self, keyword: str) -> dict:
        """检索接口·档案卡：按关键词在 key/value 上做子串匹配，返回命中事实。"""
        profile = self.load_profile()
        kw = (keyword or "").lower()
        if not kw:
            return {}
        return {
            k: v for k, v in profile["facts"].items()
            if kw in k.lower() or kw in str(v.get("value", "")).lower()
        }

    def profile_context(self, max_chars: int = 600) -> str:
        """读取接口·渲染档案卡为注入 system 提示词的紧凑文本。"""
        return to_context_text(self.load_profile(), max_chars=max_chars)

    # ===================== MTM：会话（中期记忆） =====================
    def _session_path(self, session_id: str = "current") -> str:
        return os.path.join(self.sessions_dir, f"{session_id}.json")

    def load_last_session(self) -> list:
        """
        读取接口·续聊：优先新路径 users/<id>/sessions/current.json；
        缺失则回退旧扁平 memory/sessions/current.json。
        """
        cur = self._session_path("current")
        if os.path.exists(cur):
            return self._read_json(cur, [])
        legacy = os.path.join(self._legacy_sessions_dir, "current.json")
        if os.path.exists(legacy):
            return self._read_json(legacy, [])
        return []

    def autosave(self, messages: list):
        """写入接口·每轮静默落盘 current.json（不写归档）。"""
        self._ensure()
        with open(self._session_path("current"), "w", encoding="utf-8") as f:
            json.dump(sanitize(messages), f, ensure_ascii=False, indent=2)

    def save_session(self, messages: list, session_id: str = None) -> str:
        """
        写入接口·会话结束完整保存：更新 current.json + 写一份带时间戳归档
        （归档供后续检索/摘要使用）。返回本次会话的 session_id。
        """
        self.autosave(messages)
        if session_id is None:
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(self._session_path(session_id), "w", encoding="utf-8") as f:
            json.dump(sanitize(messages), f, ensure_ascii=False, indent=2)
        return session_id

    def load_session(self, session_id: str) -> list:
        """读取接口·按 session_id 读回某次归档会话（检索后回看上下文用）。"""
        p = self._session_path(session_id)
        if os.path.exists(p):
            return self._read_json(p, [])
        p2 = os.path.join(self.sessions_dir, session_id)   # 兼容完整文件名
        if os.path.exists(p2):
            return self._read_json(p2, [])
        return []

    def list_sessions(self) -> list:
        """读取接口·列出本用户所有会话元信息（不含 current 时的过滤由调用方做）。"""
        if not os.path.isdir(self.sessions_dir):
            return []
        out = []
        for f in sorted(os.listdir(self.sessions_dir)):
            if not f.endswith(".json"):
                continue
            p = os.path.join(self.sessions_dir, f)
            out.append({
                "session_id": f[:-5],
                "mtime": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds"),
                "messages": len(self._read_json(p, [])),
            })
        return out

    def search_sessions(self, keyword: str) -> list:
        """检索接口·会话：扫描所有归档会话，返回含关键词的片段（current 除外）。"""
        kw = (keyword or "").lower()
        if not kw:
            return []
        hits = []
        for meta in self.list_sessions():
            if meta["session_id"] == "current":
                continue
            msgs = self.load_session(meta["session_id"])
            lines = [
                f"{m['role']}: {m.get('content', '')}"
                for m in msgs
                if kw in str(m.get("content", '')).lower()
            ]
            if lines:
                hits.append({"session_id": meta["session_id"], "matches": lines[:5]})
        return hits

    # ===================== STM：短期窗口 =====================
    def prune(self, messages: list, max_tokens: int = 12000, soft_ratio: float = 0.8) -> list:
        """写入接口（副作用在调用方 messages 上体现）·token 滑动窗口裁剪。"""
        return _prune(messages, max_tokens=max_tokens, soft_ratio=soft_ratio)

    # ===================== 综合检索 + 过期清理 =====================
    def retrieve(self, query: str) -> dict:
        """检索接口·跨层：档案卡命中 + 会话命中合并，供主流程按需注入上下文。"""
        return {
            "profile_hits": self.search_profile(query),
            "session_hits": self.search_sessions(query),
        }

    def cleanup_expired(self, days: int = 30, keep_current: bool = True) -> list:
        """
        过期清理接口：删除超过 days 天的归档会话文件（默认保留 current.json）。
        仅在显式调用时执行；默认 30 天，近期会话不会被删。
        返回被删除的 session_id 列表（空表 = 无需清理，数据完好）。
        """
        if not os.path.isdir(self.sessions_dir):
            return []
        cutoff = datetime.now() - timedelta(days=days)
        removed = []
        for f in os.listdir(self.sessions_dir):
            if not f.endswith(".json"):
                continue
            if keep_current and f == "current.json":
                continue
            p = os.path.join(self.sessions_dir, f)
            mtime = datetime.fromtimestamp(os.path.getmtime(p))
            if mtime < cutoff:
                try:
                    os.remove(p)
                    removed.append(f[:-5])
                except OSError:
                    pass
        return removed

    def delete_session(self, session_id: str) -> bool:
        """
        写入接口·清理单个归档会话（current.json 不可删，防误清空续聊指针）。
        与 load_session 的路径解析保持一致：同时尝试「裸 id」与「id.json」。
        返回是否删除成功（不存在/是 current/出错 都返回 False）。
        """
        if session_id in (None, "", "current"):
            return False
        for cand in (session_id, f"{session_id}.json"):
            p = os.path.join(self.sessions_dir, cand)
            if os.path.exists(p):
                try:
                    os.remove(p)
                    return True
                except OSError:
                    return False
        return False

    # ===================== 离线抽取（写读分离 + 增量） =====================
    def buffer_round(self, user_text: str, assistant_text: str):
        """
        写入接口·累积本轮新增内容到抽取缓冲（增量抽取的核心）。
        主循环每轮把 user+assistant 文本传入；会话结束 extract 时只发缓冲，
        不重发整段历史，省 token。/load 切换会话会 reset_extract_buffer。
        """
        if user_text:
            self._pending_extract.append(f"user: {user_text}")
        if assistant_text:
            self._pending_extract.append(f"assistant: {assistant_text}")

    def reset_extract_buffer(self):
        """清空抽取缓冲（/load 切换会话时调用，避免把上一会话内容并入新会话）。"""
        self._pending_extract = []

    async def extract(self, messages: list, api_key: str, api_url: str, model: str) -> list:
        """
        写入接口·会话结束离线抽取（增量）。
        优先抽取 _pending_extract 累积的“本次新增轮次”；
        若缓冲为空（例如跨进程重启后未接入 buffer_round）才降级用整段 messages，
        保证功能不丢。抽完清空缓冲。任何失败都降级返回 []。
        """
        pending = list(self._pending_extract)
        if pending:
            pending_text = "\n".join(pending)
        else:
            # 兜底：无缓冲时用整段对话（兼容旧调用路径 / 漏接 buffer_round 的情况）
            pending_text = "\n".join(
                f"{m['role']}: {m.get('content', '')}"
                for m in messages
                if m.get("role") in ("user", "assistant") and m.get("content")
            )
        self._pending_extract = []   # 抽完即清，无论成败（finally 是会话终点）
        if not pending_text.strip():
            return []
        # 只发缓冲/新增轮次，避免重发整段历史
        return await extract_facts_from_text(pending_text, api_key, api_url, model)

    # ===================== MTM：会话摘要（LLM 压缩存档） =====================
    def _summary_path(self, session_id: str) -> str:
        """摘要文件路径：<sessions_dir>/<id>.summary.json。兼容裸 id 与 id.json。"""
        sid = session_id[:-5] if session_id.endswith(".json") else session_id
        return os.path.join(self.sessions_dir, f"{sid}.summary.json")

    def save_summary(self, session_id: str, summary: dict) -> dict:
        """写入接口·保存某会话的 LLM 压缩摘要到 <id>.summary.json（原子写）。"""
        self._ensure()
        summary = dict(summary)
        summary["session_id"] = session_id
        summary["updated_at"] = datetime.now().isoformat(timespec="seconds")
        tmp = self._summary_path(session_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._summary_path(session_id))
        return summary

    def load_summary(self, session_id: str) -> dict:
        """读取接口·读回某会话的 LLM 摘要（不存在返回 {}）。"""
        return self._read_json(self._summary_path(session_id), {})

    def get_recent_summary(self) -> dict:
        """
        读取接口·返回本用户【最近一份】会话 LLM 摘要（按 mtime），
        用于启动时把上一段会话的压缩摘要作为上下文锚点注入（连续性）。
        只有存在 <id>.summary.json 时才非空；否则返回 {}（不注入）。
        """
        if not os.path.isdir(self.sessions_dir):
            return {}
        best, best_time = {}, None
        for f in os.listdir(self.sessions_dir):
            if not f.endswith(".summary.json"):
                continue
            p = os.path.join(self.sessions_dir, f)
            t = os.path.getmtime(p)
            if best_time is None or t > best_time:
                best_time = t
                best = self._read_json(p, {})
        return best or {}


def format_summary_anchor(summ: dict) -> str:
    """把 LLM 摘要渲染成可注入 system 的上下文锚点文本（主循环与 /load 共用）。"""
    lines = ["以下是你将要继续的会话的压缩摘要（非逐字记录）："]
    if summ.get("title"):
        lines.append(f"标题：{summ['title']}")
    if summ.get("topics"):
        lines.append("主题：" + "、".join(summ["topics"]))
    if summ.get("key_points"):
        lines.append("要点：")
        lines += [f"  - {kp}" for kp in summ["key_points"]]
    if summ.get("open_questions"):
        lines.append(f"待续问题：{summ['open_questions']}")
    return "\n".join(lines)
