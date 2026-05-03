# AutoPlot 智能批量出图系统 — 调用说明

## 快速开始

### 1. 安装

将 `lsp/` 目录下所有文件复制到 AutoCAD 支持文件搜索路径中的任意目录，或将该目录添加到支持路径：

**方法一：手动加载**
```
命令: (load "autoplot.lsp")
```

**方法二：启动组自动加载**
1. 命令 `APPLOAD` → 启动组 → 添加 `autoplot.lsp`

**方法三：acaddoc.lsp**
在 `acaddoc.lsp` 中添加：
```lisp
(load "autoplot.lsp")
```

### 2. 准备配置文件

复制 `autoplot.env` 为模板，修改其中的路径和参数（详见下方配置说明）。配置文件使用 AutoLISP 关联列表格式。

### 3. 执行命令

| 命令 | 用途 | 说明 |
|------|------|------|
| `AutoPlot` | 交互式单文件处理 | 弹出对话框选择配置文件，处理当前打开的 DWG |
| `BatchPlot` | 目录级批量处理 | 自动加载同目录下 `autoplot.env`，遍历指定目录下所有 DWG |
| `LoadConfig` | 单独加载配置 | 可在脚本中预加载配置，之后调用处理函数 |

---

## 命令详解

### AutoPlot（交互式）

**适用场景：** 处理当前已打开的单个 DWG 文件。

**操作流程：**
1. 输入命令 `AutoPlot`
2. 弹出文件选择对话框，选择 `.env` 配置文件
3. 程序自动执行：DXF导出(可选) → 图框识别 → 纸张匹配 → 逐框打印PDF
4. 命令行输出处理进度和结果

**示例输出：**
```
[AutoPlot] 加载配置: D:\Config\autoplot.env
[AutoPlot] 配置加载成功，共 18 项参数
[AutoPlot] 导出 DXF: D:\Output\PDFs\Building.dxf OK
  找到 4 个块引用: TK
[AutoPlot] 共识别 4 个有效图框
  [1/4] A3 landscape -> Building_001_A3.pdf OK
  [2/4] A2 landscape -> Building_002_A2.pdf OK
  [3/4] A3 portrait -> Building_003_A3.pdf OK
  [4/4] A1 landscape -> Building_004_A1.pdf OK
```

### BatchPlot（批量处理）

**适用场景：** 无人值守批量处理整个目录下的 DWG 文件。

**操作流程：**
1. 将 `autoplot.env` 放在 AutoCAD 支持路径下或与 `autoplot.lsp` 同目录
2. 确保配置中 `input-directory` 指向 DWG 所在目录
3. 输入命令 `BatchPlot`
4. 程序自动遍历、逐文件处理、输出统计报告

**断点续传：** 若处理过程中被 ESC 中断，再次执行 `BatchPlot` 时自动跳过已完成的文件。

### accoreconsole 调用

通过 AutoCAD 命令行引擎无人值守执行：

```batch
"C:\Program Files\Autodesk\AutoCAD 2026\accoreconsole.exe" ^
  /i "autoplot.lsp" ^
  /s "batch.scr"
```

其中 `batch.scr` 内容：
```
(load "autoplot.lsp")
(LoadConfig "autoplot.env")
(BatchProcess)
_.QUIT _Y
```

---

## 配置参数说明

配置文件为 AutoLISP 关联列表，使用文本编辑器直接修改。以 `;` 开头的行为注释。

### 图框识别

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `block-names` | 字符串列表 | `("TK" "TUKUANG" "BORDER")` | 图框块名关键词，按优先级顺序匹配 |
| `detect-rectangles` | 布尔 | `T` | 块名匹配无结果时，是否启用闭合矩形备选检测 |
| `rect-min-area` | 整数 | `50000` | 矩形图框最小面积阈值（平方毫米），过滤小矩形 |
| `tolerance-mm` | 浮点 | `5.0` | 纸张尺寸匹配容差（毫米） |
| `frame-layer-filter` | 字符串列表/nil | `nil` | 可选：限定搜索的图层名列表 |

**图框识别策略：**
1. 优先按 `block-names` 在模型空间搜索 INSERT 块引用
2. 自动识别动态块（通过 EffectiveName 属性）
3. 遍历所有布局的图纸空间搜索
4. 若以上均无结果且 `detect-rectangles=T`，降级为闭合矩形检测（六步几何验证）

### 输出控制

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `output-directory` | 字符串 | `"./PDF_Output"` | PDF 输出目录，支持相对/绝对路径 |
| `pdf-name-format` | 字符串 | `"{filename}_{seq:03d}"` | 输出文件名模板（见下方变量说明） |
| `merge-pdf` | 布尔 | `nil` | 是否合并为单个 PDF（需外部工具 PDFtk） |

**文件名模板变量：**

| 变量 | 替换内容 | 示例 |
|------|----------|------|
| `{filename}` | 源 DWG 文件名（无扩展名） | `Building` |
| `{seq:03d}` | 三位零填充序号 | `001`, `002` |
| `{seq:02d}` | 两位零填充序号 | `01`, `02` |
| `{seq:d}` | 普通序号 | `1`, `2` |
| `{paper}` | 匹配纸幅名称 | `A3`, `A1+0.5` |
| `{layout}` | 来源布局名 | `Model`, `Layout1` |
| `{blockname}` | 图框块名 | `TK`, `RECT` |
| `{date}` | 当前日期 YYYYMMDD | `20260504` |

