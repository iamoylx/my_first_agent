# memory 包：agent 的三层记忆实现
# - token_window.py : STM 短期，token 滑动窗口 prune()
# - sessions.py     : MTM 中期，会话持久化（落盘/读回，不进仓库）
# - profile.py      : LTM 长期，结构化档案卡（状态复写，profile.json 不进仓库）
