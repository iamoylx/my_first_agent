# skills/code_tools/skill.py
# 让 agent 能“看自己所在文件夹的代码” + “用命令行简单操作电脑”。
# 所有函数为 async（主循环 await func(**args) 调用）。
import os
import re
import json
import asyncio

# 项目根目录：skills/code_tools/skill.py -> 上层 skills -> 上层项目根
_HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))

# 遍历/搜索时跳过的目录，避免翻进 .git / 缓存 / 大依赖
_SKIP_DIRS = {".git", "__pycache__", ".workbuddy", "node_modules", ".venv", "venv"}

# run_command 危险指令拦截（基础防护，非沙箱）：命中即拒绝执行并返回提示。
# 如需放开某些指令，删/改下面的正则即可。
_DENY = [
    re.compile(r"\brm\s+-[a-z]*[rR]", re.I),        # rm -r / rm -rf（递归删除）
    re.compile(r"\brm\s+-[a-z]*[fF]", re.I),        # rm -f（强制删除）
    re.compile(r"rmdir\s+/[sq]", re.I),             # Windows rmdir /s /q
    re.compile(r"del\s+/[sq]", re.I),               # Windows del /s /q
    re.compile(r"\bformat\b", re.I),                # 格式化磁盘
    re.compile(r"\bmkfs", re.I),                    # 建文件系统
    re.compile(r"\bdd\b.*\bif=", re.I),             # dd 写磁盘
    re.compile(r"\bshutdown\b", re.I),              # 关机
    re.compile(r"\breboot\b", re.I),                # 重启
    re.compile(r"\bpoweroff\b", re.I),              # 断电
    re.compile(r":\(\)\s*\{", re.I),               # fork bomb
    re.compile(r"\bsudo\b", re.I),                 # 提权
    re.compile(r">\s*/dev/sd", re.I),              # 直写磁盘设备
    re.compile(r"\bcurl\b.*\|\s*(sh|bash)", re.I), # 下载即执行
    re.compile(r"\bwget\b.*\|\s*(sh|bash)", re.I), # 下载即执行
]


def _resolve(rel: str) -> str:
    """相对路径按项目根目录解析；绝对路径原样使用（允许查看/操作任意绝对路径）。"""
    if os.path.isabs(rel):
        return rel
    return os.path.normpath(os.path.join(PROJECT_ROOT, rel))


def _check_blocked(cmd: str):
    """返回命中的危险模式字符串；未命中返回 None。"""
    for pat in _DENY:
        if pat.search(cmd):
            return pat.pattern
    return None


# ---- 输出清洗：消除“聊到代码/本体程序就乱码”的源头 ----
# Windows 下 shell/命令输出常带 ANSI 转义（彩色）与 OEM 编码控制字符，
# 直接塞进上下文会让模型复述成乱码；这里统一清除并做编码兜底。
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB0-1]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")   # 保留 \n \r \t


