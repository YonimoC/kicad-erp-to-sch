"""与运行中的 KiCad 通信，拿到当前打开的原理图路径。

KiCad 10 的原理图 API 只提供 "打开文档的文件名"（不含目录），
因此这里再用 KiCad 的 `kicad.json`（open_projects / mru_path）和上次使用的工程目录兜底。
"""

from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_kicad = None
_import_error: Optional[str] = None


def get_kicad():
    """返回 KiCad IPC 客户端（失败返回 None）。"""
    global _kicad, _import_error
    if _kicad is not None:
        return _kicad
    if _import_error is not None:
        return None
    try:
        from kipy import KiCad

        _kicad = KiCad()
        return _kicad
    except Exception as exc:  # noqa: BLE001
        _import_error = str(exc)
        return None


def last_error() -> str:
    return _import_error or ""


def get_open_schematics() -> List[Tuple[str, str]]:
    """返回所有打开的原理图文档 [(文件名, 工程目录), ...]。"""
    kicad = get_kicad()
    if kicad is None:
        return []
    result: List[Tuple[str, str]] = []
    try:
        from kipy.proto.common.types import base_types_pb2

        docs = kicad.get_open_documents(base_types_pb2.DocumentType.Value("DOCTYPE_SCHEMATIC"))
        for doc in docs or []:
            filename = doc.board_filename or ""
            project_dir = ""
            try:
                project_dir = kicad.get_project(doc).path or ""
            except Exception:
                project_dir = ""
            result.append((filename, project_dir))
    except Exception as exc:  # noqa: BLE001
        global _import_error
        _import_error = str(exc)
        return []
    return result


def _pick_open_document(docs: List[Tuple[str, str]]) -> Tuple[str, str]:
    """多个工程同时打开时，挑选“当前”那个。

    优先看可见的原理图窗口标题里包含哪个文件名；都对不上时取最近修改的工程。
    """
    if not docs:
        return "", ""
    if len(docs) == 1:
        return docs[0]

    titles: List[str] = []
    try:
        from kicad_ui import schematic_window_titles

        titles = [t.lower() for t in schematic_window_titles()]
    except Exception:
        titles = []

    if titles:
        for name, project_dir in docs:
            stem = os.path.splitext(os.path.basename(name or ""))[0].lower()
            if stem and any(stem in title for title in titles):
                return name, project_dir

    best = docs[0]
    best_mtime = -1.0
    for name, project_dir in docs:
        root = find_root_schematic(project_dir) if project_dir else ""
        if not root:
            continue
        try:
            mtime = os.path.getmtime(root)
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = (name, project_dir), mtime
    return best


def get_open_schematic() -> Tuple[str, str]:
    """返回 (原理图文件名, 工程目录)；多个工程时挑当前工程。"""
    return _pick_open_document(get_open_schematics())