### 打印设置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `plot-style` | 字符串 | `"acad.ctb"` | 打印样式表（CTB/STB 文件名） |
| `plot-device` | 字符串 | `"DWG To PDF.pc3"` | PDF 打印设备（PC3 文件名） |
| `plot-scale` | 字符串 | `"Fit"` | `"Fit"` 自适应或固定比例如 `"1:100"` |
| `plot-margin` | 浮点 | `0.0` | 打印窗口外扩/内缩边距（毫米），正数外扩，负数内缩 |

### 纸张规格

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `paper-sizes` | 嵌套列表 | A0~A4 + 加长 | 自定义纸张数据库，格式 `("名称" 宽 高)` |
| `mediastep` | 整数 | `100` | 自动生成加长幅面的步长（毫米），从 A1 高度 841mm 起递增至 5080mm |

**内置纸张规格：**

| 名称 | 宽度(mm) | 高度(mm) |
|------|----------|----------|
| A0 | 841 | 1189 |
| A1 | 594 | 841 |
| A1+0.5 | 594 | 914.5 |
| A1+1 | 594 | 1025 |
| A2 | 420 | 594 |
| A3 | 297 | 420 |
| A4 | 210 | 297 |

`mediastep=100` 时自动生成 A1+100(594×941)、A1+200(594×1041) 等直至 5080mm。

### 批量处理

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `input-directory` | 字符串 | 无（必需） | DWG 文件输入目录，`BatchPlot` 模式下必须指定 |
| `file-filter` | 字符串 | `"*.dwg"` | 文件筛选通配符 |
| `recursive-search` | 布尔 | `nil` | 是否递归搜索子目录 |

### DXF 导出

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `export-dxf` | 布尔 | `nil` | 启用后每个 DWG 在打印前导出完整 DXF 文件 |
| `dxf-output-dir` | 字符串/nil | `nil` | DXF 输出目录，nil 时跟随 `output-directory` |
| `dxf-version` | 字符串 | `"16"` | DXF 版本号（16=AutoCAD 2013+） |

---

## 统计报告

批处理完成后自动输出格式化统计报告：

```
  +===================================================+
  |        AutoPlot 批量处理统计报告                   |
  +===================================================+
  |  总耗时:   14 分 26 秒 (866.0 秒)
  |
  |  文件统计:
  |    总计:     15 个
  |    成功:     13 个 (86.7%)
  |    失败:     2 个
  |
  |  PDF 输出:
  |    总页数:   47 页
  |    单文件平均: 3.6 页
  |    单页平均耗时: 18.4 秒
  |
  |  识别策略:
  |    块名匹配: 38 图框
  |    矩形检测: 9 图框
  |
  |  纸幅分布:
  |    A3: 18 页
  |    A4: 12 页
  |    A2: 10 页
  |    A1: 5 页
  |    A1+0.5: 2 页
  |
  |  输出目录: D:\Output\PDFs
  +===================================================+
```

---

## 配置文件示例

```lisp
;;; autoplot.env — AutoPlot 配置文件
(
  ;; 图框识别 — 根据实际图框块名修改
  ("block-names" . ("TK" "TUKUANG" "BORDER" "图框"))
  ("detect-rectangles" . T)
  ("rect-min-area" . 50000)
  ("tolerance-mm" . 5.0)

  ;; 输出控制 — 修改为实际输出路径
  ("output-directory" . "D:\\Output\\PDFs")
  ("pdf-name-format" . "{filename}_{seq:03d}_{paper}")

  ;; 打印设置
  ("plot-style" . "monochrome.ctb")
  ("plot-device" . "DWG To PDF.pc3")
  ("plot-scale" . "Fit")
  ("plot-margin" . 0.0)

  ;; 纸张规格
  ("mediastep" . 100)

  ;; 批量处理 — 修改为实际 DWG 目录
  ("input-directory" . "D:\\Drawings\\Input")
  ("file-filter" . "*.dwg")
  ("recursive-search" . nil)

  ;; DXF 导出（按需启用）
  ("export-dxf" . nil)
)
```

---

## 错误代码

| 代码 | 类型 | 原因 | 处理 |
|------|------|------|------|
| E101 | 文件打开失败 | 文件不存在/权限不足/网络断开 | 跳过 |
| E102 | 文件损坏 | DWG 格式错误/版本不兼容 | 跳过 |
| E201 | 无图框识别 | 块名不匹配/无闭合矩形 | 记录警告 |
| E202 | 图框全部无效 | 尺寸超出 100~5000mm 范围 | 记录警告 |

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `autoplot.lsp` | 主入口，加载模块、注册命令 |
| `ap-utils.lsp` | 工具函数（全局状态/错误处理/统计/几何计算） |
| `ap-config.lsp` | 配置管理（加载/解析/默认值） |
| `ap-detect.lsp` | 图框识别引擎（块名/动态块/矩形检测） |
| `ap-paper.lsp` | 纸张匹配系统（ISO A系列/加长/PC3映射） |
| `ap-plot.lsp` | 打印输出引擎（Window模式/页面设置） |
| `ap-batch.lsp` | 批量处理（目录遍历/断点续传） |
| `autoplot.env` | 配置文件示例 |

所有 `.lsp` 文件需放在同一目录下，该目录须在 AutoCAD 支持文件搜索路径中。

---

## 兼容性

支持 AutoCAD 2014 ~ 2026，包括 `accoreconsole.exe` 命令行模式。程序通过 `._` 命令前缀确保在中文/英文界面下均正常工作。
