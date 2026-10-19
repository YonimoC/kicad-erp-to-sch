# ERP to SCH

KiCad 原理图插件：把原理图里的元件按「数值 + 封装」匹配到 ERP 原材料基础数据，
并把 **PartNumber** 与 **Description** 写回元件属性。

> 适用于 **KiCad 10.0.1 及以上**（IPC 插件，图标出现在原理图编辑器工具栏）。

---

## 功能

1. 扫描当前工程（含层次子图）中的所有电阻 / 电容 / 二极管；
2. 从元件读取 `Value`、`Footprint`、`Reference`；
3. 到 `原材料基础数据.xls` 中匹配 **同类别 + 同尺寸 + 同数值** 的物料；
4. 按品牌策略选择：**FH(风华) > 厚声 > YAGEO(国巨) > SUP(仅有库存时) > 新建 FH 新料**；
5. 都没有时按《物料参数要求表》的字段顺序生成新料描述，PartNumber 写 `（新料）`；
6. 写入元件的 `PartNumber`（没有该字段时自动新建）与 `Description`；
7. 写入前自动备份 `*.kicad_sch.erp-bak`。

### 匹配规则示例

| 原理图 | 匹配结果 |
|---|---|
| `R` / `10K` / `Resistor_SMD:R_0603_1608Metric` | `101010674` — 贴片电阻,0603,10KΩ,±5%,1/10W,FH |
| `R` / `10K 1%` / `R_0402_1005Metric` | `101010646` — 贴片电阻,0402,10KΩ,±1%,1/16W,FH |
| `C` / `100nF` / `C_0603_1608Metric` | `102010082` — 贴片电容,0603,100nF,50V,±10%,X7R,FH |
| `D` / `SS14` / `D_SOD-123` | 按型号匹配（不限制品牌） |

* Value 里没写容差时优先选 **±5%**；写了 `1%` 才优先 ±1%。
* 插件件（THT）、可调电阻、热敏电阻等第一版不参与匹配，会在列表里标注跳过。
* 封装里识别不到尺寸的元件会标为「缺少封装」，不会自动新建料。

---

## 安装

### 方式一：PCM 本地安装

```powershell
cd "d:\_LWH\KiCad Lib\Addons\ERP-To-SCH"
python package.py --install
```

然后在 KiCad：`插件和内容管理器 → 从文件安装… → erp-to-sch.zip`。

### 方式二：直接复制

把 `plugins/` 下的全部内容复制到：

```
C:\Users\<用户名>\Documents\KiCad\10.0\plugins\erp-to-sch\
```

（该目录下必须能直接看到 `plugin.json`。）

---

## ⚠️ 首次使用必须开启 API

`偏好设置 → 插件 → 启用 KiCad API（Enable KiCad API）`，然后重启 KiCad。

KiCad 会在后台为该插件创建 Python 虚拟环境并安装 `requirements.txt` 里的依赖
（`kicad-python`、`xlrd`、`openpyxl`），首次启动可能需要几十秒。

环境位置：`%LOCALAPPDATA%\KiCad\10.0\python-environments\com.github.YonimoC.kicad-erp-to-sch`

---

## 使用

1. 打开原理图，点工具栏上的 **ERP 物料匹配** 图标（或 `工具 → 外部插件`）；
2. 确认「ERP 物料表」「参数要求表」「工程目录」；
3. 点 **扫描并匹配**；
4. 需要改的元件可在列表中双击，弹出候选物料列表（含禁用过滤与品牌优先级）；
5. 点 **写入原理图**；
6. **回到原理图执行 `文件 → 重新加载（Revert）`**。

> ### 为什么需要手动重新加载？
> KiCad 10 的原理图 API 只能读取"打开了哪个文件"，还不能通过 API 修改图元
> （原理图读写 API 要到 KiCad 11 才有）。因此插件直接改写 `.kicad_sch` 文件。
>
> **写入后不要先按 Ctrl+S**，否则 KiCad 会用内存里的旧内容覆盖刚写入的字段。

---

## 配置

首次运行会在插件设置目录生成 `config.json`，可调整：

| 键 | 说明 |
|---|---|
| `erp_file` / `rules_file` | 数据文件路径 |
| `preferred_brands` | 优选品牌顺序 |
| `fallback_brands` | 兜底品牌（需有库存） |
| `resistor_tolerance_priority` | 未标注容差时的优先顺序 |
| `resistor_power_by_size` | 新建电阻时按封装补的功率 |
| `capacitor_voltage_by_size` | 新建电容时按封装补的耐压 |
| `new_partnumber` | 新料的 PartNumber，默认 `（新料）` |

设置目录：`%APPDATA%\kicad-erp-to-sch\`（或 KiCad 分配的 plugin settings 目录）。

---

## 目录结构

```
ERP-To-SCH/
├── plugins/
│   ├── plugin.json          # IPC 插件声明（scopes: schematic）
│   ├── requirements.txt
│   ├── erp_to_sch.py        # 入口
│   ├── erp_gui.py           # 主界面
│   ├── erp_match.py         # 匹配规则
│   ├── erp_data.py          # Excel 读取 + 缓存
│   ├── sch_sexpr.py         # .kicad_sch 读写（保排版）
│   ├── kicad_session.py     # IPC 连接 / 工程路径定位
│   ├── erp_config.py
│   └── icons/
├── resources/icon.png
├── metadata.json
├── package.py
├── generate_icons.py
└── _dev/                    # 测试脚本
```

## 已知限制

* 只支持贴片电阻 / 贴片电容 / 贴片二极管；
* 不支持多单元元件、插件件、可调/热敏电阻；
* KiCad 10 需手动重新加载原理图（见上）。
