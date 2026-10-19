"""元件 <-> ERP 物料匹配规则。

匹配思路
--------
1. 从原理图元件拿到：类别(电阻/电容/二极管)、封装尺寸(0603…)、数值(10KΩ/100nF…)、
   容差(Value 里写了才用)、型号(二极管)。
2. 在 ERP 物料表里筛出「同类别 + 同尺寸 + 数值相等」的物料。
3. 按品牌优先级排序：FH > 厚声 > YAGEO(国巨) > (备选：SUP/SAMSUNG 需有库存)。
4. 同一品牌下：容差优先（未标注时 5% 优先）、库存多的优先。
5. 全都没有 -> 按参数要求表格式新建一个 FH 新料，PartNumber 写「（新料）」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from erp_config import Config
from erp_data import Material

#: 常见贴片封装尺寸（按长度排序）
SIZE_RE = re.compile(r"(?<!\d)(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?!\d)")

# 数值 token
_RES_COMPACT_RE = re.compile(r"^(\d+(?:\.\d+)?)([kKmMRr])?(\d*)$")
_CAP_COMPACT_RE = re.compile(r"^(\d+(?:\.\d+)?)([pnuµμPNU])?(\d*)$")
_TOL_RE = re.compile(r"±?\s*(\d+(?:\.\d+)?)\s*%")
_VOLT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*V(?![A-Za-z])")
_POWER_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*W", re.I)
_DIELECTRIC_RE = re.compile(r"(X7R|X5R|X6S|X6T|NPO|NP0|C0G|COG|Y5V|Z5U)", re.I)

_UNIT_SCALE = {
    "p": 1e-12, "P": 1e-12,
    "n": 1e-9, "N": 1e-9,
    "u": 1e-6, "U": 1e-6,
    "µ": 1e-6, "μ": 1e-6,
}


# --------------------------------------------------------------------------
# 数值解析
# --------------------------------------------------------------------------
def extract_size(text: str) -> str:
    m = SIZE_RE.search(text or "")
    return m.group(1) if m else ""


def _res_multiplier(unit: str) -> float:
    return {"k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6, "r": 1.0, "R": 1.0}.get(unit, 1.0)


def _normalize_decimal_comma(text: str) -> str:
    """把 "4,7K" / "0,1uF" 这类欧式小数逗号转成小数点。"""
    return re.sub(r"(\d),(\d)", r"\1.\2", text)


def _parse_compact_resistance(token: str) -> Optional[float]:
    t = _normalize_decimal_comma((token or "").strip())
    if not t:
        return None
    t = t.replace("Ω", "").replace("ω", "")
    t = re.sub(r"(?i)ohms?$", "", t)
    m = _RES_COMPACT_RE.match(t)
    if not m:
        return None
    whole, unit, frac = m.group(1), m.group(2), m.group(3)
    try:
        base = float(f"{whole}.{frac}") if frac else float(whole)
    except ValueError:
        return None
    return base * _res_multiplier(unit or "")


def parse_resistance(text: str) -> Optional[float]:
    """从描述里解析阻值（欧姆）。"""
    if not text:
        return None
    if "Ω" in text or "ω" in text:
        for m in re.finditer(r"([^\s,，;；()（）]+?)\s*[Ωω]", text):
            value = _parse_compact_resistance(m.group(1))
            if value is not None:
                return value
        return None
    stripped = text.strip()
    value = _parse_compact_resistance(stripped)
    if value is not None:
        return value
    first = re.split(r"[\s,，;；]+", stripped)[0]
    return _parse_compact_resistance(first)


def _parse_compact_capacitance(token: str) -> Optional[float]:
    t = _normalize_decimal_comma((token or "").strip()).replace("µ", "u").replace("μ", "u")
    if not t:
        return None
    if t[-1:] in ("F", "f"):
        t = t[:-1]
    m = _CAP_COMPACT_RE.match(t)
    if not m:
        return None
    whole, unit, frac = m.group(1), m.group(2), m.group(3)
    try:
        base = float(f"{whole}.{frac}") if frac else float(whole)
    except ValueError:
        return None
    if unit:
        return base * _UNIT_SCALE.get(unit, 1.0)
    # 没单位：只有明确带 F 后缀时才当做法拉
    return base if token.strip()[-1:] in ("F", "f") else None


def parse_capacitance(text: str) -> Optional[float]:
    """从描述里解析容值（法拉）。"""
    if not text:
        return None
    # 优先取带 F 后缀的 token
    for m in re.finditer(r"([^\s,，;；()（）]+?)F\b", text, re.I):
        value = _parse_compact_capacitance(m.group(1) + "F")
        if value is not None:
            return value
    for m in re.finditer(r"([^\s,，;；()（）]+?)\s*F(?![A-Za-z])", text):
        value = _parse_compact_capacitance(m.group(1))
        if value is not None:
            return value
    stripped = text.strip()
    m = _CAP_COMPACT_RE.match(stripped.replace("µ", "u").replace("μ", "u"))
    if m and m.group(2):
        value = _parse_compact_capacitance(stripped)
        if value is not None:
            return value
    return None


def parse_tolerance(text: str) -> Optional[float]:
    if not text:
        return None
    m = _TOL_RE.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def parse_voltage(text: str) -> Optional[float]:
    if not text:
        return None
    m = _VOLT_RE.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def parse_power(text: str) -> str:
    if not text:
        return ""
    m = _POWER_RE.search(text)
    if m:
        return f"{m.group(1)}/{m.group(2)}W"
    m = re.search(r"(\d+(?:\.\d+)?)\s*W(?![A-Za-z])", text)
    return f"{m.group(1)}W" if m else ""


def parse_dielectric(text: str) -> str:
    if not text:
        return ""
    m = _DIELECTRIC_RE.search(text)
    if not m:
        return ""
    return {"NP0": "NPO", "COG": "C0G"}.get(m.group(1).upper(), m.group(1).upper())


def format_resistance(value: float) -> str:
    if value >= 1e6:
        return f"{value / 1e6:g}MΩ"
    if value >= 1e3:
        return f"{value / 1e3:g}KΩ"
    return f"{value:g}Ω"


def format_capacitance(value: float) -> str:
    if value >= 1e-6:
        return f"{value * 1e6:g}uF"
    if value >= 1e-9:
        return f"{value * 1e9:g}nF"
    return f"{value * 1e12:g}pF"


def format_tolerance(value: Optional[float]) -> str:
    if not value:
        return ""
    return f"±{value:g}%"


# --------------------------------------------------------------------------
# 物料解析
# --------------------------------------------------------------------------
@dataclass
class ParsedMaterial:
    material: Material
    kind: str = ""              # R / C / D
    size: str = ""
    value: Optional[float] = None      # 欧姆 / 法拉
    tolerance: Optional[float] = None
    voltage: Optional[float] = None
    dielectric: str = ""
    power: str = ""
    brand: str = ""


def _classify_material(material: Material) -> str:
    name = material.name or ""
    spec = material.spec or ""
    text = name + " " + spec
    if "贴片" not in text:
        return ""
    if "电阻" in text:
        if any(k in text for k in ("热敏", "可调", "电位", "插件", "采样", "合金", "排阻", "水泥")):
            return ""
        return "R"
    if "电容" in text:
        if any(k in text for k in ("电解", "钽", "插件", "瓷片", "安规")):
            return ""
        return "C"
    if "二极管" in text:
        return "D"
    return ""


def normalize_brand(raw: str, config: Config) -> str:
    aliases: Dict[str, str] = config.get("brand_aliases") or {}
    upper_map = {str(k).upper(): v for k, v in aliases.items() if str(k).isascii()}
    text = (raw or "").strip()
    if text:
        if text in aliases:
            return aliases[text]
        if text.upper() in upper_map:
            return upper_map[text.upper()]
        return text
    return ""


def _brand_from_text(text: str, config: Config) -> str:
    aliases: Dict[str, str] = config.get("brand_aliases") or {}
    upper_text = text.upper()
    for key, value in aliases.items():
        if str(key).isascii():
            if re.search(r"(?<![A-Za-z])" + re.escape(str(key).upper()) + r"(?![A-Za-z])", upper_text):
                return value
        elif str(key) in text:
            return value
    return ""


def parse_material(material: Material, config: Config) -> ParsedMaterial:
    parsed = ParsedMaterial(material=material, brand=normalize_brand(material.brand, config))
    parsed.kind = _classify_material(material)
    text = f"{material.name} {material.spec}"
    if not parsed.brand:
        parsed.brand = _brand_from_text(text, config)

    parsed.size = extract_size(text)
    parsed.tolerance = parse_tolerance(material.spec)

    if parsed.kind == "R":
        parsed.value = parse_resistance(material.spec) or parse_resistance(material.name)
        parsed.power = parse_power(material.spec)
    elif parsed.kind == "C":
        parsed.value = parse_capacitance(material.spec)
        parsed.voltage = parse_voltage(material.spec)
        parsed.dielectric = parse_dielectric(material.spec)
    return parsed


# --------------------------------------------------------------------------
# 原理图元件
# --------------------------------------------------------------------------
@dataclass
class Component:
    file: str
    uuid: str
    reference: str
    value: str
    footprint: str
    lib_id: str
    kind: str = ""
    size: str = ""
    value_num: Optional[float] = None
    tolerance: Optional[float] = None
    model: str = ""
    description: str = ""
    partnumber: str = ""

    @property
    def label(self) -> str:
        return self.reference or self.uuid[:8]


def analyze_component(comp: Component, config: Config) -> Component:
    """补全类别 / 尺寸 / 数值 / 容差。"""
    ref = (comp.reference or "").upper()
    lib = (comp.lib_id or "")

    if ref.startswith("#") or "power" in lib.lower():
        # 电源符号/图框符号，跳过
        return comp

    prefix = re.match(r"([A-Z]+)", ref)
    prefix_text = prefix.group(1) if prefix else ""

    if prefix_text in ("R", "RN") or lib.lower().startswith("device:r"):
        comp.kind = "R"
    elif prefix_text in ("C", "CN") or lib.lower().startswith("device:c"):
        comp.kind = "C"
    elif prefix_text in ("D", "LED") or lib.lower().startswith("device:d") or lib.lower().startswith("device:led"):
        comp.kind = "D"
    else:
        return comp

    # 排除可调/热敏等特殊器件
    text = f"{lib} {comp.footprint} {comp.value}".lower()
    if comp.kind == "R" and any(k in text for k in ("potentiometer", "thermistor", "r_pack", "resistor_network")):
        comp.kind = ""
        return comp

    comp.size = extract_size(comp.footprint) or extract_size(comp.value)
    comp.tolerance = parse_tolerance(comp.value)

    if comp.kind == "R":
        comp.value_num = parse_resistance(comp.value)
    elif comp.kind == "C":
        comp.value_num = parse_capacitance(comp.value)
    elif comp.kind == "D":
        comp.model = comp.value.strip()
    return comp


# --------------------------------------------------------------------------
# 候选与选择
# --------------------------------------------------------------------------
@dataclass
class Candidate:
    parsed: ParsedMaterial
    brand_rank: int = 9999
    reason: str = ""

    @property
    def material(self) -> Material:
        return self.parsed.material

    @property
    def pn(self) -> str:
        return self.material.pn

    @property
    def description(self) -> str:
        return self.material.spec

    @property
    def brand(self) -> str:
        return self.parsed.brand


def _num_equal(a: float, b: float) -> bool:
    if a == 0 or b == 0:
        return abs(a - b) <= 1e-12
    return abs(a - b) <= max(abs(a), abs(b)) * 1e-6


def _brand_rank(brand: str, config: Config, kind: str) -> int:
    preferred = [str(b).upper() for b in (config.get("preferred_brands") or [])]
    fallback = [str(b).upper() for b in (config.get("fallback_brands") or [])]
    b = (brand or "").upper()
    if not b:
        # 二极管没有指定品牌策略，允许选"品牌为空但有库存"的料；
        # 电阻/电容则宁可按规则新建 FH，也不自动选无品牌料。
        return 500 if kind == "D" else 1000
    if b in preferred:
        return preferred.index(b)
    if b in fallback:
        return 100 + fallback.index(b)
    return 1000


def _tolerance_rank(tol: Optional[float], want: Optional[float], kind: str,
                    config: Config) -> int:
    if want:
        return 0 if (tol is not None and abs(tol - want) < 1e-9) else 1
    order = (config.get("resistor_tolerance_priority") if kind == "R"
             else config.get("capacitor_tolerance_priority")) or []
    try:
        values = [float(v) for v in order]
    except (TypeError, ValueError):
        values = []
    if tol is None:
        return len(values) + 1
    for i, v in enumerate(values):
        if abs(tol - v) < 1e-9:
            return i
    return len(values)


def _dielectric_rank(dielectric: str, kind: str, config: Config) -> int:
    if kind != "C":
        return 0
    order = [str(v).upper() for v in (config.get("capacitor_dielectric_priority") or [])]
    d = (dielectric or "").upper()
    if not d:
        return len(order) + 1
    return order.index(d) if d in order else len(order)


def _material_matches(comp: Component, parsed: ParsedMaterial) -> bool:
    if not parsed.kind or parsed.kind != comp.kind:
        return False
    material = parsed.material
    if comp.kind in ("R", "C"):
        if comp.size and parsed.size and comp.size != parsed.size:
            return False
        if comp.value_num is None or parsed.value is None:
            return False
        return _num_equal(parsed.value, comp.value_num)
    if comp.kind == "D":
        model = (comp.model or "").upper()
        if model:
            text = f"{material.name} {material.spec}".upper()
            if model not in text:
                return False
        if comp.size and parsed.size and comp.size != parsed.size:
            return False
        return True
    return False


def build_candidates(comp: Component, materials: Iterable[Material],
                     config: Config) -> List[Candidate]:
    """返回按优先级排序的候选（含非优选品牌，供人工挑选）。"""
    if not comp.kind:
        return []
    result: List[Candidate] = []
    for material in materials:
        if material.disabled:
            continue
        parsed = parse_material(material, config)
        if not _material_matches(comp, parsed):
            continue
        candidate = Candidate(parsed=parsed, brand_rank=_brand_rank(parsed.brand, config, comp.kind))
        result.append(candidate)

    result.sort(key=lambda c: (
        c.brand_rank,
        _tolerance_rank(c.parsed.tolerance, comp.tolerance, comp.kind, config),
        _dielectric_rank(c.parsed.dielectric, comp.kind, config),
        -c.parsed.material.stock,
        c.pn,
    ))
    return result


def auto_select(candidates: Sequence[Candidate], config: Config) -> Optional[Candidate]:
    """按品牌策略选一个：优选品牌 -> 兜底品牌(有库存) -> None（需要新建）。"""
    for candidate in candidates:
        if candidate.brand_rank < 100:
            return candidate
    for candidate in candidates:
        if candidate.brand_rank < 1000 and candidate.material.stock > 0:
            return candidate
    return None


# --------------------------------------------------------------------------
# 新建物料
# --------------------------------------------------------------------------
def build_new_item(comp: Component, config: Config) -> Tuple[str, str]:
    """返回 (PartNumber, 描述)。描述沿用物料参数要求表的字段顺序。"""
    brand = config.get("new_brand") or "FH"
    pn = config.get("new_partnumber") or "（新料）"

    if comp.kind == "R":
        size = comp.size or ""
        value_text = format_resistance(comp.value_num) if comp.value_num else comp.value
        tolerance = format_tolerance(comp.tolerance) or (config.get("new_resistor_tolerance") or "±5%")
        power_table = config.get("resistor_power_by_size") or {}
        power = power_table.get(size, "")
        parts = ["贴片电阻", size, value_text, tolerance, power, brand]
        return pn, ",".join([p for p in parts if p])

    if comp.kind == "C":
        size = comp.size or ""
        value_text = format_capacitance(comp.value_num) if comp.value_num else comp.value
        voltage_table = config.get("capacitor_voltage_by_size") or {}
        voltage = voltage_table.get(size, "50V")
        tolerance = format_tolerance(comp.tolerance) or (config.get("new_capacitor_tolerance") or "±10%")
        dielectric = config.get("new_capacitor_dielectric") or "X7R"
        parts = ["贴片电容", size, value_text, voltage, tolerance, dielectric, brand]
        return pn, ",".join([p for p in parts if p])

    if comp.kind == "D":
        size = comp.size or ""
        parts = ["贴片二极管", comp.model, size, brand]
        return pn, ",".join([p for p in parts if p])

    return pn, comp.value
