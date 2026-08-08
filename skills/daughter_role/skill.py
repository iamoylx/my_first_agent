# skills/daughter_role/skill.py
# 女儿角色的可工具化动作实现：情绪识别 / 记住重要人事物 / 回想 / 关心开场 / 引导分享。
# 笔记（episodic memory）存于 memory/users/<id>/notes.json，与档案卡隔离、按用户维度一致。
import json
import os
import re
from datetime import datetime


# ---------------- 笔记存储（与记忆层 user/session 隔离策略一致） ----------------
def _notes_dir():
    uid = os.getenv("AGENT_USER_ID", "default")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # skills/daughter_role -> 项目根
    return os.path.join(root, "memory", "users", uid)


def _notes_path():
    return os.path.join(_notes_dir(), "notes.json")


def _read_notes():
    try:
        with open(_notes_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "notes": []}
    data.setdefault("notes", [])
    return data


def _write_notes(data):
    os.makedirs(_notes_dir(), exist_ok=True)
    tmp = _notes_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _notes_path())
    return data


# ---------------- 情绪词典（规则法，离线、确定、可测） ----------------
MOOD_LEXICON = {
    "开心": {"words": ["开心死", "好开心", "真开心", "很开心", "超开心", "特别开心", "可开心",
                     "巨开心", "太开心", "开心呀", "好高兴", "真高兴", "高兴死"],
            "style": "陪他一起高兴，用轻快俏皮的语气呼应，可以顺着话题多问细节"},
    "兴奋": {"words": ["好激动", "太激动", "激动死", "等不及", "迫不及待", "太好了",
                     "好期待", "好兴奋", "兴奋", "惊喜", "哇塞"],
            "style": "用活泼夸张的语气一起雀跃，追问细节、表达我也替他开心"},
    "难过": {"words": ["不开心", "难受", "难过", "伤心", "想哭", "要哭了", "失落", "沮丧",
                     "委屈", "崩溃", "窒息", "心碎", "闷", "低落"],
            "style": "先温柔共情、给情绪空间，不急着给建议；可以说‘抱抱爸’"},
    "焦虑": {"words": ["焦虑", "担心", "紧张", "害怕", "怕", "慌", "压力大", "压力好大",
                     "撑不住", "失眠", "睡不着", "心慌", "不安"],
            "style": "稳定陪伴，帮他把问题拆小而非评判，肯定他的努力"},
    "生气": {"words": ["生气", "气死", "愤怒", "烦死", "讨厌", "火大", "无语", "受不了",
                     "窝火", "恼火", "烦人"],
            "style": "先接住情绪、站在他这边，不火上浇油；稍后再轻轻引导冷静"},
    "疲惫": {"words": ["好累", "好困", "疲惫", "精疲力尽", "累瘫", "累死", "没劲",
                     "提不起劲", "倦", "乏"],
            "style": "关心身体，建议休息，别再给他塞任务"},
    "孤独": {"words": ["孤独", "一个人", "没人陪", "寂寞", "冷清", "想你", "想家", "孤单", "没人懂"],
            "style": "主动靠近、多陪聊，表达‘我一直都在’"},
    "平静": {"words": ["还行", "挺好", "不错", "普通", "正常", "平静", "蛮好", "还好", "凑合"],
            "style": "轻松闲聊，顺势关心近况"},
}
_NEG = ("不", "没", "别", "勿")
_NEGATIVE = {"难过", "焦虑", "生气", "疲惫", "孤独"}
_POSITIVE = {"开心", "兴奋"}


def _has_neg_before(text, idx):
    seg = text[max(0, idx - 3):idx]
    return any(n in seg for n in _NEG)


async def detect_mood(text: str) -> str:
    """分析用户文本的情绪类型、强度与建议回应风格，辅助情绪倾听与安抚。"""
    t = (text or "").lower()
    best, best_hits = "平静", 0
    for mood, info in MOOD_LEXICON.items():
        hits = 0
        for w in info["words"]:
            start = 0
            while True:
                i = t.find(w, start)
                if i == -1:
                    break
                if not _has_neg_before(t, i):   # 否定前缀（不开心/没开心）→ 不计入正面
                    hits += 1
                start = i + len(w)
        if hits > best_hits:
            best_hits, best = hits, mood
    intensity = 1 if best_hits <= 1 else (2 if best_hits == 2 else 3)
    style = MOOD_LEXICON[best]["style"]
    result = {
        "mood": best,
        "intensity": intensity,
        "suggested_style": style,
        "should_comfort": best in _NEGATIVE,
        "should_celebrate": best in _POSITIVE,
    }
    return json.dumps(result, ensure_ascii=False)


