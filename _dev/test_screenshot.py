"""界面截图测试：把对话框渲染成 PNG，便于检查布局。"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
sys.path.insert(0, str(Path(os.environ.get("TEMP", "")) / "erptest" / "Lib" / "site-packages"))
os.environ.setdefault("ERP_TO_SCH_SETTINGS_DIR", tempfile.mkdtemp(prefix="erp-shot-"))

import wx  # noqa: E402


def main() -> int:
    out = ROOT / "_dev" / "gui.png"
    app = wx.App(False)
    import erp_gui

    dlg = erp_gui.ErpToSchDialog(None)
    dlg.Show()

    # 造几行数据，方便看表格样式
    from erp_match import Component, analyze_component
    rows = []
    R1 = analyze_component(Component(file="a.kicad_sch", uuid="u1", reference="R1", value="10K",
                                    footprint="Resistor_SMD:R_0603_1608Metric", lib_id="Device:R"),
                           dlg.config)
    rows.append(dlg._match_component(R1))
    C1 = analyze_component(Component(file="a.kicad_sch", uuid="u2", reference="C1", value="100nF",
                                    footprint="Capacitor_SMD:C_0603_1608Metric", lib_id="Device:C"),
                           dlg.config)
    rows.append(dlg._match_component(C1))
    R9 = analyze_component(Component(file="a.kicad_sch", uuid="u3", reference="R99", value="13K",
                                    footprint="Resistor_SMD:R_0402_1005Metric", lib_id="Device:R"),
                           dlg.config)
    rows.append(dlg._match_component(R9))
    dlg.rows = rows
    dlg._refresh_list()

    for _ in range(30):
        app.Yield()
        dlg.Update()

    rect = dlg.GetScreenRect()
    bmp = wx.Bitmap(rect.width, rect.height)
    mem = wx.MemoryDC(bmp)
    mem.Blit(0, 0, rect.width, rect.height, wx.ScreenDC(), rect.x, rect.y)
    mem.SelectObject(wx.NullBitmap)
    bmp.SaveFile(str(out), wx.BITMAP_TYPE_PNG)
    print("截图:", out)

    dlg.Destroy()
    app.Destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
