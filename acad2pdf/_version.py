__version__ = "0.7.0"
"""自动版本管理 — 版本号唯一来源，写回 acad2pdf/_version.py。

规则（git 仓库内）：
    __version__ = MAJOR.MINOR.PATCH  [+dirty]
    - MAJOR/MINOR/PATCH 取仓库里最新 v* tag（无 tag 则从 0.5.0 起）
    - 根据最新 tag 之后的 conventional commit 前缀决定升级档位：
        breaking!/BREAKING CHANGE  → MAJOR + 1
        feat                       → MINOR + 1
        fix/chore/docs/... 其余    → PATCH + 1
      有改动 → 进位并清零低位；无改动（HEAD 恰在 tag 上）→ 版本号不变
    - 工作区有未提交改动 → 追加 dirty 标记
非 git 环境（如打包分发）→ 读 _version.py 里已固化的版本号。

用法：
    代码中   from ._version import __version__
    命令行   python -m acad2pdf._version   # 打印当前版本并固化
"""

import logging
import os
import re
import subprocess

log = logging.getLogger("acad2pdf")

_FILE = os.path.abspath(__file__)
_PKG_DIR = os.path.dirname(_FILE)
_ROOT = os.path.dirname(_PKG_DIR)          # 仓库根目录
_FALLBACK = "0.0.0"

_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _run_git(*args):
    """在仓库根目录执行 git，失败返回 None。"""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=_ROOT,
            capture_output=True,
            timeout=10,
        )
        if out.returncode != 0:
            return None
        return out.stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        return None


def _latest_tag():
    """取最新 v* 标签 → (major, minor, patch, tag)，无则 (0,5,0,None)。"""
    out = _run_git("tag", "--list", "v*", "--sort=-v:refname")
    if out is None:
        out = _run_git("tag", "--list", "[0-9]*.[0-9]*.[0-9]*", "--sort=-v:refname")
    if not out:
        return (0, 5, 0, None)
    first = out.splitlines()[0].strip()
    m = _TAG_RE.match(first)
    if not m:
        return (0, 5, 0, None)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), first)


def _commits_since(tag):
    """tag..HEAD 的提交信息列表；tag=None 表示全部历史。"""
    rng = f"{tag}..HEAD" if tag else "HEAD"
    out = _run_git("log", rng, "--pretty=format:%s")
    if out is None:
        return []
    return [l.strip() for l in out.splitlines() if l.strip()]


def _is_dirty():
    out = _run_git("status", "--porcelain")
    if out is None:
        return False
    for line in out.splitlines():
        # 忽略版本文件自身（写入版本号会造成自我指涉的脏状态）
        if "_version.py" in line:
            continue
        if line.strip():
            return True
    return False


def _bump(maj, minr, pat, subjects):
    """根据提交前缀决定升级档位。"""
    level = 0  # 0=不变 1=patch 2=minor 3=major
    for s in subjects:
        sl = s.lower()
        if "breaking change" in sl or re.match(r"^[a-z]+(\(.+\))?!:", sl):
            level = max(level, 3)
        elif re.match(r"^feat(\(.+\))?:", sl):
            level = max(level, 2)
        elif re.match(r"^[a-z]+(\(.+\))?:", sl):  # fix/chore/docs/perf/refactor...
            level = max(level, 1)
    if level == 3:
        return (maj + 1, 0, 0)
    if level == 2:
        return (maj, minr + 1, 0)
    if level == 1:
        return (maj, minr, pat + 1)
    return (maj, minr, pat)


def compute_version():
    """计算当前版本号字符串。"""
    if _run_git("rev-parse", "--is-inside-work-tree") is None:
        return _read_pinned() or _FALLBACK
    maj, minr, pat, tag = _latest_tag()
    subjects = _commits_since(tag)
    maj, minr, pat = _bump(maj, minr, pat, subjects)
    ver = f"{maj}.{minr}.{pat}"
    if _is_dirty():
        ver += "+dirty"
    return ver


def _read_pinned():
    """读取本文件顶部已固化的 __version__（非 git 环境用）。"""
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(r'^__version__\s*=\s*"([^"]+)"', line.strip())
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


def _pin(ver):
    """把 __version__ = "..." 固化写入本文件顶部（幂等）。"""
    marker = '__version__ = '
    with open(_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    new_line = f'__version__ = "{ver}"\n'
    if lines and lines[0].startswith(marker):
        if lines[0] == new_line:
            return
        lines[0] = new_line
    else:
        lines.insert(0, new_line)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, _FILE)


def _resolve():
    """计算版本并固化，返回版本字符串。任何失败都回退，不抛异常。"""
    try:
        ver = compute_version()
        _pin(ver)
        return ver
    except Exception as e:
        log.warning("版本计算失败，使用回退值: %s", e)
        return _read_pinned() or _FALLBACK


__version__ = _resolve()


if __name__ == "__main__":
    print(__version__)
