# Changelog

本文件记录 ACADxPDF 的版本变更。版本号规则见下文「版本管理」。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.6.0] - 2026-09-10

### Added
- 自动版本管理模块 `acad2pdf/_version.py`：从 git tag + conventional commit 推导版本号并固化，作为全局唯一版本来源。
- API 返回版本号：`/health` 与 `/` 响应新增 `version` 字段。
- Web UI 页头显示当前版本号（从 `/health` 动态读取）。
- xlsx→DWG：全专业（建筑/结构/水/暖/电）批量转换 API，按专业拆分并行。
- xlsx→DWG：DWG 输出文件名改为 `绿色建筑设计专篇（{专业}）.dwg`。
- xlsx→DWG：页标题按专业区分（建筑→`绿色建筑设计专篇（一）`，其他→`{专业}专业绿色建筑设计专篇（一）`）。
- 统一调度架构：拉取式 Worker + 统一任务模型；远程 Worker 入口与状态展示。

### Changed
- xlsx→DWG：隐藏行不再转换到 DWG（所有专业）。
- 补充 `.gitignore`，纳入 A1/mt 模板与部署脚本。

### Fixed
- xlsx→DWG：中文支持、边框修复、跨列补框；空单元格隐藏边框。
- PDF→DWG 卡死、结果丢失 PDF、目录名错误。
- 版本 dirty 判断排除 `_version.py` 自身，避免自我指涉。

## [0.5.0] - 2026-04-27

### Added
- DWG → PDF 批量转换核心引擎（图框检测 + 自动纸张匹配）。
- PDF → DWG 反向转换（AutoCAD PDFIMPORT）。
- AutoLISP 模块化图框识别/纸张匹配/打印输出。
- Flask Web UI + SSE 实时进度。
- 支持天正/TArch 插件，Windows 原生与 WSL2。

---

## 版本管理

版本号**唯一来源**是 `acad2pdf/_version.py` 中的 `__version__`，由 git 历史自动推导：

```
__version__ = MAJOR.MINOR.PATCH [+dirty]
```

- 取仓库最新 `v*` tag 作为基线（无 tag 则从 `0.5.0` 起）。
- 根据 tag 之后的 [Conventional Commits](https://www.conventionalcommits.org/) 前缀自动升级：
  - `feat` → **MINOR** 进位
  - `fix` / `chore` / `docs` 等其余 → **PATCH** 进位
  - `BREAKING CHANGE` 或 `xxx!:` → **MAJOR** 进位
- 工作区有未提交改动时追加 `+dirty`。

发布新版本：在仓库根执行 `python -m acad2pdf._version` 计算并固化，然后打对应 `vX.Y.Z` tag 即可。
UI 与 API 实时从 `/health` 读取，无需手动同步。
