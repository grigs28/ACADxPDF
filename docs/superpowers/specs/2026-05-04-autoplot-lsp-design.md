# AutoPlot LISP 程序设计规格

## 概述

基于 PRD.md 生成 AutoCAD 2026 智能批量出图 LISP 程序，运行于 AutoCAD 内部（GUI 交互 + 无人值守批处理），也兼容 accoreconsole 命令行模式。采用多文件模块化架构，完整实现全部功能。支持可选的 DXF 中间导出。

## 文件结构与加载顺序

```
lsp/
├── autoplot.lsp        ← 主入口，加载所有模块，注册 S::STARTUP
├── ap-utils.lsp        ← 工具函数（最先加载，无依赖）
├── ap-config.lsp       ← 配置管理（依赖 utils）
├── ap-detect.lsp       ← 图框识别引擎（依赖 utils, config）
├── ap-paper.lsp        ← 纸张匹配系统（依赖 utils, config）
├── ap-plot.lsp         ← 打印输出引擎（依赖 utils, config, paper）
└── ap-batch.lsp        ← 批量处理+统计（依赖所有模块）
```

加载顺序严格按依赖链：utils → config → detect → paper → plot → batch，任一加载失败则中止并报错。

命名空间通过 `ap:` 前缀隔离。全局状态变量（`*config*`、`*frame-list*`、`*stats*`、`*running*`）统一定义在 `ap-utils.lsp` 中。

对外命令：
- `c:AutoPlot` — 交互式，选择配置 → 处理当前文档
- `c:BatchPlot` — 批量，读取配置 → 遍历目录处理
- `c:LoadConfig` — 可单独调用加载配置

## ap-utils.lsp 工具模块

职责：全局状态、错误处理、系统变量管理、坐标/几何计算、文件名模板替换。

全局变量：
- `*config*` — 配置关联列表
- `*frame-list*` — 图框列表
- `*stats*` — 统计信息
- `*running*` — 重入锁

核心函数：

| 函数 | 功能 |
|------|------|
| `ap:save-sysvars` | 保存 FILEDIA/CMDECHO/PICKADD 等 |
| `ap:restore-sysvars` | 恢复系统变量 |
| `ap:with-silent-mode` | 静默模式包装器（保存→静默→执行→恢复） |
| `ap:init-stats` | 初始化统计关联列表 |
| `ap:update-stats` | 增量更新统计项 |
| `ap:output-stats` | 输出格式化统计报告 |
| `*error*` 重定义 | 捕获异常、恢复状态、释放锁 |
| `ap:format-filename` | 模板变量替换 `{filename}` `{seq:03d}` `{paper}` `{layout}` `{date}` `{blockname}` |
| `ap:export-dxf` | 当 `export-dxf=T` 时，调用 `_.DXFOUT` 导出当前文档为 DXF |
| `ap:get-entity-bounds` | ActiveX GetBoundingBox 封装 |
| `ap:distance-2d` | 二维距离计算 |
| `ap:point-center` | 两点中心 |

统计报告包含文件统计、PDF 输出、识别策略分布、纸幅分布四大部分。

## ap-config.lsp 配置管理模块

职责：配置文件加载、解析、默认值填充、配置项访问。

配置文件格式：AutoLISP 原生关联列表，通过 `read` 函数一次性解析。

核心函数：

| 函数 | 功能 |
|------|------|
| `c:LoadConfig` | 入口：打开文件 → 拼接内容 → read 解析 → 填充默认值 |
| `ap:fill-defaults` | 合并用户配置与默认值，用户优先 |
| `ap:get-config` | 按 key 取值，不存在返回 nil |
| `ap:get-config-default` | 按 key 取值，不存在返回指定默认值 |

默认值清单：

| 参数 | 默认值 |
|------|--------|
| `block-names` | `("TK" "TUKUANG" "BORDER")` |
| `output-directory` | `"./PDF_Output"` |
| `pdf-name-format` | `"{filename}_{seq:03d}"` |
| `merge-pdf` | `nil` |
| `plot-style` | `"acad.ctb"` |
| `plot-device` | `"DWG To PDF.pc3"` |
| `plot-scale` | `"Fit"` |
| `paper-sizes` | A0~A4 + A1+0.5 + A1+1 |
| `mediastep` | `100` |
| `file-filter` | `"*.dwg"` |
| `recursive-search` | `nil` |
| `tolerance-mm` | `5.0` |
| `detect-rectangles` | `T` |
| `rect-min-area` | `50000` |
| `frame-layer-filter` | `nil` |
| `export-dxf` | `nil` |
| `dxf-output-dir` | `nil`（跟随 output-directory） |

