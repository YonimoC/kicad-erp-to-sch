"""内置的《物料参数要求表》规则（几乎不变，随插件一起发布）。

来源：物料参数要求表20260709（贴片电阻/贴片电容/贴片二极管/贴片集成电路/贴片三极管/贴片场效应管）。
新建物料时按这里的字段顺序拼描述。
"""

from __future__ import annotations

from typing import Dict, List, Optional

#: 名称、参数顺序、示例
RULE_ROWS: List[Dict[str, str]] = [
    {
        "name": "贴片电阻",
        "order": "料号名称、尺寸、阻值、误差、功率、品牌",
        "example": "贴片电阻,0603,680Ω,±1%,1/10W,SUP",
    },
    {
        "name": "贴片电容",
        "order": "料号名称、尺寸、容值、误差、材质、耐压、品牌",
        "example": "贴片电容,0603,100nF,50V,±10%,X7R,SAMSUNG",
    },
    {
        "name": "贴片二极管",
        "order": "料号名称、型号、反向耐压(Vr)、整流电流、尺寸、品牌",
        "example": "贴片二极管,SS14,40V,1A,,SOD-123,KUU",
    },
    {
        "name": "IC类",
        "order": "料号名称、IC类型、型号、尺寸、工作电压、品牌",
        "example": "贴片集成电路,模拟开关,CH443K,SC-70-6(SOT-363),2.1~5.5V,WCH",
    },
    {
        "name": "贴片三极管",
        "order": "料号名称、型号、晶体管类型、集电极电流(Ic)、集射极击穿电压(Vceo)、尺寸、品牌",
        "example": "贴片三极管,LBSS4240LT1G,NPN,2A,40V,SOT-23,LRC(乐山无线电)",
    },
    {
        "name": "贴片场效应管",
        "order": "料号名称、型号、N或P沟道、耐压、额定电流、尺寸、品牌",
        "example": "贴片MOS管,2N7002,N沟道, 60V,115mA,SOT-23,JSMSEMI(杰盛微)",
    },
]

#: 新建物料时的默认名称
CATEGORY_NAME: Dict[str, str] = {
    "R": "贴片电阻",
    "C": "贴片电容",
    "D": "贴片二极管",
    "IC": "贴片集成电路",
    "Q": "贴片三极管",
    "MOS": "贴片MOS管",
}


def rule_text() -> str:
    """给 UI 显示的完整文本。"""
    lines = []
    for row in RULE_ROWS:
        lines.append(f"【{row['name']}】")
        lines.append(f"  参数顺序：{row['order']}")
        lines.append(f"  示例：{row['example']}")
        lines.append("")
    return "\n".join(lines)


def find_rule(keyword: str) -> Optional[Dict[str, str]]:
    for row in RULE_ROWS:
        if keyword in row["name"]:
            return row
    return None
