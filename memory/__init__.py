# memory 包：agent 的三层记忆【机制代码】（硬板接口）
# 本包只含代码，不含任何数据文件——本地记忆数据统一落在项目根的 memory_data/。
# - store.py        : 统一对外接口 MemoryStore（路径由 base_dir 驱动，默认 memory_data/）
# - token_window.py : STM 短期，token 滑动窗口 prune()
# - sessions.py     : MTM 中期纯函数（sanitize 清洗 / summarize_session 摘要）
# - profile.py      : LTM 长期纯函数（merge_facts 合并 / to_context_text 渲染 / extract 抽取）
#
# 数据目录 memory_data/（硬盘）：profile.json / sessions/ / users/<id>/，可整体提取迁移。
