"""内置《物料参数要求表》（完整 85 类）。

数据来自 `erp_rules_data.json`（由 ERP 的《物料参数要求表》导出，随插件一起发布），
每类包含：参数顺序（order）与示例（example）。

用途：
* 界面「参数要求…」里查看完整表；
* 新建物料时按所选类别的字段顺序自动拼描述；
* 未自动匹配的元件按 lib_id / 型号猜一个最接近的类别。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

DATA_FILE = Path(__file__).resolve().parent / "erp_rules_data.json"

#: 找不到数据文件时的最小兜底
_FALLBACK_ROWS: List[Dict[str, str]] = [
    {"name": "贴片电阻", "order": "料号名称、尺寸、阻值、误差、功率、品牌",
     "example": "贴片电阻,0603,680Ω,±1%,1/10W,SUP"},
    {"name": "贴片电容", "order": "料号名称、尺寸、容值、误差、材质、耐压、品牌",
     "example": "贴片电容,0603,100nF,50V,±10%,X7R,SAMSUNG"},
    {"name": "贴片二极管", "order": "料号名称、型号、反向耐压(Vr)、整流电流、尺寸、品牌",
     "example": "贴片二极管,SS14,40V,1A,,SOD-123,KUU"},
    {"name": "IC类", "order": "料号名称、IC类型、型号、尺寸、工作电压、品牌",
     "example": "贴片集成电路,模拟开关,CH443K,SC-70-6(SOT-363),2.1~5.5V,WCH"},
    {"name": "贴片三极管", "order": "料号名称、型号、晶体管类型、集电极电流(Ic)、集射极击穿电压(Vceo)、尺寸、品牌",
     "example": "贴片三极管,LBSS4240LT1G,NPN,2A,40V,SOT-23,LRC(乐山无线电)"},
    {"name": "贴片场效应管", "order": "料号名称、型号、N或P沟道、耐压、额定电流、尺寸、品牌",
     "example": "贴片MOS管,2N7002,N沟道, 60V,115mA,SOT-23,JSMSEMI(杰盛微)"},
]

#: 通用兜底类别
GENERIC_RULE: Dict[str, str] = {
    "name": "贴片元件",
    "order": "料号名称、型号、尺寸、品牌",
    "example": "贴片元件,型号,尺寸,品牌",
}

#: 《物料参数要求表》里没有、但实际会用到的类别（字段顺序参照同类）
EXTRA_RULES: List[Dict[str, str]] = [
    {
        "name": "贴片发光二极管",
        "order": "料号名称、型号、颜色、电压、电流、尺寸、品牌",
        "example": "贴片发光二极管,G504DC,绿发翠绿光,φ5mm,卓越微",
    },
    {
        "name": "插件二极管",
        "order": "料号名称、型号、反向耐压(Vr)、整流电流、尺寸、品牌",
        "example": "插件二极管,1N4148,100V,150mA,DO-35,品牌",
    },
    {
        "name": "插件集成电路",
        "order": "料号名称、IC类型、型号、尺寸、工作电压、品牌",
        "example": "插件集成电路,运算放大器,LM358,DIP-8,5V,ST",
    },
]

#: 元件类别 -> 默认规则
_KIND_RULE = {
    "R": "贴片电阻",
    "C": "贴片电容",
    "L": "贴片电感",
    "X": "贴片晶振",
    "XO": "贴片晶振",
    "D": "贴片二极管",
    "LED": "贴片发光二极管",
    "IC": "IC类",
    "Q": "贴片三极管",
    "MOS": "贴片场效应管",
}


def _load_rows() -> List[Dict[str, str]]:
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        rows = []
        for item in data:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            rows.append({
                "name": name,
                "order": str(item.get("order") or "").strip(),
                "example": str(item.get("example") or "").strip(),
                "category": str(item.get("category") or "").strip(),
                "note": str(item.get("note") or "").strip(),
            })
        if rows:
            return rows + list(EXTRA_RULES)
    except Exception:
        pass
    return list(_FALLBACK_ROWS) + list(EXTRA_RULES)


RULE_ROWS: List[Dict[str, str]] = _load_rows()


def rule_names() -> List[str]:
    names = [row["name"] for row in RULE_ROWS]
    if GENERIC_RULE["name"] not in names:
        names.append(GENERIC_RULE["name"])
    return names


def find_rule(name: str) -> Optional[Dict[str, str]]:
    if not name:
        return None
    if name == GENERIC_RULE["name"]:
        return GENERIC_RULE
    for row in RULE_ROWS:
        if row["name"] == name:
            return row
    for row in RULE_ROWS:
        if name in row["name"] or row["name"] in name:
            return row
    return None


def rule_text() -> str:
    """给 UI 显示的完整文本。"""
    lines = []
    for row in RULE_ROWS:
        lines.append(f"【{row['name']}】")
        if row.get("order"):
            lines.append(f"  参数顺序：{row['order']}")
        if row.get("example"):
            lines.append(f"  示例：{row['example']}")
        if row.get("note"):
            lines.append(f"  备注：{row['note']}")
        lines.append("")
    return "\n".join(lines)


def suggest_rule_name(comp) -> str:
    """根据原理图元件猜一个最接近的参数要求类别。"""
    kind = getattr(comp, "kind", "") or ""
    lib = (getattr(comp, "lib_id", "") or "").lower()
    value = (getattr(comp, "value", "") or "").lower()
    footprint = (getattr(comp, "footprint", "") or "").lower()
    text = f"{lib} {value} {footprint}"

    try:
        from erp_match import is_tht_footprint

        tht = is_tht_footprint(getattr(comp, "footprint", "") or "")
    except Exception:
        tht = "tht" in footprint or "dip" in footprint

    if kind in _KIND_RULE and not tht:
        return _KIND_RULE[kind]
    if kind in _KIND_RULE and tht:
        tht_map = {
            "R": "插件电阻",
            "C": "插件电解电容" if (getattr(comp, "value_num", None) or 0) >= 1e-6 else "插件电容",
            "L": "插件电感",
            "X": "插件晶振",
            "D": "插件发光二极管" if ("led" in text or "发光" in text) else "插件二极管",
            "LED": "插件发光二极管",
            "IC": "插件集成电路",
            "Q": "插件三极管",
            "MOS": "插件场效应管",
        }
        return tht_map.get(kind, _KIND_RULE[kind])

    if "led" in text or "发光" in text:
        return "插件发光二极管" if tht else "贴片发光二极管"
    if any(k in text for k in ("header", "pin", "针座")):
        return "针座"
    if any(k in text for k in ("conn", "usb", "jack", "socket", "terminal")):
        return "USB连接器"
    if "crystal" in text or "resonator" in text or "晶振" in text:
        return "贴片晶振"
    if "switch" in text or "sw_" in text or "开关" in text:
        return "轻触开关"
    if "fuse" in text or "保险丝" in text:
        return "保险丝"
    if "inductor" in text or "ferrite" in text or "磁珠" in text:
        return "贴片电感"
    if "relay" in text or "继电器" in text:
        return "继电器"
    if "buzzer" in text or "蜂鸣器" in text:
        return "蜂鸣器"
    if "oled" in text or "lcd" in text or "display" in text or "显示屏" in text:
        return "显示屏"
    if "battery" in text or "电池" in text:
        return "电池"
    if "motor" in text or "马达" in text:
        return "电机、马达"
    if "transformer" in text or "变压器" in text:
        return "变压器"
    if "varistor" in text or "压敏" in text:
        return "压敏电阻"
    if "thermistor" in text or "热敏" in text:
        return "热敏电阻"
    if "diode" in text or "二极管" in text:
        return "贴片二极管"
    return GENERIC_RULE["name"]


# --------------------------------------------------------------------------
# 按规则组装描述
# --------------------------------------------------------------------------
def _format_resistance(comp) -> str:
    try:
        from erp_match import format_resistance
    except Exception:
        return ""
    if getattr(comp, "value_num", None) is not None:
        return format_resistance(comp.value_num)
    return ""


def _format_capacitance(comp) -> str:
    try:
        from erp_match import format_capacitance
    except Exception:
        return ""
    if getattr(comp, "value_num", None) is not None:
        return format_capacitance(comp.value_num)
    return ""


def _format_tolerance(comp) -> str:
    try:
        from erp_match import format_tolerance
    except Exception:
        return ""
    return format_tolerance(getattr(comp, "tolerance", None))


def _is_tht(comp) -> bool:
    try:
        from erp_match import is_tht_footprint

        return is_tht_footprint(getattr(comp, "footprint", "") or "")
    except Exception:
        return False


def _field_value(field: str, index: int, rule_name: str, comp, config,
                 display_name: str = "") -> str:
    key = field.split("(")[0].split("（")[0].replace(" ", "")
    kind = getattr(comp, "kind", "") or ""
    size = getattr(comp, "size", "") or ""
    package = getattr(comp, "package", "") or ""
    model = getattr(comp, "model", "") or ""
    if not model and kind not in ("R", "C", "L", "X", "LED"):
        model = getattr(comp, "value", "") or ""
    brand = str(config.get("new_brand") or "FH")

    if index == 0:
        if "/" in field:
            options = [opt.strip() for opt in field.split("/") if opt.strip()]
            tht = _is_tht(comp)
            picked = ""
            for opt in options:
                if tht and "插件" in opt:
                    picked = opt
                    break
                if (not tht) and "贴片" in opt:
                    picked = opt
                    break
            return picked or (options[0] if options else rule_name)
        if any(k in key for k in ("料号名称", "名称", "料号")):
            return display_name or rule_name
    if "尺寸" in key:
        # 电阻/电容/电感/晶振看封装尺寸（0603 等），其它类别看器件封装（SOT-23 等）
        if kind in ("R", "C", "L", "X", "XO"):
            return size or package
        return package or size
    if "阻值" in key:
        return _format_resistance(comp) or (getattr(comp, "value", "") or "")
    if "容值" in key:
        return _format_capacitance(comp) or (getattr(comp, "value", "") or "")
    if "感值" in key or "电感值" in key:
        try:
            from erp_match import format_inductance, parse_inductance

            value = getattr(comp, "value_num", None)
            if value is None:
                value = parse_inductance(getattr(comp, "value", "") or "")
            if value:
                return format_inductance(value)
        except Exception:
            pass
        return getattr(comp, "value", "") or ""
    if "电流" in key:
        current = getattr(comp, "current", None)
        return f"{current:g}A" if current else ""
    if "颜色" in key:
        try:
            from erp_match import led_color

            colour = led_color(getattr(comp, "value", "") or "")
        except Exception:
            colour = ""
        return f"{colour}光" if colour else ""
    if "电压" in key and kind == "LED":
        try:
            from erp_match import parse_voltage

            voltage = parse_voltage(getattr(comp, "value", "") or "")
        except Exception:
            voltage = None
        return f"{voltage:g}V" if voltage else ""
    if "频率" in key:
        try:
            from erp_match import format_frequency, parse_frequency

            value = getattr(comp, "value_num", None)
            if value is None:
                value = parse_frequency(getattr(comp, "value", "") or "")
            if value:
                return format_frequency(value)
        except Exception:
            pass
        return getattr(comp, "value", "") or ""
    if "误差" in key or "精度" in key:
        tolerance = _format_tolerance(comp)
        if not tolerance and kind == "R":
            tolerance = str(config.get("new_resistor_tolerance") or "±5%")
        if not tolerance and kind == "C":
            tolerance = str(config.get("new_capacitor_tolerance") or "±10%")
        return tolerance
    if "功率" in key:
        table = config.get("resistor_power_by_size") or {}
        return table.get(size, "")
    if "材质" in key:
        if kind == "C":
            return str(config.get("new_capacitor_dielectric") or "X7R")
        return ""
    if "耐压" in key:
        if kind == "C":
            table = config.get("capacitor_voltage_by_size") or {}
            return table.get(size, "50V")
        return ""
    if "型号" in key:
        return model
    if "品牌" in key:
        # 只有电阻/电容的新料才补品牌
        return brand if kind in ("R", "C") else ""
    if "供应商" in key:
        return ""
    return ""


def build_description(rule_name: str, comp, config,
                      display_name: str = "") -> str:
    """按指定类别的参数顺序，用元件现有信息拼一条描述。

    `display_name` 用于把“IC类”这种表内类别名换成实际写入的“贴片集成电路”。
    """
    rule = find_rule(rule_name) or GENERIC_RULE
    order = rule.get("order") or GENERIC_RULE["order"]
    fields = [f.strip() for f in re.split(r"[、,，]", order) if f.strip()]

    parts: List[str] = []
    for index, field in enumerate(fields):
        value = _field_value(field, index, rule["name"], comp, config, display_name)
        if value:
            parts.append(value)

    if len(parts) <= 1:
        for extra in (getattr(comp, "model", "") or getattr(comp, "value", ""),
                      getattr(comp, "package", "") or getattr(comp, "size", "")):
            if extra:
                parts.append(extra)

    return ",".join(parts)
