"""插件配置：默认值 + 读写 config.json。

配置文件位置由 KiCad 的 IPC API 提供（GetPluginSettingsPath），
拿不到时退回 `%APPDATA%/kicad-erp-to-sch`。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict

PLUGIN_IDENTIFIER = "com.github.YonimoC.kicad-erp-to-sch"

#: 仓库内默认数据文件位置（打包后可在设置里改成 ERP 导出目录）
DEFAULT_ERP_FILE = r"D:\_LWH\KiCad Lib\Addons\ERP-To-SCH\原材料基础数据 copy.xls"

DEFAULTS: Dict[str, Any] = {
    "erp_file": DEFAULT_ERP_FILE,
    "last_project_dir": "",

    # 品牌优选顺序（越小越优先）。国巨 = YAGEO，写成两种别名都认。
    "preferred_brands": ["FH", "厚声", "YAGEO", "国巨"],
    # 兜底品牌：只有在优选品牌都没有、且库存 > 0 时才使用。
    # 注意：SUP/SAMSUNG 等属于要逐步淘汰的品牌，只有 SUP 允许"有库存时先用掉"，
    # SAMSUNG 一律不自动选用（不在这里配置）。
    "fallback_brands": ["SUP"],
    # 品牌别名归一化（规格文本里出现的中英文写法）
    "brand_aliases": {
        "风华": "FH",
        "FH": "FH",
        "厚声": "厚声",
        "UNIOHM": "厚声",
        "YAGEO": "YAGEO",
        "国巨": "YAGEO",
        "SUP": "SUP",
        "SAMSUNG": "SAMSUNG",
        "三星": "SAMSUNG",
        "RALEC": "RALEC",
        "WALSIN": "WALSIN",
        "华新科": "WALSIN",
        "TDK": "TDK",
        "MURATA": "MURATA",
        "村田": "MURATA",
    },

    # 原理图 Value 没写容差时的优先容差（%）
    "resistor_tolerance_priority": [5, 1, 10, 20, 0.5, 0.1],
    "capacitor_tolerance_priority": [10, 5, 20],

    # 电容介质优先顺序
    "capacitor_dielectric_priority": ["X7R", "X5R", "X6S", "NPO", "C0G", "Y5V"],

    # 新建物料时按封装补全的参数
    "resistor_power_by_size": {
        "0201": "1/20W",
        "0402": "1/16W",
        "0603": "1/10W",
        "0805": "1/8W",
        "1206": "1/4W",
        "1210": "1/2W",
        "1812": "1W",
        "2010": "3/4W",
        "2512": "1W",
    },
    "capacitor_voltage_by_size": {
        "0201": "50V",
        "0402": "50V",
        "0603": "50V",
        "0805": "50V",
        "1206": "50V",
        "1210": "50V",
        "1812": "100V",
        "2010": "100V",
        "2512": "100V",
    },
    "new_resistor_tolerance": "±5%",
    "new_capacitor_tolerance": "±10%",
    "new_capacitor_dielectric": "X7R",
    # 仅电阻/电容新料才补品牌（其它类别不写品牌）
    "new_brand": "FH",
    "new_partnumber": "（新料）",
}


def settings_dir() -> Path:
    """插件可写设置目录。"""
    override = os.environ.get("ERP_TO_SCH_SETTINGS_DIR")
    if override:
        path = Path(override)
        path.mkdir(parents=True, exist_ok=True)
        return path

    # 首选：让 KiCad 分配（需要 IPC 连接）
    try:
        from kicad_session import get_kicad

        kicad = get_kicad()
        if kicad is not None:
            path = Path(kicad.get_plugin_settings_path(PLUGIN_IDENTIFIER))
            path.mkdir(parents=True, exist_ok=True)
            return path
    except Exception:
        pass

    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / "kicad-erp-to-sch"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Config:
    """config.json 的薄封装。"""

    def __init__(self, path: Path | None = None):
        self.path = path or (settings_dir() / "config.json")
        self.data: Dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if self.path.is_file():
                with open(self.path, "r", encoding="utf-8") as fp:
                    loaded = json.load(fp)
                if isinstance(loaded, dict):
                    merged = copy.deepcopy(DEFAULTS)
                    merged.update(loaded)
                    self.data = merged
        except Exception:
            self.data = copy.deepcopy(DEFAULTS)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fp:
                json.dump(self.data, fp, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    # -- 便捷属性 ---------------------------------------------------------
    @property
    def erp_file(self) -> str:
        return str(self.get("erp_file"))

    @property
    def cache_dir(self) -> Path:
        path = settings_dir() / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return path