def _sanitize(text: str) -> str:
    """去除 ANSI 转义与不可打印控制字符，折叠多余空行。中文与正常换行不受影响。"""
    if not text:
        return text
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode(raw: bytes) -> str:
    """多编码兜底解码（Windows 控制台常为 GBK/cp936），最终失败用 replace 不崩。"""
    for enc in ("utf-8", "cp936", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


async def read_file(path: str, max_lines: int = 200, start_line: int = 1) -> str:
    """读取项目内文件内容，用于查看源码/配置。返回带行号范围标记的文本。"""
    if not path:
        return json.dumps({"error": "path 不能为空"}, ensure_ascii=False)
    fp = _resolve(path)
    if not os.path.isfile(fp):
        return json.dumps({"error": f"文件不存在：{path}"}, ensure_ascii=False)
    try:
        with open(fp, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError as e:
        return json.dumps({"error": f"读取失败：{e}"}, ensure_ascii=False)
    total = len(lines)
    start = max(1, int(start_line))
    end = min(total, start + int(max_lines) - 1)
    snippet = "".join(lines[start - 1:end])
    if len(snippet) > 4000:
        snippet = snippet[:4000] + "\n...(内容过长，仅显示前 4000 字符)"
    meta = f"[文件 {path}：共 {total} 行，显示第 {start}-{end} 行]\n"
    return _sanitize(meta + snippet)


async def list_dir(path: str = ".") -> str:
    """列出目录下的文件与子目录，浏览项目结构。"""
    root = _resolve(path)
    if not os.path.isdir(root):
        return json.dumps({"error": f"路径不是目录：{path}"}, ensure_ascii=False)
    entries = []
    for name in sorted(os.listdir(root)):
        full = os.path.join(root, name)
        entries.append({"name": name, "type": "dir" if os.path.isdir(full) else "file"})
    rel = os.path.relpath(root, PROJECT_ROOT) or "."
    return _sanitize(json.dumps({"path": rel, "entries": entries}, ensure_ascii=False, indent=2))


async def search_files(name_pattern: str, path: str = ".") -> str:
    """按文件名或通配符（如 *.py、AGENT.py）在项目内查找文件。"""
    import glob
    root = _resolve(path)
    if not os.path.isdir(root):
        return json.dumps({"error": f"路径不是目录：{path}"}, ensure_ascii=False)
    found = []
    if any(c in name_pattern for c in "*?["):
        pattern = os.path.join(root, "**", name_pattern)
        found = glob.glob(pattern, recursive=True)
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if name_pattern in fn or fn == name_pattern:
                    found.append(os.path.join(dirpath, fn))
    found = sorted(os.path.relpath(f, PROJECT_ROOT) for f in found)
    if not found:
        return _sanitize(json.dumps({"message": f"未找到匹配 {name_pattern!r} 的文件"}, ensure_ascii=False))
    return _sanitize(json.dumps({"files": found, "count": len(found)}, ensure_ascii=False, indent=2))


async def search_content(keyword: str, path: str = ".", max_matches: int = 50) -> str:
    """在文件内容中搜索关键词/正则，定位代码或配置（类似 grep）。"""
    if not keyword:
        return json.dumps({"error": "keyword 不能为空"}, ensure_ascii=False)
    root = _resolve(path)
    if not os.path.isdir(root):
        return json.dumps({"error": f"路径不是目录：{path}"}, ensure_ascii=False)
    try:
        pattern = re.compile(keyword, re.I)
    except re.error:
        pattern = None  # 非法正则则退化为子串匹配
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if len(results) >= max_matches:
                break
            fp = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(fp) > 2_000_000:
                    continue  # 跳过超大文件
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if pattern:
                            hit = pattern.search(line)
                        else:
                            hit = keyword.lower() in line.lower()
                        if hit:
                            results.append(
                                f"{os.path.relpath(fp, PROJECT_ROOT)}:{i}: {line.rstrip()}"
                            )
                            if len(results) >= max_matches:
                                break
            except (OSError, UnicodeDecodeError):
                continue
    if not results:
        return _sanitize(json.dumps({"message": f"未找到匹配 {keyword!r} 的内容"}, ensure_ascii=False))
    text = json.dumps({"matches": results, "count": len(results)}, ensure_ascii=False, indent=2)
    if len(text) > 2500:
        text = text[:2500] + "\n...(匹配过多，仅显示前 2500 字符)"
    return _sanitize(text)


async def run_command(command: str, timeout: int = 30) -> str:
    """在 agent 运行目录执行一条命令行指令并返回输出（简单操作电脑）。"""
    if not command or not command.strip():
        return json.dumps({"error": "command 不能为空"}, ensure_ascii=False)
    blocked = _check_blocked(command)
    if blocked:
        return json.dumps(
            {"error": f"出于安全考虑已拦截该指令（命中危险模式 {blocked!r}）。"
                      f"如需放开请调整 skills/code_tools/skill.py 的 _DENY 列表。"},
            ensure_ascii=False,
        )
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=PROJECT_ROOT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return json.dumps({"error": f"命令超时（>{timeout}s）已被强制结束"}, ensure_ascii=False)
        text = _decode(out)
        text = _sanitize(text)
        if len(text) > 2000:
            text = "...(输出过长，仅显示末尾 2000 字符)\n" + text[-2000:]
        return json.dumps(
            {"command": command, "returncode": proc.returncode, "output": text},
            ensure_ascii=False, indent=2,
        )
    except Exception as e:
        return json.dumps({"error": f"命令执行失败：{e}"}, ensure_ascii=False)