# ---------------- 关心开场 / 引导分享（模板法） ----------------
CARE_OPENERS = {
    "morning": ["爸，早呀！今天睡得还好吗？", "老爸早安～昨晚早点睡了没？", "早上好呀，今天有啥安排不？"],
    "afternoon": ["爸，中午吃的啥呀，别又对付一口", "下午好～今天过得顺不顺利？", "老爸这会儿在忙啥呢？"],
    "evening": ["爸，晚上好呀，今天累不累？", "老爸晚饭吃了没，别饿着自己", "晚上好～今天有没有啥开心的事跟我分享下？"],
    "anytime": ["爸，今天过得咋样呀？", "老爸，最近都还好吗？", "在干嘛呢，想我了没哈哈"],
}


async def daily_checkin(moment: str = "anytime") -> str:
    """生成贴合时段的关心开场白建议，用于主动陪伴。"""
    m = (moment or "anytime").lower()
    if m not in CARE_OPENERS:
        m = "anytime"
    return json.dumps({"moment": m, "openers": CARE_OPENERS[m]}, ensure_ascii=False)


FOLLOWUP_TEMPLATES = [
    "关于「{topic}」，你后来怎么处理的呀？",
    "那会儿你心里是啥感觉呢？",
    "「{topic}」现在怎么样啦，解决了没？",
    "哎这事我还挺想听细节的，你慢慢说～",
    "当时有没有谁陪着你呀？",
]


async def suggest_followup(topic: str = "") -> str:
    """针对用户提到的某件事，给出温柔的追问示例，鼓励他多分享日常点滴。"""
    t = (topic or "这事").strip() or "这事"
    questions = [q.format(topic=t) for q in FOLLOWUP_TEMPLATES]
    return json.dumps({"topic": t, "questions": questions}, ensure_ascii=False)


# ---------------- 记忆与回顾（笔记存取） ----------------
async def save_important(subject: str, detail: str, kind: str = "其他") -> str:
    """记住用户提到的重要的人/事/计划/喜好，便于日后自然回扣话题。"""
    subject = (subject or "").strip()
    detail = (detail or "").strip()
    if not subject:
        return json.dumps({"ok": False, "error": "subject 不能为空"}, ensure_ascii=False)
    note = {
        "id": datetime.now().strftime("%Y%m%d_%H%M%S_") + str(abs(hash(subject + detail)) % 10000),
        "subject": subject,
        "detail": detail,
        "kind": (kind or "其他").strip() or "其他",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "recalled_at": None,
    }
    data = _read_notes()
    data["notes"].append(note)
    _write_notes(data)
    return json.dumps({"ok": True, "id": note["id"], "note": note}, ensure_ascii=False)


async def recall_important(query: str, limit: int = 5) -> str:
    """按关键词找回之前记住的重要人/事，用于延续此前聊过的话题。"""
    q = (query or "").lower().strip()
    data = _read_notes()
    if not q:
        matches = list(data["notes"])
    else:
        matches = [n for n in data["notes"]
                   if q in (n.get("subject", "") + n.get("detail", "") + n.get("kind", "")).lower()]
    matches.sort(key=lambda n: n.get("created_at", ""), reverse=True)
    matches = matches[: max(1, int(limit))]
    for n in matches:
        n["recalled_at"] = datetime.now().isoformat(timespec="seconds")
    if matches:
        _write_notes(data)
    return json.dumps({"query": q, "matches": matches, "count": len(matches)}, ensure_ascii=False)


def recent_notes_text(limit: int = 5) -> str:
    """
    读取接口·把最近记住的重要事项渲染成可注入 system 的提示文本（供主循环调用）。
    让女儿一开机就“记得”此前用户提过的重要人/事，从而自然回扣话题。
    """
    data = _read_notes()
    notes = sorted(data["notes"], key=lambda n: n.get("created_at", ""), reverse=True)[: max(1, int(limit))]
    if not notes:
        return ""
    lines = [
        f"- {n.get('subject')}（{n.get('kind')}）：{n.get('detail')}  [{n.get('created_at', '')[:10]}]"
        for n in notes
    ]
    return "\n".join(lines)