纸张数据库含 ISO A 系列全规格 + 加长幅面。通过 `mediastep` 参数动态生成额外加长规格至 5080mm。

单位换算通过 `INSUNITS` 系统变量自动检测，内置英寸(×25.4)/毫米(×1.0)/厘米(×10.0)/米(×1000.0)换算系数表，统一归一化为毫米。

## ap-detect.lsp 图框识别引擎

职责：模型空间/图纸空间的图框检测，支持块名匹配、动态块识别、封闭矩形备选检测。

图框记录数据结构（统一关联列表）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `"entity"` | ENAME | AutoCAD 实体名 |
| `"type"` | STRING | "BLOCK" 或 "RECTANGLE" |
| `"block-name"` | STRING/nil | 匹配的块名 |
| `"layout"` | STRING | "Model" 或布局名 |
| `"bounds"` | ((x1 y1) (x2 y2)) | WCS 边界框 |
| `"paper-match"` | STRING/nil | 匹配纸幅（后续填充） |
| `"orientation"` | STRING/nil | 打印方向（后续填充） |

核心函数：

| 函数 | 功能 |
|------|------|
| `ap:detect-all-frames` | 主入口：先块名搜索 → 失败则矩形备选 → 去重排序 |
| `ap:search-blocks-model` | 模型空间块名 ssget 过滤扫描 |
| `ap:search-blocks-paper` | 遍历所有布局，图纸空间块名搜索 |
| `ap:search-blocks-enhanced` | 增强搜索：含动态块 EffectiveName 识别 |
| `ap:get-effective-name` | 获取动态块可见名称 |
| `ap:find-closed-polylines` | 闭合 LWPOLYLINE 筛选 (70 . 1) |
| `ap:is-rectangle` | 六步矩形验证（顶点数/闭合/凸度/对边/对角线/直角） |
| `ap:get-poly-bounds` | 多段线顶点 → AABB 边界框 |
| `ap:make-frame-record` | 构造标准化图框记录 |
| `ap:sort-frames` | 从上到下、从左到右排序（Y 降序主键，X 升序次键） |
| `ap:dedup-frames` | AABB 重叠 >90% 去重，保留面积较大者 |
| `ap:validate-frame` | 尺寸有效性检查（100mm~5000mm） |

搜索流程：
1. 模型空间块名搜索 → 增强搜索（含动态块）
2. 图纸空间遍历所有布局搜索
3. 若块名策略零结果且 `detect-rectangles=T`，降级矩形检测
4. 矩形检测：闭合多段线 → 矩形验证 → 面积过滤 → 图层过滤（可选）
5. 合并所有结果 → 去重 → 有效性验证 → 排序

## ap-paper.lsp 纸张匹配系统

职责：根据图框尺寸自动匹配最优纸张规格，支持标准/加长/自定义幅面。

纸张数据库结构：`("名称" 宽度mm 高度mm)`

标准规格：A0(841×1189) / A1(594×841) / A2(420×594) / A3(297×420) / A4(210×297)
加长规格：A1+0.5(594×914.5) / A1+1(594×1025)
动态加长：按 `mediastep` 步长从 A1 基础高度自动生成至 5080mm

核心函数：

| 函数 | 功能 |
|------|------|
| `ap:build-paper-db` | 合并内置规格 + 配置自定义规格 + 动态加长生成 |
| `ap:match-paper` | 主入口：图框 → 计算尺寸 → 匹配 → 写入结果 |
| `ap:match-by-area` | 面积最小差值法，两种方向均尝试 |
| `ap:match-by-ratio` | 长宽比对数相似度（权重 0.4） |
| `ap:combine-score` | 综合评分 = 0.6×面积 + 0.4×比例 |
| `ap:canonical-media-name` | 标准名 → PC3 完整名称映射 |
| `ap:resolve-units` | INSUNITS → 毫米换算系数 |
| `ap:generate-elongated` | 按步长生成加长幅面列表 |

匹配流程：
1. bounds → 宽高 → 单位换算归一化为毫米
2. 标准规格搜索 → 加长规格搜索
3. 面积匹配 + 比例匹配综合评分，取最高分
4. 三级容差降级：严格(≤5%) → 宽松(≤15%) → 强制（任意+警告）
5. 自动判断 orientation（portrait/landscape）

PC3 名称映射：维护 AutoCAD 2026 标准 PDF 驱动的媒体名称映射，自定义纸张通过 `custom-media-names` 配置扩展。

## ap-plot.lsp 打印输出引擎

职责：单图框打印配置、Window 模式精确裁剪、多图框循环输出。

核心函数：

