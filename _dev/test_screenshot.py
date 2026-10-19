"""界面截图：主界面 + 物料搜索框。"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(os.environ.get("TEMP", "")) / "erptest" / "Lib" / "site-packages"))
os.environ.setdefault("ERP_TO_SCH_SETTINGS_DIR", tempfile.mkdtemp(prefix="erp-shot-"))

import wx  # noqa: E402

from test_core import SAMPLE  # noqa: E402


def capture(window: wx.Window, path: Path) -> None:
    rect = window.GetScreenRect()
    bmp = wx.Bitmap(rect.width, rect.height)
    mem = wx.MemoryDC(bmp)
    mem.Blit(0, 0, rect.width, rect.height, wx.ScreenDC(), rect.x, rect.y)
    mem.SelectObject(wx.NullBitmap)
    bmp.SaveFile(str(path), wx.BITMAP_TYPE_PNG)
    print("screenshot:", path)


def main() -> int:
    app = wx.App(False)
    import erp_gui

    dlg = erp_gui.ErpToSchDialog(None)
    work = Path(tempfile.mkdtemp(prefix="erp-shot-sch-"))
    (work / "demo.kicad_sch").write_text(SAMPLE, encoding="utf-8")
    dlg.txt_project.SetValue(str(work))
    dlg._scan()
    dlg.Show()
    for _ in range(30):
        app.Yield()
        dlg.Update()
    capture(dlg, ROOT / "_dev" / "gui.png")

    search = erp_gui.MaterialSearchDialog(dlg, dlg.materials, "10K")
    search.Show()
    for _ in range(20):
        app.Yield()
        search.Update()
    capture(search, ROOT / "_dev" / "gui_search.png")
    search.Destroy()

    dlg.Destroy()
    app.Destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