# --------------------------------------------------------------------------
# KiCad 用户配置目录
# --------------------------------------------------------------------------
def _config_root() -> Path:
    override = os.environ.get("KICAD_CONFIG_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "kicad"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "kicad"
    return Path.home() / ".config" / "kicad"


def _kicad_json_candidates() -> List[Path]:
    root = _config_root()
    found = list(root.glob("*/kicad.json"))
    # 版本号大的优先
    def version_key(path: Path) -> Tuple[int, ...]:
        try:
            return tuple(int(p) for p in path.parent.name.split("."))
        except ValueError:
            return (0,)
    found.sort(key=version_key, reverse=True)
    return found


def _load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def open_project_dirs() -> List[str]:
    """KiCad 最近/当前打开的工程目录，按可信度排序。"""
    dirs: List[str] = []
    for cfg in _kicad_json_candidates():
        data = _load_json(cfg)

        for item in data.get("open_projects") or []:
            if isinstance(item, str):
                path = item
            elif isinstance(item, dict):
                path = item.get("path") or item.get("file") or ""
            else:
                path = ""
            if path:
                dirs.append(os.path.dirname(os.path.abspath(path)))

        mru = data.get("mru_path") or ""
        if mru:
            dirs.append(os.path.abspath(mru))
        if dirs:
            break

    seen: List[str] = []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.append(d)
    return seen


def process_hint_dirs() -> List[str]:
    """从父进程（KiCad / eeschema）的工作目录与命令行推断工程目录。

    IPC 插件进程是由 KiCad 启动的子进程，父进程的工作目录通常就是工程目录；
    命令行里也会带上打开的文件路径。拿不到 psutil 时直接返回空。
    """
    hints: List[str] = []
    try:
        import psutil

        proc = psutil.Process()
        for _ in range(4):
            proc = proc.parent()
            if proc is None:
                break
            try:
                name = (proc.name() or "").lower()
            except Exception:
                name = ""
            if not any(k in name for k in ("eeschema", "kicad", "pcbnew")):
                continue
            try:
                cwd = proc.cwd()
                if cwd:
                    hints.append(cwd)
            except Exception:
                pass
            try:
                for arg in proc.cmdline() or []:
                    if arg.lower().endswith((".kicad_pro", ".kicad_sch")):
                        hints.append(os.path.dirname(os.path.abspath(arg)))
            except Exception:
                pass
            try:
                # KiCad 正开着的工程文件/原理图文件（最可靠的线索）
                for opened in proc.open_files():
                    path = opened.path
                    if path.lower().endswith((".kicad_pro", ".kicad_sch")):
                        hints.append(os.path.dirname(os.path.abspath(path)))
            except Exception:
                pass
    except Exception:
        pass

    seen: List[str] = []
    for d in hints:
        if d and d not in seen and os.path.isdir(d):
            seen.append(d)
    return seen


def find_schematic(filename: str, extra_dirs: Optional[List[str]] = None) -> str:
    """根据文件名在各候选目录里找原理图完整路径。"""
    if not filename:
        return ""
    dirs: List[str] = []
    for d in (extra_dirs or []):
        if d:
            dirs.append(d)
    dirs.extend(process_hint_dirs())
    dirs.extend(open_project_dirs())

    for d in dirs:
        candidate = os.path.join(d, filename)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return ""


def find_project_file(schematic_path: str) -> str:
    """找同一目录下的 .kicad_pro（用于显示工程名）。"""
    if not schematic_path:
        return ""
    folder = os.path.dirname(schematic_path)
    stem = os.path.splitext(os.path.basename(schematic_path))[0]
    direct = os.path.join(folder, stem + ".kicad_pro")
    if os.path.isfile(direct):
        return direct
    found = glob.glob(os.path.join(folder, "*.kicad_pro"))
    return found[0] if found else ""


def find_root_schematic(folder: str) -> str:
    """在工程目录里找根原理图（没有被其他图纸引用的那张）。"""
    if not folder or not os.path.isdir(folder):
        return ""
    files = sorted(glob.glob(os.path.join(folder, "*.kicad_sch")))
    if not files:
        return ""
    if len(files) == 1:
        return os.path.abspath(files[0])

    referenced = set()
    try:
        import sch_sexpr

        for path in files:
            try:
                text = sch_sexpr.load_file(path)
                for name in sch_sexpr.sheet_files(text):
                    referenced.add(os.path.normcase(
                        os.path.abspath(os.path.join(folder, name))))
            except Exception:
                continue
    except Exception:
        pass

    roots = [f for f in files if os.path.normcase(os.path.abspath(f)) not in referenced]
    if not roots:
        roots = files

    projects = glob.glob(os.path.join(folder, "*.kicad_pro"))
    stem = (os.path.splitext(os.path.basename(projects[0]))[0] if projects
            else os.path.basename(os.path.abspath(folder)))
    for path in roots:
        if os.path.splitext(os.path.basename(path))[0].lower() == stem.lower():
            return os.path.abspath(path)

    roots.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return os.path.abspath(roots[0])