| 函数 | 功能 |
|------|------|
| `ap:init-plot-device` | 获取 Plot 对象，验证/回退 PC3 设备 |
| `ap:set-plot-window` | SetWindowToPlot + acWindow 模式 |
| `ap:apply-page-setup` | 纸张大小、方向、样式表、打印比例 |
| `ap:plot-frame` | 设置窗口 → 页面配置 → PlotToFile |
| `ap:process-frames` | 多图框循环：匹配 → 打印 → 状态跟踪 |
| `ap:process-current-drawing` | 处理当前活动文档（AutoPlot 模式） |
| `ap:export-dxf` | 当 `export-dxf=T` 时，调用 `_.DXFOUT` 导出当前文档为 DXF |

DXF 导出流程（可选，`export-dxf=T` 时执行）：
1. 计算输出路径：`dxf-output-dir`（默认跟随 `output-directory`）+ 文件名.dxf
2. `(command "._DXFOUT" dxf-path "16" "")` — 导出版本为 AutoCAD 2013+（版本号可配置）
3. 导出成功后记录路径至统计信息
4. 导出失败不阻断后续 PDF 打印流程

单图框打印流程：
1. bounds 读取窗口坐标，考虑 `plot-margin` 外扩/内缩
2. SetWindowToPlot + PlotType=acWindow
3. SetCanonicalMediaName（PC3 名称精确匹配）
4. PlotRotation（ac0degrees/ac90degrees）
5. StyleSheet（CTB/STB，PSTYLEMODE 检测兼容性）
6. 比例：Fit → acScaleToFit；固定 → CustomScale
7. PlotToFile 输出 PDF

多图框循环：foreach 遍历排序图框列表，每帧匹配→文件名生成→打印→统计更新，单帧失败重试一次。

## ap-batch.lsp 批量处理与统计模块

职责：目录遍历、多文件批处理、进度恢复、统计报告输出。

核心函数：

| 函数 | 功能 |
|------|------|
| `c:BatchProcess` | 主入口：加载配置 → 构建文件队列 → 逐文件处理 |
| `ap:collect-dwg-files` | 递归目录遍历 + wcmatch 筛选 DWG |
| `ap:process-single-file` | 打开 → 检测图框 → 打印输出 → 关闭 |
| `ap:load-progress` | 读取已处理文件列表（断点续传） |
| `ap:save-progress` | 写入进度检查点 |
| `ap:sort-file-queue` | 文件排序（名称/修改时间/大小） |
| `ap:output-stats` | 格式化统计报告 |
| `ap:write-csv-log` | CSV 日志写入（可选） |

批处理流程：
1. 检查 `*running*` 锁
2. 加载配置，验证 `input-directory`
3. 收集文件，排除已处理项
4. 静默模式
5. 逐文件：OPEN → [export-dxf] → detect → process-frames → CLOSE → save-progress
6. 文件级错误分类（E101~E202）→ 跳过继续
7. 恢复系统变量，输出统计报告

断点续传：`autoplot-progress.sav` 记录已成功文件路径，重启时自动跳过。ESC 中断时 `*error*` 保存检查点。

## autoplot.lsp 主入口与加载器

按依赖链顺序加载所有模块文件，任一失败则中止。

命令注册：
- `c:AutoPlot` — 交互式：检查锁 → COM初始化 → 选择配置 → 加载 → 检测图框 → 输出PDF → 释放锁
- `c:BatchPlot` — 批量：检查锁 → 默认/选择配置 → 构建文件队列 → 逐文件处理 → 统计报告 → 释放锁

S::STARTUP 支持：检测已有定义 → 追加初始化代码（不覆盖），验证 PDF 打印设备，输出加载提示。

兼容性：命令前缀 `._` 确保国际化环境正常；`vl-load-com` 每个入口重复调用（幂等）；支持 AutoCAD 2014-2026。

## 设计决策

1. **关联列表而非对象**：图框记录使用关联列表而非闭包/对象，符合 AutoLISP 惯用模式，调试时可直接打印查看。
2. **三级容差降级**：纸张匹配严格→宽松→强制降级策略，确保任何尺寸图框均有输出方案。
3. **动态加长生成**：通过 mediastep 参数自动生成加长规格，减少手动配置。
4. **断点续传**：大规模批处理（数百文件）中防止中断后完全重来。
5. **重入锁**：`*running*` 变量防止用户在批处理运行中误触发第二次。
6. **模块化文件**：每个文件 200-400 行，职责单一，可独立调试。
7. **可选 DXF 导出**：`export-dxf` 默认关闭，启用后在图框检测前执行 `_.DXFOUT` 导出完整文件，失败不阻断 PDF 打印流程。DXF 版本号可通过 `dxf-version` 配置（默认 16 = AutoCAD 2013+）。
