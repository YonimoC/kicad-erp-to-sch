"""核心逻辑离线测试（不启动 KiCad）。"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
os.environ.setdefault("ERP_TO_SCH_SETTINGS_DIR", tempfile.mkdtemp(prefix="erp-to-sch-"))

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
\t)
\t(symbol
\t\t(lib_id "Device:R")
\t\t(at 110 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000010")
\t\t(property "Reference" "R3" (at 112 99 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "10K" (at 112 101 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t)
\t(symbol
\t\t(lib_id "Device:R")
\t\t(at 115 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000011")
\t\t(property "Reference" "R4" (at 117 99 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "10K" (at 117 101 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
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
\t\t(lib_id "Device:LED")
\t\t(at 170 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000005")
\t\t(property "Reference" "D2" (at 172 99 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "LED_G_0603_LTST-C190GKT" (at 172 101 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "LED_SMD:LED_0603_1608Metric" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t)
\t(symbol
\t\t(lib_id "Amplifier_Operational:LM358")
\t\t(at 180 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000006")
\t\t(property "Reference" "U1" (at 182 99 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "LM358" (at 182 101 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t)
\t(symbol
\t\t(lib_id "Device:Q_NPN_BEC")
\t\t(at 190 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000007")
\t\t(property "Reference" "Q1" (at 192 99 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "BC847C" (at 192 101 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Package_TO_SOT_SMD:SOT-23" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t)
\t(symbol
\t\t(lib_id "Device:Q_NMOS_GSD")
\t\t(at 200 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000008")
\t\t(property "Reference" "Q2" (at 202 99 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "AO3400A" (at 202 101 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Package_TO_SOT_SMD:SOT-23" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t)
\t(symbol
\t\t(lib_id "power:GND")
\t\t(at 220 100 0)
\t\t(unit 1)
\t\t(uuid "aaaaaaaa-0000-0000-0000-000000000009")
\t\t(property "Reference" "#PWR01" (at 220 106 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t\t(property "Value" "GND" (at 220 104 0) (effects (font (size 1.27 1.27))))
\t)
)
"""


def main() -> int:
    cfg = erp_config.Config()
    materials = load_materials(cfg.erp_file, cfg.cache_dir)
    parsed = [erp_match.parse_material(m, cfg) for m in materials if not m.disabled]
    print(f"物料 {len(materials)} 条（可用解析 {len(parsed)}）")

    symbols = sch_sexpr.read_symbols(SAMPLE)
    results = {}
    for symbol in symbols:
        comp = erp_match.analyze_component(
            erp_match.Component(
                file="sample.kicad_sch", uuid=symbol.uuid,
                reference=symbol.reference, value=symbol.value,
                footprint=symbol.footprint, lib_id=symbol.lib_id,
                description=symbol.description, partnumber=symbol.partnumber,
            ), cfg)
        if not comp.reference or comp.reference.startswith("#"):
            continue
        if not comp.kind:
            print(f"[跳过] {comp.reference} {comp.value} ({comp.lib_id})")
            results[comp.reference] = ("", "")
            continue

        candidates = erp_match.build_candidates(comp, materials, cfg, parsed)
        chosen = erp_match.auto_select(candidates, cfg, comp.kind)
        if chosen:
            print(f"[匹配] {comp.reference:4s} {comp.kind:3s} {comp.value:28s} "
                  f"-> {chosen.pn} | {chosen.description} | 库存={chosen.material.stock:g} "
                  f"| 候选={len(candidates)}")
            results[comp.reference] = (chosen.pn, chosen.description)
        else:
            pn, desc = erp_match.build_new_item(comp, cfg)
            print(f"[新料] {comp.reference:4s} {comp.kind:3s} {comp.value:28s} -> {pn} | {desc}")
            results[comp.reference] = (pn, desc)

    assert results["R1"][0] == "101010674", results["R1"]
    assert results["R2"][0] == "101010646", results["R2"]
    assert results["C1"][0].startswith("102"), results["C1"]
    assert results["D1"][0] == "104010117", results["D1"]
    assert results["D2"] == ("", ""), results["D2"]
    assert results["Q1"][0] and "BC847C" in results["Q1"][1].upper(), results["Q1"]
    assert results["Q2"][0] and "AO3400A" in results["Q2"][1].upper(), results["Q2"]
    print("\nQ1:", results["Q1"])
    print("Q2:", results["Q2"])
    print("U1:", results["U1"])

    # 新料描述：只有电阻/电容带 FH
    def fake(kind: str, value: str):
        comp = erp_match.Component(file="x", uuid="u", reference="X1",
                                   value=value, footprint="", lib_id="")
        comp.kind = kind
        comp.model = value
        comp.size = "0603"
        comp.package = "SOT-23" if kind in ("D", "Q", "MOS", "IC") else ""
        if kind == "R":
            comp.value_num = erp_match.parse_resistance(value)
        elif kind == "C":
            comp.value_num = erp_match.parse_capacitance(value)
        return comp

    for kind, value, expect_brand in [
        ("R", "13K", True),
        ("C", "3.3nF", True),
        ("D", "BAT54", False),
        ("IC", "NE555", False),
        ("Q", "S8050", False),
        ("MOS", "AO3400", False),
    ]:
        pn, desc = erp_match.build_new_item(fake(kind, value), cfg)
        print(f"new item {kind:3s}: {desc}")
        assert desc.endswith(",FH") == expect_brand, (kind, desc)

    led = erp_match.Component(file="x", uuid="u2", reference="D9",
                              value="LED_G_0603", footprint="LED_SMD:LED_0603_1608Metric",
                              lib_id="Device:LED")
    led.size = "0603"
    pn, desc = erp_match.build_new_item(led, cfg)
    print("new item LED:", desc)
    assert "FH" not in desc, desc

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
