"""元件 <-> ERP 物料匹配规则。

支持的类别
----------
* R   贴片电阻      ：Value + 封装尺寸（10K + 0603）
* C   贴片电容      ：Value + 封装尺寸（100nF + 0603）
* D   贴片二极管    ：型号（SS14 …）—— **发光二极管(LED) 不参与匹配**
* IC  贴片集成电路  ：型号（LM358 / BS84B08A-3 …）
* Q   贴片三极管    ：型号（BC847C …）
* MOS 贴片 MOS 管   ：型号（AO3400A ……）

品牌策略（仅电阻/电容）
----------------------
FH(风华) > 厚声 > YAGEO(国巨) > SUP(仅库存>0) > 新建 FH 新料。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from erp_config import Config
from erp_data import Material

# --------------------------------------------------------------------------
# 正则
# --------------------------------------------------------------------------
#: 贴片尺寸
SIZE_RE = re.compile(r"(?<!\d)(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?!\d)")

#: 常见封装
PACKAGE_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"SOT-?\d{2,3}(?:-\d+)?|SOD-?\d{2,3}|"
    r"TSSOP-?\d+|SSOP-?\d+|MSOP-?\d+|SOP-?\d+|SOIC-?\d+|SO-?\d+|"
    r"QFN-?\d+|DFN-?\d+|LQFP-?\d+|TQFP-?\d+|QFP-?\d+|BGA-?\d+|"
    r"SC-?\d+(?:-\d+)?|SMA|SMB|SMC|DO-?\d+|TO-?\d+(?:-\d+)?|DIP-?\d+"
    r")",
    re.I,
)

_RES_COMPACT_RE = re.compile(r"^(\d+(?:\.\d+)?)([kKmMuU]?)(\d*)$")
_CAP_COMPACT_RE = re.compile(r"^(\d+(?:\.\d+)?)([pnuµμPNU])?(\d*)$")
_L_COMPACT_RE = re.compile(r"^(\d+(?:\.\d+)?)([pnuµmM]?)(\d*)$")
_RES_DECIMAL_RE = re.compile(r"^(\d+)R(\d+)$", re.I)
_TOL_RE = re.compile(r"±?\s*(\d+(?:\.\d+)?)\s*%")
_VOLT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*V(?![A-Za-z])")
_POWER_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*W", re.I)
_DIELECTRIC_RE = re.compile(r"(X7R|X5R|X6S|X6T|NPO|NP0|C0G|COG|Y5V|Z5U)", re.I)
_MODEL_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/\+_]*$")
_FREQ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([kKmMgG]?)\s*Hz", re.I)
_FREQ_SHORT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([kKmMgG])$")
#: 常见晶振封装尺寸（4 位代码）
CRYSTAL_SIZE_RE = re.compile(r"(?<!\d)(1612|2016|2520|3225|5032|7050|6035|8038)(?!\d)")

_UNIT_SCALE = {
    "p": 1e-12, "P": 1e-12,
    "n": 1e-9, "N": 1e-9,
    "u": 1e-6, "U": 1e-6,
    "µ": 1e-6, "μ": 1e-6,
}

#: 电阻单位倍率（小写 m 为毫欧，大写 M 为兆欧）
_RES_SCALE = {
    "k": 1e3, "K": 1e3,
    "M": 1e6,
    "m": 1e-3,
    "u": 1e-6, "U": 1e-6,
}

KIND_LABELS = {
    "R": "电阻",
    "C": "电容",
    "L": "电感",
    "X": "晶振",
    "XO": "有源晶振",
    "D": "二极管",
    "IC": "芯片",
    "Q": "三极管",
    "MOS": "MOS管",
}

#: 插件封装前缀（第一版只匹配贴片）
THT_PACKAGES = ("DIP-", "TO-92", "TO-126", "TO-220", "TO-247", "TO-3")

#: 库名/封装名里出现这些字样也按插件处理
THT_HINTS = ("THT", "THROUGHHOLE", "PINHEADER", "PINSOCKET", "TERMINALBLOCK", "HC49")


def is_tht_footprint(footprint: str) -> bool:
    """判断是否为插件（THT）封装。"""
    footprint = footprint or ""
    if not footprint:
        return False
    library, _, name = footprint.partition(":")
    library_upper = library.upper()
    name_upper = name.upper()
    if "DIP" in library_upper or "DIP" in name_upper:
        return True
    if any(k in library_upper or k in name_upper for k in THT_HINTS):
        return True
    return name_upper.startswith(THT_PACKAGES)


# --------------------------------------------------------------------------
# 数值解析
# --------------------------------------------------------------------------
def extract_size(text: str) -> str:
    m = SIZE_RE.search(text or "")
    return m.group(1) if m else ""


def normalize_package(raw: str) -> str:
    if not raw:
        return ""
    text = raw.upper().replace(" ", "")
    m = re.match(r"^(SOT|SOD|SOIC|SO|SOP|SSOP|TSSOP|MSOP|QFN|DFN|LQFP|TQFP|QFP|BGA|SC|DO|TO|DIP)-?(\d.*)$", text)
    return f"{m.group(1)}-{m.group(2)}" if m else text


def extract_package(text: str) -> str:
    m = PACKAGE_RE.search(text or "")
    return normalize_package(m.group(1)) if m else ""


def package_equal(a: str, b: str) -> bool:
    if not a or not b:
        return False
    a, b = a.upper(), b.upper()
    if a == b:
        return True
    return a.startswith(b + "-") or b.startswith(a + "-")


def _normalize_decimal_comma(text: str) -> str:
    return re.sub(r"(\d),(\d)", r"\1.\2", text)


def _res_multiplier(unit: str) -> float:
    return _RES_SCALE.get(unit, 1.0)


def _parse_compact_resistance(token: str) -> Optional[float]:
    t = _normalize_decimal_comma((token or "").strip())
    if not t:
        return None
    t = t.replace("Ω", "").replace("ω", "")
    t = re.sub(r"(?i)ohms?$", "", t)
    # 0R026 / 4R7：字母 R 当小数点
    decimal = _RES_DECIMAL_RE.match(t)
    if decimal:
        try:
            return float(f"{decimal.group(1)}.{decimal.group(2)}")
        except ValueError:
            return None
    # 26mR：末尾 R 只是单位符号，去掉后再看倍率
    if len(t) > 1 and t[-1] in "Rr":
        t = t[:-1]
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
    return base if token.strip()[-1:] in ("F", "f") else None


def parse_capacitance(text: str) -> Optional[float]:
    """从描述里解析容值（法拉）。"""
    if not text:
        return None
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
        return _parse_compact_capacitance(stripped)
    return None


def _parse_compact_inductance(token: str) -> Optional[float]:
    """10uH / 4u7 / 100nH / 2m2 → 亨利。"""
    t = _normalize_decimal_comma((token or "").strip()).replace("µ", "u").replace("μ", "u")
    if not t:
        return None
    if t[-1:] in ("H", "h"):
        t = t[:-1]
    m = _L_COMPACT_RE.match(t)
    if not m:
        return None
    whole, unit, frac = m.group(1), m.group(2), m.group(3)
    try:
        base = float(f"{whole}.{frac}") if frac else float(whole)
    except ValueError:
        return None
    scale = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "M": 1e-3}.get(unit or "", 1.0)
    return base * scale


def parse_inductance(text: str) -> Optional[float]:
    """从描述里解析感值（亨利）。"""
    if not text:
        return None
    for m in re.finditer(r"(\d+(?:\.\d+)?\s*[pnuµmM]?\d*)\s*[Hh](?![A-Za-z])", text):
        value = _parse_compact_inductance(m.group(1))
        if value is not None:
            return value
    return None


def parse_current(text: str) -> Optional[float]:
    """从描述里解析额定电流（安培），如 10uH/5A 里的 5。"""
    if not text:
        return None
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*A(?![A-Za-z])", text):
        try:
            return float(m.group(1))
        except ValueError:
            continue
    return None


def format_inductance(value: float) -> str:
    if value >= 1e-3:
        return f"{value * 1e3:g}mH"
    if value >= 1e-6:
        return f"{value * 1e6:g}uH"
    return f"{value * 1e9:g}nH"


def parse_frequency(text: str) -> Optional[float]:
    """从描述/数值里解析频率（Hz）：8MHz / 32.768kHz / 8M。"""
    if not text:
        return None
    scale = {"k": 1e3, "m": 1e6, "g": 1e9}
    for m in _FREQ_RE.finditer(text):
        try:
            base = float(m.group(1))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        return base * scale.get(unit, 1.0)
    short = _FREQ_SHORT_RE.match((text or "").strip())
    if short:
        try:
            base = float(short.group(1))
        except ValueError:
            return None
        return base * scale.get(short.group(2).lower(), 1.0)
    return None


def format_frequency(value: float) -> str:
    if value >= 1e6:
        return f"{value / 1e6:g}MHz"
    if value >= 1e3:
        return f"{value / 1e3:g}kHz"
    return f"{value:g}Hz"


def extract_crystal_size(text: str) -> str:
    m = CRYSTAL_SIZE_RE.search(text or "")
    return m.group(1) if m else ""


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
    if value == 0:
        return "0Ω"
    if value >= 1e6:
        return f"{value / 1e6:g}MΩ"
    if value >= 1e3:
        return f"{value / 1e3:g}KΩ"
    if value < 1:
        return f"{value * 1e3:g}mΩ"
    return f"{value:g}Ω"


def format_capacitance(value: float) -> str:
    if value >= 1e-6:
        return f"{value * 1e6:g}uF"
    if value >= 1e-9:
        return f"{value * 1e9:g}nF"
    return f"{value * 1e12:g}pF"


def format_tolerance(value: Optional[float]) -> str:
    return f"±{value:g}%" if value else ""


# --------------------------------------------------------------------------
# 型号
# --------------------------------------------------------------------------
def normalize_model(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def search_normalize(text: str) -> str:
    """搜索用归一化：全角→半角，欧姆/微符号统一，再转大写。"""
    raw = unicodedata.normalize("NFKC", text or "")
    raw = raw.replace("\u2126", "\u03a9")            # 欧姆符号 → 希腊大写 Ω
    raw = raw.replace("\u00b5", "\u03bc")            # 微符号 → 希腊小写 μ
    raw = raw.replace("\u03c9", "\u03a9").replace("\u03bc", "\u03bc")
    return raw.upper()


def is_electronic_pn(pn: str) -> bool:
    """电子物料的料号：1 开头且 9 位（如 101010511）。"""
    code = (pn or "").strip()
    return len(code) == 9 and code.startswith("1") and code.isdigit()


def is_electronic_prefix(pn: str) -> bool:
    """电子物料大类前缀：1 开头（如 101010511 / 1004201100014）；排除 5xxxxx 成品等。"""
    code = (pn or "").strip()
    return code.startswith("1") and code[:1].isdigit()


def search_keyword(comp: "Component") -> str:
    """右键搜索 ERP 时的预填关键字：类别 + 大致封装 + Value/型号。

    电阻额外把阻值写成 ",100KΩ"（带逗号前缀）以便精确命中规格字段，
    避免搜 1KΩ 时把 5.1KΩ 也带出来。
    """
    kind = getattr(comp, "kind", "") or ""
    label = KIND_LABELS.get(kind, "")
    if kind in ("R", "C", "L", "X", "XO"):
        package = getattr(comp, "size", "") or getattr(comp, "package", "") or ""
    else:
        package = getattr(comp, "package", "") or getattr(comp, "size", "") or ""
    value = (getattr(comp, "model", "") or getattr(comp, "value", "") or "").strip()
    if kind == "R":
        number = getattr(comp, "value_num", None)
        if number is None:
            number = parse_resistance(value)
        if number is not None:
            value = "," + format_resistance(number)
    parts = [p for p in (label, package, value) if p]
    if not parts:
        parts = [value] if value else []
    return " ".join(parts)


def _looks_like_model(token: str) -> bool:
    t = (token or "").strip()
    if len(t) < 4 or len(t) > 40:
        return False
    if not _MODEL_TOKEN_RE.match(t):
        return False
    if "." in t:
        return False
    if re.fullmatch(r"\d+(\.\d+)?[A-Za-z%]{1,2}", t):
        return False
    if not any(c.isalpha() for c in t) or not any(c.isdigit() for c in t):
        return False
    package = extract_package(t)
    if package and package.replace("-", "") == t.upper().replace("-", ""):
        return False
    return True


def extract_model_from_spec(spec: str) -> str:
    for token in re.split(r"[,，;；\s]+", spec or ""):
        if _looks_like_model(token):
            return token
    return ""


# --------------------------------------------------------------------------
# 物料解析
# --------------------------------------------------------------------------
@dataclass
class ParsedMaterial:
    material: Material
    kind: str = ""              # R / C / D / IC / Q / MOS
    mount: str = ""             # SMT / THT（贴片 / 插件）
    size: str = ""              # 0603…
    model: str = ""             # SS14 / LM358 / BC847C / AO3400A
    package: str = ""           # SOT-23 / SOD-123 / SOP-8
    value: Optional[float] = None
    tolerance: Optional[float] = None
    voltage: Optional[float] = None
    current: Optional[float] = None
    dielectric: str = ""
    power: str = ""
    brand: str = ""


def _material_mount(text: str) -> str:
    """从名称+描述判断是贴片还是插件。"""
    if "插件" in text and "贴片" not in text:
        return "THT"
    if "贴片" in text and "插件" not in text:
        return "SMT"
    low = text.lower()
    if "smd" in low:
        return "SMT"
    if any(k in low for k in ("hc-49", "hc49", "dip")):
        return "THT"
    return ""


def _classify_material(material: Material, mount: str = "") -> str:
    name = material.name or ""
    spec = material.spec or ""
    text = f"{name} {spec}"
    if not mount:
        mount = _material_mount(text)

    if "集成电路" in text or "集成块" in text or "芯片" in text:
        return "IC"
    if "热敏" in text or "可调" in text or "电位" in text or "采样" in text or "压敏" in text:
        return ""
    if "MOS" in name.upper() or "场效应" in text:
        return "MOS"
    if "三极管" in text:
        return "Q"
    if "发光" in text:
        return ""          # 发光二极管不参与匹配
    if "二极管" in text:
        return "D"
    if "有源" in text or "oscillator" in text.lower():
        return "XO"
    if "晶振" in text or "crystal" in text.lower():
        return "X"
    if "电感" in text and "共模" not in text and "磁珠" not in text:
        return "L"
    if "电阻" in text and "排阻" not in text and "合金" not in text:
        return "R"
    if "电容" in text:
        # 贴片电解/钽电容仍不自动匹配；插件电解电容是常见物料，允许匹配
        if mount == "SMT" and ("电解" in text or "钽" in text):
            return ""
        return "C"
    return ""


def normalize_brand(raw: str, config: Config) -> str:
    aliases: Dict[str, str] = config.get("brand_aliases") or {}
    upper_map = {str(k).upper(): v for k, v in aliases.items() if str(k).isascii()}
    text = (raw or "").strip()
    if not text:
        return ""
    if text in aliases:
        return aliases[text]
    if text.upper() in upper_map:
        return upper_map[text.upper()]
    return text


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
    text = f"{material.name} {material.spec}"
    parsed.mount = _material_mount(text)
    parsed.kind = _classify_material(material, parsed.mount)
    if not parsed.brand:
        parsed.brand = _brand_from_text(text, config)

    parsed.size = extract_size(text) or extract_crystal_size(text)
    parsed.package = extract_package(material.spec) or extract_package(material.name)
    parsed.tolerance = parse_tolerance(material.spec)

    if parsed.kind == "R":
        value = parse_resistance(material.spec)
        if value is None:
            value = parse_resistance(material.name)
        parsed.value = value
        parsed.power = parse_power(material.spec)
    elif parsed.kind in ("X", "XO"):
        value = parse_frequency(material.spec)
        if value is None:
            value = parse_frequency(material.name)
        parsed.value = value
    elif parsed.kind == "L":
        value = parse_inductance(material.spec)
        if value is None:
            value = parse_inductance(material.name)
        parsed.value = value
        parsed.current = parse_current(material.spec)
    elif parsed.kind == "C":
        value = parse_capacitance(material.spec)
        if value is None:
            value = parse_capacitance(material.name)
        parsed.value = value
        parsed.voltage = parse_voltage(material.spec)
        parsed.dielectric = parse_dielectric(material.spec)
    elif parsed.kind in ("D", "IC", "Q", "MOS"):
        parsed.model = extract_model_from_spec(material.spec) or extract_model_from_spec(material.name)
        if not parsed.package:
            parsed.package = extract_package(material.spec)
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
    package: str = ""
    model: str = ""
    value_num: Optional[float] = None
    tolerance: Optional[float] = None
    description: str = ""
    partnumber: str = ""
    #: "SMT" 贴片 / "THT" 插件
    mount: str = "SMT"
    #: 额定电流（电感等，单位 A）
    current: Optional[float] = None
    #: Value 为 NC/DNP 等不上件标记
    do_not_place: bool = False
    #: 属性是否已隐藏（False/缺失 表示会显示在原理图上）
    fields_hidden: Dict[str, bool] = field(default_factory=dict)

    def hidden(self, canonical: str) -> bool:
        """该字段是否隐藏（缺失字段视为隐藏）。"""
        return bool(self.fields_hidden.get(canonical, True))

    @property
    def label(self) -> str:
        return self.reference or self.uuid[:8]

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind or "其他")


def _looks_like_led(lib_id: str, value: str) -> bool:
    lib = (lib_id or "").lower()
    if "led" in lib or "发光" in (value or ""):
        return True
    text = (value or "").upper()
    return text.startswith("LED") or "_LED" in text


#: “不上件”的 Value 写法
NOPOP_VALUES = {"NC", "N/C", "DNP", "DNF", "DNI"}

#: 库名里出现这些字样 → 明确不是电阻/电容/芯片等（不参与自动匹配）
OTHER_LIB_HINTS = (
    "connector", "usb", "jack", "header", "socket", "terminal", "pinheader",
    "switch", "sw_", "relay", "buzzer", "fuse", "battery", "mounting",
    "testpoint", "net_tie", "jumper", "screw",
)


def _kind_from_lib(lib_low: str) -> str:
    """根据库名判断元件类别；返回 "" 表示无法判断，"OTHER" 表示明确不匹配。"""
    if not lib_low:
        return ""
    if any(k in lib_low for k in OTHER_LIB_HINTS):
        return "OTHER"
    if "led" in lib_low:
        return "LED"
    if any(k in lib_low for k in ("oscillator", "tcxo", "vctcxo")):
        return "XO"
    if any(k in lib_low for k in ("crystal", "resonator", "xtal")):
        return "X"
    if any(k in lib_low for k in ("mosfet", "nmos", "pmos", "transistor_fet")):
        return "MOS"
    if re.search(r"(^|:)(mos|fet)(_|$)", lib_low) or "mos" in lib_low or "fet" in lib_low:
        return "MOS"
    if "transistor" in lib_low or re.search(r"(^|:)q(_|$)", lib_low):
        return "Q"
    if "diode" in lib_low or re.search(r"(^|:)d(_|$)", lib_low) or "bridge" in lib_low:
        return "D"
    if "inductor" in lib_low or "ferrite" in lib_low or re.search(r"(^|:)l(_|$)", lib_low):
        return "L"
    if "resistor" in lib_low or re.search(r"(^|:)r(_|$)", lib_low):
        return "R"
    if "capacitor" in lib_low or re.search(r"(^|:)c(_|$)", lib_low):
        return "C"
    return ""


def analyze_component(comp: Component, config: Config) -> Component:
    """补全类别 / 尺寸 / 封装 / 数值 / 型号。"""
    ref = (comp.reference or "").upper()
    if not ref or ref.startswith("#"):
        return comp

    lib = comp.lib_id or ""
    lib_low = lib.lower()
    text = f"{lib_low} {comp.footprint} {comp.value}".lower()
    prefix_match = re.match(r"([A-Z]+)", ref)
    prefix = prefix_match.group(1) if prefix_match else ""

    # 尺寸/封装/容差对任何类别都先填好（未自动匹配的元件也能一键生成描述）
    comp.size = extract_size(comp.footprint) or extract_size(comp.value)
    comp.package = extract_package(comp.footprint) or extract_package(comp.value)
    comp.tolerance = parse_tolerance(comp.value)
    comp.mount = "THT" if is_tht_footprint(comp.footprint) else "SMT"

    # Value = NC / DNP 的元件不上件，不参与匹配
    if (comp.value or "").strip().upper() in NOPOP_VALUES:
        comp.do_not_place = True
        return comp

    kind = _kind_from_lib(lib_low)
    if kind in ("OTHER", "LED"):
        return comp  # 连接器/开关等不自动匹配；发光二极管不匹配

    if not kind:
        # 库名看不出类别时，退回按位号前缀判断
        if prefix.startswith("LED"):
            return comp
        if prefix.startswith("R"):
            if any(k in text for k in ("potentiometer", "thermistor", "network", "r_pack")):
                return comp
            kind = "R"
        elif prefix.startswith("C"):
            kind = "C"
        elif prefix.startswith("L"):
            kind = "L"
        elif prefix.startswith("D"):
            if _looks_like_led(lib, comp.value):
                return comp
            kind = "D"
        elif prefix.startswith(("Q", "T")):
            kind = "MOS" if any(k in lib_low for k in ("mos", "fet", "nmos", "pmos")) else "Q"
        elif prefix.startswith("U") or prefix.startswith("IC"):
            kind = "IC"
        else:
            return comp

    comp.kind = kind
    if kind in ("X", "XO"):
        comp.size = extract_crystal_size(comp.footprint) or comp.size

    if kind == "R":
        comp.value_num = parse_resistance(comp.value)
    elif kind == "C":
        comp.value_num = parse_capacitance(comp.value)
    elif kind == "L":
        comp.value_num = parse_inductance(comp.value)
        comp.current = parse_current(comp.value)
    elif kind in ("X", "XO"):
        comp.value_num = parse_frequency(comp.value)
    else:
        comp.model = (comp.value or "").strip()
    return comp


# --------------------------------------------------------------------------
# 候选与选择
# --------------------------------------------------------------------------
@dataclass
class Candidate:
    parsed: ParsedMaterial
    brand_rank: int = 9999

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
        return 500 if kind not in ("R", "C") else 1000
    if b in preferred:
        return preferred.index(b)
    if b in fallback:
        return 100 + fallback.index(b)
    return 1000


def _tolerance_rank(tol: Optional[float], want: Optional[float], kind: str,
                    config: Config) -> int:
    if want:
        return 0 if (tol is not None and abs(tol - want) < 1e-9) else 1
    order = ("resistor_tolerance_priority" if kind == "R" else "capacitor_tolerance_priority")
    values: List[float] = []
    for v in (config.get(order) or []):
        try:
            values.append(float(v))
        except (TypeError, ValueError):
            continue
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
    # 贴片/插件必须是同一类，避免把插件料用到贴片元件上
    if parsed.mount:
        if comp.mount and parsed.mount != comp.mount:
            return False
    elif comp.mount == "THT":
        # 物料没写贴片/插件时按贴片处理，插件元件不套用
        return False
    material = parsed.material

    if comp.kind in ("R", "C"):
        if comp.size and parsed.size and comp.size != parsed.size:
            return False
        if comp.value_num is None or parsed.value is None:
            return False
        return _num_equal(parsed.value, comp.value_num)

    if comp.kind in ("X", "XO"):
        if comp.size and parsed.size and comp.size != parsed.size:
            return False
        if comp.value_num is None or parsed.value is None:
            return False
        return _num_equal(parsed.value, comp.value_num)

    if comp.kind == "L":
        if comp.size and parsed.size and comp.size != parsed.size:
            return False
        if comp.value_num is None or parsed.value is None:
            return False
        if not _num_equal(parsed.value, comp.value_num):
            return False
        # 物料额定电流低于元件需求时排除（任一侧未知则不比较）
        if comp.current and parsed.current and parsed.current + 1e-9 < comp.current:
            return False
        return True

    # 二极管 / IC / 三极管 / MOS：按型号匹配
    if not comp.model:
        return False
    needle = normalize_model(comp.model)
    if len(needle) < 3:
        return False
    haystack = normalize_model(material.spec) + "|" + normalize_model(material.name)
    return needle in haystack


def build_candidates(comp: Component, materials: Iterable[Material],
                     config: Config,
                     parsed_materials: Optional[Sequence[ParsedMaterial]] = None) -> List[Candidate]:
    """返回按优先级排序的候选（含非优选品牌，供人工挑选）。"""
    if not comp.kind:
        return []
    if parsed_materials is None:
        parsed_materials = [parse_material(m, config) for m in materials if not m.disabled]

    result: List[Candidate] = []
    for parsed in parsed_materials:
        if parsed.material.disabled:
            continue
        if not is_electronic_prefix(parsed.material.pn):
            continue  # 只认电子物料（1 开头），排除 5xxxxx 成品等
        if not _material_matches(comp, parsed):
            continue
        result.append(Candidate(parsed=parsed,
                                brand_rank=_brand_rank(parsed.brand, config, comp.kind)))

    def sort_key(candidate: Candidate):
        stock = -candidate.material.stock
        if comp.kind == "L":
            return (
                candidate.brand_rank,
                _tolerance_rank(candidate.parsed.tolerance, comp.tolerance, comp.kind, config),
                0 if candidate.parsed.current else 1,
                -(candidate.parsed.current or 0.0),
                stock,
                candidate.pn,
            )
        if comp.kind in ("X", "XO"):
            return (
                candidate.brand_rank,
                0 if (comp.size and candidate.parsed.size == comp.size) else 1,
                stock,
                candidate.pn,
            )
        if comp.kind in ("R", "C"):
            return (
                candidate.brand_rank,
                _tolerance_rank(candidate.parsed.tolerance, comp.tolerance, comp.kind, config),
                _dielectric_rank(candidate.parsed.dielectric, comp.kind, config),
                stock,
                candidate.pn,
            )
        # 型号类：型号完全一致 > 封装一致 > 库存多
        model_exact = 0 if normalize_model(comp.model) == normalize_model(candidate.parsed.model) else 1
        package_rank = 0 if package_equal(comp.package, candidate.parsed.package) else 1
        return (model_exact, package_rank, stock, candidate.pn)

    result.sort(key=sort_key)
    return result


def build_generic_candidates(comp: Component, config: Config,
                             parsed_materials: Sequence[ParsedMaterial]) -> List[Candidate]:
    """未分类元件（连接器/开关/晶振等）按型号在所有物料里找。"""
    needle = normalize_model(comp.model or comp.value)
    if len(needle) < 4:
        return []
    result: List[Candidate] = []
    for parsed in parsed_materials:
        if parsed.material.disabled:
            continue
        if not is_electronic_prefix(parsed.material.pn):
            continue
        if parsed.mount and comp.mount and parsed.mount != comp.mount:
            continue
        haystack = (normalize_model(parsed.material.spec) + "|"
                    + normalize_model(parsed.material.name))
        if needle in haystack:
            result.append(Candidate(parsed=parsed,
                                    brand_rank=_brand_rank(parsed.brand, config, comp.kind)))
    result.sort(key=lambda c: (-c.material.stock, c.pn))
    return result


def auto_select(candidates: Sequence[Candidate], config: Config,
                kind: str = "") -> Optional[Candidate]:
    """按策略选一个；返回 None 表示需要新建。"""
    if kind in ("R", "C"):
        for candidate in candidates:
            if candidate.brand_rank < 100:
                return candidate
        for candidate in candidates:
            if candidate.brand_rank < 1000 and candidate.material.stock > 0:
                return candidate
        return None

    for candidate in candidates:
        if candidate.material.stock > 0:
            return candidate
    return candidates[0] if candidates else None


# --------------------------------------------------------------------------
# 新建物料
# --------------------------------------------------------------------------
#: 插件（THT）新料对应的《物料参数要求表》类别
THT_KIND_RULES = {
    "R": "插件电阻",
    "C": "插件电容",
    "L": "插件电感",
    "X": "插件晶振",
    "XO": "插件晶振",
    "D": "插件二极管",
    "IC": "插件集成电路",
    "Q": "插件三极管",
    "MOS": "插件场效应管",
}

#: 贴片（SMT）新料对应的规则类别
SMT_KIND_RULES = {
    "R": "贴片电阻",
    "C": "贴片电容",
    "L": "贴片电感",
    "X": "贴片晶振",
    "XO": "贴片晶振",
    "D": "贴片二极管",
    "IC": "IC类",
    "Q": "贴片三极管",
    "MOS": "贴片场效应管",
}

#: 规则类别名 → 实际写入描述的首字段名
RULE_DISPLAY_NAMES = {
    "IC类": "贴片集成电路",
    "贴片场效应管": "贴片MOS管",
    "插件场效应管": "插件MOS管",
    "贴片晶振": "贴片晶振",
}


def canonical_new_rule(comp: Component) -> Tuple[str, str]:
    """返回新料应使用的 (规则类别, 写入描述的首字段名)。"""
    kind = comp.kind or ""
    if not kind:
        return "", ""
    if comp.mount == "THT":
        rule = THT_KIND_RULES.get(kind, "")
        if kind == "C" and (comp.value_num or 0) >= 1e-6:
            rule = "插件电解电容"
    else:
        rule = SMT_KIND_RULES.get(kind, "")
    if not rule:
        return "", ""
    return rule, RULE_DISPLAY_NAMES.get(rule, rule)


def build_new_item(comp: Component, config: Config) -> Tuple[str, str]:
    """返回 (PartNumber, 描述)。

    与界面右键「生成新料描述」使用**同一个**规则引擎，保证两边文本一致。
    品牌只在电阻/电容新料里补 FH。
    """
    pn = config.get("new_partnumber") or "新料"
    package = comp.package or comp.size or ""

    try:
        import erp_rules

        rule_name, display = canonical_new_rule(comp)
        if not rule_name:
            rule_name = erp_rules.suggest_rule_name(comp)
            display = RULE_DISPLAY_NAMES.get(rule_name, rule_name)
        description = erp_rules.build_description(rule_name, comp, config, display_name=display)
        if description:
            return pn, description
    except Exception:
        pass

    parts = [guess_category_name(comp), comp.model or comp.value, package]
    return pn, ",".join([p for p in parts if p])


def guess_category_name(comp: Component) -> str:
    """给未自动匹配的元件猜一个物料名称，用于一键生成新料描述。"""
    lib = (comp.lib_id or "").lower()
    value = (comp.value or "").upper()
    if "led" in lib or value.startswith("LED"):
        return "贴片发光二极管"
    if any(k in lib for k in ("conn", "jack", "usb", "header", "terminal", "socket")):
        return "贴片连接器"
    if "switch" in lib or "sw_" in lib:
        return "贴片轻触开关"
    if "crystal" in lib or "resonator" in lib:
        return "贴片晶振"
    if "fuse" in lib:
        return "贴片保险丝"
    if "inductor" in lib or lib.startswith("device:l"):
        return "贴片电感"
    return "贴片元件"
