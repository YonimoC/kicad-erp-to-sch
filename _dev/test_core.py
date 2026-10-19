"""核心逻辑离线测试（不启动 KiCad）。

运行:
    set PYTHONPATH=%TEMP%\erptest\Lib\site-packages
    D:\KiCad\10.0\bin\python.exe _dev\test_core.py
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
os.environ.setdefault("ERP_TO_SCH_SETTINGS_DIR", tempfile.mkdtemp(prefix="erp-to-sch-"))

# KiCad 自带的 python 不读 PYTHONPATH，这里手动加上测试依赖目录
_extra = Path(os.environ.get("TEMP", "")) / "erptest" / "Lib" / "site-packages"
if _extra.is_dir() and str(_extra) not in sys.path:
    sys.path.insert(0, str(_extra))

import erp_config  # noqa: E402
import erp_match  # noqa: E402
import sch_sexpr  # noqa: E402
from erp_data import load_materials  # noqa: E402

SAMPLE = """(kicad_sch
\t(version 20230121)
\t(generator eeschema)
\t(uuid "11111111-1111-1111-1111-111111111111")
\t(paper "A4")
\t(lib_symbols
\t\t(symbol "Device:R"
\t\t\t(property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
\t\t\t(property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))
\t\t)
\t)
\t(symbol
\t\t(lib_id "Device:R")
\t\t(at 100 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000001")
\t\t(property "Reference" "R1" (at 102 99 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "10K" (at 102 101 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t\t(property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t)
\t(symbol
\t\t(lib_id "Device:R")
\t\t(at 120 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000002")
\t\t(property "Reference" "R2" (at 122 99 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "10K 1%" (at 122 101 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Resistor_SMD:R_0402_1005Metric" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t\t(property "PartNumber" "OLD-PN" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t)
\t(symbol
\t\t(lib_id "Device:C")
\t\t(at 140 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000003")
\t\t(property "Reference" "C1" (at 142 99 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "100nF" (at 142 101 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Capacitor_SMD:C_0603_1608Metric" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t)
\t(symbol
\t\t(lib_id "Device:D_Schottky")
\t\t(at 160 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000004")
\t\t(property "Reference" "D1" (at 162 99 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "SS14" (at 162 101 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Diode_SMD:D_SOD-123" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t)
\t(symbol
\t\t(lib_id "Device:R")
\t\t(at 180 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000005")
\t\t(property "Reference" "R3" (at 182 99 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "4K7" (at 182 101 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Resistor_SMD:R_0805_2012Metric" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t)
\t(symbol
\t\t(lib_id "power:GND")
\t\t(at 200 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000006")
\t\t(property "Reference" "#PWR01" (at 200 106 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t\t(property "Value" "GND" (at 200 104 0) (effects (font (size 1.27 1.27))))
\t)
)
"""


def main() -> int:
    cfg = erp_config.Config()
    print("配置:", cfg.path)

    symbols = sch_sexpr.read_symbols(SAMPLE)
    print(f"解析到 {len(symbols)} 个 symbol 实例")
    for s in symbols:
        print(" ", s.reference, s.value, s.footprint, "PN=", s.partnumber or "-")

    materials = load_materials(cfg.erp_file, cfg.cache_dir)
    print(f"物料 {len(materials)} 条")

    edits = []
    for symbol in symbols:
        comp = erp_match.analyze_component(
            erp_match.Component(
                file="sample.kicad_sch", uuid=symbol.uuid,
                reference=symbol.reference, value=symbol.value,
                footprint=symbol.footprint, lib_id=symbol.lib_id,
                description=symbol.description, partnumber=symbol.partnumber,
            ), cfg)
        if not comp.kind:
            print(f"[跳过] {comp.reference} ({comp.lib_id})")
            continue
        candidates = erp_match.build_candidates(comp, materials, cfg)
        chosen = erp_match.auto_select(candidates, cfg)
        if chosen:
            print(f"[匹配] {comp.reference} {comp.value} {comp.size} -> {chosen.pn} "
                  f"{chosen.description} | 品牌={chosen.brand} 库存={chosen.material.stock:g} "
                  f"候选={len(candidates)}")
        else:
            pn, desc = erp_match.build_new_item(comp, cfg)
            print(f"[新料] {comp.reference} {comp.value} {comp.size} -> {pn} {desc} "
                  f"候选={len(candidates)}")
            chosen = None
        if chosen:
            edits.append(sch_sexpr.plan_set_property(SAMPLE, symbol, "PartNumber", chosen.pn))
            edits.append(sch_sexpr.plan_set_property(SAMPLE, symbol, "Description", chosen.description))

    out = sch_sexpr.apply_edits(SAMPLE, edits)
    print("\n--- 写入后片段 ---")
    for line in out.splitlines():
        if "property" in line and ("Reference" in line or "PartNumber" in line or "Description" in line):
            print(line)

    # 再解析一次确认字段生效
    symbols2 = sch_sexpr.read_symbols(out)
    for s in symbols2:
        if s.reference.startswith("#"):
            continue
        print(f"复核 {s.reference}: PN={s.partnumber!r} DESC={s.description!r}")

    # 解析完整性：无修改时输出应与输入一致
    assert sch_sexpr.apply_edits(SAMPLE, []) == SAMPLE
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
