# skills/memory_tools/skill.py
# 记忆读写工具实现：write_memory / save_important / recall_important
# mem（MemoryStore）由 core.process_turn 按签名注入——工具函数若声明 mem 参数，
# process_turn 调用时会自动传入当前会话的 MemoryStore，保证读写的是用户自己的记忆。
async def write_memory(mem=None, key="", value="", fact_type="fact", confidence=0.9,
                       text="", content="", note="", **extra) -> str:
    """把一条事实/偏好写入长期记忆档案卡（LTM）。兼容 save_important 类工具传 text/content/note。"""
    if mem is None:
        return "错误：记忆系统不可用（未注入 MemoryStore）"
    if not key:
        key = "important_note"
    if not value:
        value = text or content or note
    key = (key or "").strip()
    value = str(value or "").strip()
    if not key or not value:
        return "错误：key 和 value 都不能为空"
    ftype = fact_type if fact_type in ("fact", "preference") else "fact"
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.9
    conf = max(0.0, min(1.0, conf))

    extracted = [{"key": key, "value": value, "confidence": conf, "type": ftype}]
    profile, changed = mem.update_profile(extracted)
    if changed:
        return f"已写入长期记忆：{key} = {value}（{ftype}，confidence={conf:.2f}）"
    return f"该记忆已存在且置信度不低于新值，未覆盖：{key} = {value}"


async def save_important(mem=None, text="", content="", note="", title="", **extra) -> str:
    """把一条重要事项追加保存到长期记忆 important_notes（可多条，按序保留）。"""
    if mem is None:
        return "错误：记忆系统不可用（未注入 MemoryStore）"
    item = (text or content or note or title or "").strip()
    if not item:
        return "错误：内容为空"
    profile = mem.load_profile()
    old = profile.get("facts", {}).get("important_notes", {}).get("value")
    if isinstance(old, list):
        notes = old + [item]
    elif isinstance(old, str) and old.strip():
        notes = [old, item]
    else:
        notes = [item]
    extracted = [{"key": "important_notes", "value": notes, "confidence": 0.95, "type": "fact"}]
    profile, changed = mem.update_profile(extracted)
    return f"已保存重要事项（当前共 {len(notes)} 条）。"


async def recall_important(mem=None, **extra) -> str:
    """读取长期记忆里保存的重要事项列表。"""
    if mem is None:
        return "错误：记忆系统不可用（未注入 MemoryStore）"
    profile = mem.load_profile()
    notes = profile.get("facts", {}).get("important_notes", {}).get("value", [])
    if not notes:
        return "暂时没有保存的重要事项。"
    if isinstance(notes, list):
        lines = [f"{i+1}. {n}" for i, n in enumerate(notes)]
        return "已保存的重要事项：\n" + "\n".join(lines)
    return f"已保存的重要事项：{notes}"
