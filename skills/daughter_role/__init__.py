# skills/daughter_role/__init__.py
# 女儿陪伴角色技能：对外暴露 TOOLS + TOOL_MAP（工具），以及 PERSONA_PROMPT / recent_notes_text（人格注入）。
from .schema import TOOLS
from .skill import (detect_mood, save_important, recall_important,
                    daily_checkin, suggest_followup, recent_notes_text)
from .persona import PERSONA_PROMPT

TOOL_MAP = {
    "detect_mood": detect_mood,
    "save_important": save_important,
    "recall_important": recall_important,
    "daily_checkin": daily_checkin,
    "suggest_followup": suggest_followup,
}
