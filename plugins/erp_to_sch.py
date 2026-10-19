"""KiCad IPC 插件入口（原理图编辑器工具栏按钮）。

KiCad 通过 plugin.json 里的 entrypoint 用独立 Python 进程运行本脚本，
环境变量里带有 KICAD_API_SOCKET / KICAD_API_TOKEN。
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))


def _log_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    folder = Path(base) / "kicad-erp-to-sch"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = PLUGIN_DIR
    return folder / "plugin.log"


def _log(message: str) -> None:
    try:
        with open(_log_path(), "a", encoding="utf-8") as fp:
            fp.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")
    except Exception:
        pass


def main() -> int:
    _log("=" * 60)
    _log(f"启动插件: python={sys.executable}")
    _log(f"插件目录: {PLUGIN_DIR}")
    try:
        import erp_gui

        return erp_gui.run()
    except Exception:
        detail = traceback.format_exc()
        _log(detail)
        try:
            import wx

            app = wx.App(False)
            wx.MessageBox(f"ERP 物料匹配插件启动失败：\n\n{detail}",
                          "ERP 物料匹配", wx.OK | wx.ICON_ERROR)
            app.Destroy()
        except Exception:
            print(detail, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
