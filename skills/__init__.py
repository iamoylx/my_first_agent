# skills/__init__.py
def collect_tools(*pairs):
    """
    聚合多个技能的工具定义与实现。
    每个技能传入一对 (TOOLS 列表, TOOL_MAP 字典)，
    这里把它们合并成全局统一的两份清单。
    """
    tools, tool_map = [], {}
    for tool_schemas, func_map in pairs:
        tools.extend(tool_schemas)   # 把各技能的 schema 追加进总表
        tool_map.update(func_map)    # 把各技能的函数并入总表
    return tools, tool_map
