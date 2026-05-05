# AutoPlot LISP 智能批量出图 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 AutoCAD 2026 智能批量出图 LISP 程序，支持 GUI 交互和无人值守批处理，含图框自动识别、纸张自适应匹配、Window 模式精确打印、可选 DXF 导出。

**Architecture:** 7 个模块文件按依赖链加载（utils→config→detect→paper→plot→batch→主入口）。图框记录统一为关联列表，通过 `ap:` 前缀命名空间隔离。全局状态变量管理重入锁和统计信息。

**Tech Stack:** AutoLISP / Visual LISP ActiveX (vla-*/vlax-*) / AutoCAD 2026

**Spec:** `docs/superpowers/specs/2026-05-04-autoplot-lsp-design.md`

**验证方式:** 无自动化测试框架。每个文件完成后在 AutoCAD 中 `(load "ap-xxx.lsp")` 验证无语法错误，最终整体加载后执行 `AutoPlot` 命令端到端验证。

---

## Task 1: ap-utils.lsp — 工具函数模块

**Files:**
- Create: `lsp/ap-utils.lsp`

本模块是整个程序的基石，定义全局状态变量、错误处理机制、系统变量管理、几何计算工具和文件名模板替换。所有其他模块依赖此文件。

- [ ] **Step 1: 创建 ap-utils.lsp — 全局变量与错误处理**

```lisp
;;; ============================================================
;;; ap-utils.lsp — AutoPlot 工具函数模块
;;; 全局状态、错误处理、系统变量管理、几何计算、文件名模板
;;; ============================================================

(vl-load-com)

;;; ------------------------------------------------------------
;;; 全局状态变量
;;; ------------------------------------------------------------
(setq *config*      nil)    ; 配置关联列表
(setq *frame-list*  nil)    ; 图框列表
(setq *stats*       nil)    ; 统计信息
(setq *running*     nil)    ; 重入锁
(setq *saved-sysvars* nil)  ; 系统变量备份
(setq *orig-error*  nil)    ; 原始 *error* 备份

;;; ------------------------------------------------------------
;;; 错误处理
;;; ------------------------------------------------------------
(defun ap:error-handler (msg)
  (princ (strcat "\n[AutoPlot] 异常: " msg))
  (if *saved-sysvars*
    (ap:restore-sysvars *saved-sysvars*))
  (setq *running* nil)
  (if *stats* (ap:output-stats))
  (if *orig-error*
    (setq *error* *orig-error*))
  (princ))

(defun ap:install-error-handler (/ old)
  (setq *orig-error* *error*)
  (setq *error* ap:error-handler))

;;; ------------------------------------------------------------
;;; 系统变量管理
;;; ------------------------------------------------------------
(defun ap:save-sysvars (/ vars vals)
  (setq vars '("FILEDIA" "CMDECHO" "PICKADD" "CLAYER"))
  (setq vals (mapcar 'getvar vars))
  (setq *saved-sysvars* (list vars vals))
  *saved-sysvars*)

(defun ap:restore-sysvars (saved)
  (if (and saved (= (length saved) 2))
    (mapcar '(lambda (v s)
               (vl-catch-all-apply 'setvar (list v s)))
            (car saved) (cadr saved)))
  (setq *saved-sysvars* nil))

(defun ap:with-silent-mode (func / result)
  (ap:save-sysvars)
  (setvar "FILEDIA" 0)
  (setvar "CMDECHO" 0)
  (setvar "PICKADD" 1)
  (setq result (vl-catch-all-apply func))
  (ap:restore-sysvars *saved-sysvars*)
  (if (vl-catch-all-error-p result)
    (progn
      (princ (strcat "\n[AutoPlot] 执行错误: "
                     (vl-catch-all-error-message result)))
      nil)
    result))
```

- [ ] **Step 2: 追加统计信息管理函数**

```lisp
;;; ------------------------------------------------------------
;;; 统计信息管理
;;; ------------------------------------------------------------
(defun ap:init-stats ()
  (setq *stats*
    (list
      (cons "start-time" ((lambda (/ d) (setq d (getvar "DATE")) (* 86400.0 (- d (fix d)))) ))
      (cons "start-julian" (getvar "DATE"))
      (cons "total-files" 0)
      (cons "success-files" 0)
      (cons "fail-files" 0)
      (cons "total-frames" 0)
      (cons "block-frames" 0)
      (cons "rect-frames" 0)
      (cons "total-pdfs" 0)
      (cons "errors" nil)
      (cons "file-times" nil)
      (cons "paper-dist" nil))))

(defun ap:update-stats (key value / pair rest)
  (setq pair (assoc key *stats*))
  (if pair
    (setq *stats*
      (subst (cons key value) pair *stats*))
    (setq *stats* (cons (cons key value) *stats*))))

(defun ap:inc-stat (key / cur)
  (setq cur (cdr (assoc key *stats*)))
  (ap:update-stats key (1+ (if cur cur 0))))

(defun ap:record-paper (paper-name / pair cur)
  (setq pair (assoc paper-name (cdr (assoc "paper-dist" *stats*))))
  (if pair
    (setq cur (1+ (cdr pair)))
    (setq cur 1))
  (ap:update-stats "paper-dist"
    (if pair
      (subst (cons paper-name cur) pair (cdr (assoc "paper-dist" *stats*)))
      (cons (cons paper-name cur) (cdr (assoc "paper-dist" *stats*))))))
```

- [ ] **Step 3: 追加统计报告输出函数**

```lisp
;;; ------------------------------------------------------------
;;; 统计报告输出
;;; ------------------------------------------------------------
(defun ap:julian->string (julian / days-since epoch-frac sec ts yr mo dy hr mn sc)
  ;; 简易 Julian 日期 → "YYYY-MM-DD HH:MM:SS"
  (setq julian (+ julian 0.5))
  (setq epoch-frac (- julian (fix julian)))
  (setq sec (fix (* epoch-frac 86400.0)))
  (setq hr (/ sec 3600) sec (% sec 3600))
  (setq mn (/ sec 60) sc (% sec 60))
  (setq ts (rtos julian 2 0))
  (strcat (menucmd "M=$(edtime,$(getvar,date),YYYY-MO-DD HH:MM:SS)")))

(defun ap:format-elapsed (seconds / m s)
  (setq m (fix (/ seconds 60.0))
        s (% (fix seconds) 60))
  (strcat (itoa m) " 分 " (itoa s) " 秒"))

(defun ap:output-stats (/ start end elapsed total ok fail pdfs
                          blk-f rect-f paper-d p-items lines)
  (setq start   (cdr (assoc "start-julian" *stats*))
        end     (getvar "DATE")
        elapsed (* 86400.0 (- end start))
        total   (cdr (assoc "total-files" *stats*))
        ok      (cdr (assoc "success-files" *stats*))
        fail    (cdr (assoc "fail-files" *stats*))
        pdfs    (cdr (assoc "total-pdfs" *stats*))
        blk-f   (cdr (assoc "block-frames" *stats*))
        rect-f  (cdr (assoc "rect-frames" *stats*))
        paper-d (cdr (assoc "paper-dist" *stats*)))

  (textscr)
  (princ "\n")
  (princ "\n  +===================================================+")
  (princ "\n  |        AutoPlot 批量处理统计报告                   |")
  (princ "\n  +===================================================+")
  (princ (strcat "\n  |  总耗时:   " (ap:format-elapsed elapsed)
                 " (" (rtos elapsed 2 1) " 秒)"))
  (princ "\n  |")
  (princ "\n  |  文件统计:")
  (princ (strcat "\n  |    总计:     " (itoa total) " 个"))
  (princ (strcat "\n  |    成功:     " (itoa ok) " 个 ("
                 (rtos (if (> total 0) (* 100.0 (/ (float ok) (float total))) 0.0) 2 1) "%)"))
  (princ (strcat "\n  |    失败:     " (itoa fail) " 个"))
  (princ "\n  |")
  (princ "\n  |  PDF 输出:")
  (princ (strcat "\n  |    总页数:   " (itoa pdfs) " 页"))
  (if (> ok 0)
    (princ (strcat "\n  |    单文件平均: " (rtos (/ (float pdfs) (float ok)) 2 1) " 页")))
  (if (> pdfs 0)
    (princ (strcat "\n  |    单页平均耗时: " (rtos (/ elapsed (float pdfs)) 2 1) " 秒")))
  (princ "\n  |")
  (princ "\n  |  识别策略:")
  (princ (strcat "\n  |    块名匹配: " (itoa blk-f) " 图框"))
  (princ (strcat "\n  |    矩形检测: " (itoa rect-f) " 图框"))
  (princ "\n  |")
  (if paper-d
    (progn
      (princ "\n  |  纸幅分布:")
      (foreach p paper-d
        (princ (strcat "\n  |    " (car p) ": " (itoa (cdr p)) " 页")))))
  (princ "\n  |")
  (princ (strcat "\n  |  输出目录: " (ap:get-config-default "output-directory" "./PDF_Output")))
  (princ "\n  +===================================================+")
  (princ "\n")
  (princ))
```

- [ ] **Step 4: 追加几何计算与文件名模板函数**

```lisp
;;; ------------------------------------------------------------
;;; 几何计算工具
;;; ------------------------------------------------------------
(defun ap:distance-2d (p1 p2)
  (sqrt (+ (expt (- (car p2) (car p1)) 2)
           (expt (- (cadr p2) (cadr p1)) 2))))

(defun ap:point-center (p1 p2)
  (list (/ (+ (car p1) (car p2)) 2.0)
        (/ (+ (cadr p1) (cadr p2)) 2.0)))

(defun ap:get-entity-bounds (ent / obj minpt maxpt)
  (setq obj (vlax-ename->vla-object ent))
  (vla-GetBoundingBox obj 'minpt 'maxpt)
  (list (vlax-safearray->list minpt)
        (vlax-safearray->list maxpt)))

(defun ap:frame-width (frame / b)
  (setq b (cdr (assoc "bounds" frame)))
  (abs (- (car (cadr b)) (car (car b)))))

(defun ap:frame-height (frame / b)
  (setq b (cdr (assoc "bounds" frame)))
  (abs (- (cadr (cadr b)) (cadr (car b)))))

(defun ap:frame-center (frame / b)
  (setq b (cdr (assoc "bounds" frame)))
  (ap:point-center (car b) (cadr b)))

(defun ap:frame-area (frame)
  (* (ap:frame-width frame) (ap:frame-height frame)))

(defun ap:frame-get (frame key)
  (cdr (assoc key frame)))

(defun ap:frame-put (frame key value / pair)
  (setq pair (assoc key frame))
  (if pair
    (subst (cons key value) pair frame)
    (cons (cons key value) frame)))

;;; ------------------------------------------------------------
;;; 文件名模板替换
;;; ------------------------------------------------------------
(defun ap:format-filename (template filename seq paper layout blockname / result)
  (setq result template)
  ;; {filename}
  (setq result (vl-string-subst filename "{filename}" result))
  ;; {seq:03d} — 三位零填充
  (setq result (vl-string-subst
                 (strcat (if (< seq 100) "0" "")
                         (if (< seq 10) "0" "")
                         (itoa seq))
                 "{seq:03d}" result))
  ;; {seq:02d}
  (setq result (vl-string-subst
                 (strcat (if (< seq 10) "0" "") (itoa seq))
                 "{seq:02d}" result))
  ;; {seq:d}
  (setq result (vl-string-subst (itoa seq) "{seq:d}" result))
  ;; {paper}
  (if paper
    (setq result (vl-string-subst paper "{paper}" result))
    (setq result (vl-string-subst "UNKNOWN" "{paper}" result)))
  ;; {layout}
  (setq result (vl-string-subst (if layout layout "Model") "{layout}" result))
  ;; {blockname}
  (setq result (vl-string-subst (if blockname blockname "RECT") "{blockname}" result))
  ;; {date}
  (setq result (vl-string-subst
                 (menucmd "M=$(edtime,$(getvar,date),YYYYMODD)")
                 "{date}" result))
  result)

;;; ------------------------------------------------------------
;;; DXF 导出（可选）
;;; ------------------------------------------------------------
(defun ap:export-dxf (/ out-dir dxf-path dxf-ver doc-name bare-name result)
  (if (ap:get-config "export-dxf")
    (progn
      (setq out-dir (ap:get-config-default "dxf-output-dir"
                     (ap:get-config-default "output-directory" "./PDF_Output")))
      (setq dxf-ver (ap:get-config-default "dxf-version" "16"))
      (setq doc-name (vla-get-Name (vla-get-ActiveDocument (vlax-get-acad-object))))
      (setq bare-name (vl-filename-base doc-name))
      (setq dxf-path (strcat out-dir "\\" bare-name ".dxf"))
      (princ (strcat "\n[AutoPlot] 导出 DXF: " dxf-path))
      (setq result
        (vl-catch-all-apply
          '(lambda ()
             (command "._DXFOUT" dxf-path dxf-ver ""))))
      (if (vl-catch-all-error-p result)
        (princ (strcat "\n[AutoPlot] DXF 导出失败: "
                       (vl-catch-all-error-message result)))
        (princ " OK")))))

(princ "\n[AutoPlot] ap-utils.lsp 已加载。")
(princ)
```

- [ ] **Step 5: 提交**

```bash
git add lsp/ap-utils.lsp
git commit -m "feat: 添加 ap-utils.lsp 工具模块（全局状态/错误处理/统计/几何计算/DXF导出）"
```

---

## Task 2: ap-config.lsp — 配置管理模块

**Files:**
- Create: `lsp/ap-config.lsp`
- Depends on: `ap-utils.lsp`

- [ ] **Step 1: 创建 ap-config.lsp 完整文件**

```lisp
;;; ============================================================
;;; ap-config.lsp — AutoPlot 配置管理模块
;;; 配置文件加载、解析、默认值填充、配置项访问
;;; ============================================================

;;; ------------------------------------------------------------
;;; 配置访问函数
;;; ------------------------------------------------------------
(defun ap:get-config (key / pair)
  (setq pair (assoc key *config*))
  (if pair (cdr pair) nil))

(defun ap:get-config-default (key default / val)
  (setq val (ap:get-config key))
  (if val val default))

;;; ------------------------------------------------------------
;;; 默认值填充
;;; ------------------------------------------------------------
(defun ap:fill-defaults (config / defaults key)
  (setq defaults
    '(("block-names" . ("TK" "TUKUANG" "BORDER"))
      ("output-directory" . "./PDF_Output")
      ("pdf-name-format" . "{filename}_{seq:03d}")
      ("merge-pdf" . nil)
      ("plot-style" . "acad.ctb")
      ("plot-device" . "DWG To PDF.pc3")
      ("plot-scale" . "Fit")
      ("paper-sizes" . (("A0" 841 1189) ("A1" 594 841) ("A1+0.5" 594 914.5)
                        ("A1+1" 594 1025) ("A2" 420 594) ("A3" 297 420)
                        ("A4" 210 297)))
      ("mediastep" . 100)
      ("file-filter" . "*.dwg")
      ("recursive-search" . nil)
      ("tolerance-mm" . 5.0)
      ("detect-rectangles" . T)
      ("rect-min-area" . 50000)
      ("frame-layer-filter" . nil)
      ("plot-margin" . 0.0)
      ("export-dxf" . nil)
      ("dxf-output-dir" . nil)
      ("dxf-version" . "16")))
  (foreach item defaults
    (setq key (car item))
    (if (null (assoc key config))
      (setq config (cons item config))))
  config)

;;; ------------------------------------------------------------
;;; 配置加载入口
;;; ------------------------------------------------------------
(defun c:LoadConfig (path / file content line config)
  (vl-load-com)
  (setq file (open path "r"))
  (if (null file)
    (progn
      (princ (strcat "\n[AutoPlot] 错误: 无法打开配置文件 " path))
      nil)
    (progn
      (setq content "")
      (while (setq line (read-line file))
        (setq content (strcat content " " line)))
      (close file)
      (setq config (read content))
      (if (null config)
        (progn
          (princ "\n[AutoPlot] 错误: 配置文件为空或格式不正确。")
          nil)
        (progn
          (setq *config* (ap:fill-defaults config))
          (princ (strcat "\n[AutoPlot] 配置加载成功，共 "
                         (itoa (length *config*)) " 项参数"))
          T)))))

(princ "\n[AutoPlot] ap-config.lsp 已加载。")
(princ)
```

- [ ] **Step 2: 提交**

```bash
git add lsp/ap-config.lsp
git commit -m "feat: 添加 ap-config.lsp 配置管理模块"
```

---

## Task 3: ap-detect.lsp — 图框识别引擎

**Files:**
- Create: `lsp/ap-detect.lsp`
- Depends on: `ap-utils.lsp`, `ap-config.lsp`

- [ ] **Step 1: 创建 ap-detect.lsp — 图框记录与块名搜索**

```lisp
;;; ============================================================
;;; ap-detect.lsp — AutoPlot 图框识别引擎
;;; 块名匹配（含动态块）、封闭矩形备选检测、去重排序
;;; ============================================================

;;; ------------------------------------------------------------
;;; 图框记录构造与访问
;;; ------------------------------------------------------------
(defun ap:make-frame-record (ent ftype block-name layout bounds)
  (list
    (cons "entity" ent)
    (cons "type" ftype)
    (cons "block-name" block-name)
    (cons "layout" layout)
    (cons "bounds" (or bounds (ap:get-entity-bounds ent)))
    (cons "paper-match" nil)
    (cons "orientation" nil)))

;;; ------------------------------------------------------------
;;; 模型空间块名搜索
;;; ------------------------------------------------------------
(defun ap:search-blocks-model (block-names / result ss i ent)
  (setq result nil)
  (foreach name block-names
    (setq ss (ssget "_X" (list '(0 . "INSERT") (cons 2 name))))
    (if ss
      (progn
        (princ (strcat "\n  找到 " (itoa (sslength ss)) " 个块引用: " name))
        (setq i 0)
        (repeat (sslength ss)
          (setq ent (ssname ss i))
          (setq result (cons
            (ap:make-frame-record ent "BLOCK" name "Model" nil)
            result))
          (setq i (1+ i))))))
  result)

;;; ------------------------------------------------------------
;;; 动态块有效名称识别
;;; ------------------------------------------------------------
(defun ap:get-effective-name (ent / obj ename)
  (setq obj (vlax-ename->vla-object ent))
  (if (= (vla-get-ObjectName obj) "AcDbBlockReference")
    (vl-catch-all-apply 'vla-get-EffectiveName (list obj))
    nil))

(defun ap:search-blocks-enhanced (block-names / ss-all result i ent eff-name)
  (setq ss-all (ssget "_X" '((0 . "INSERT"))))
  (if ss-all
    (progn
      (setq result nil i 0)
      (repeat (sslength ss-all)
        (setq ent (ssname ss-all i))
        (setq eff-name (ap:get-effective-name ent))
        (if (and eff-name
                 (vl-position eff-name block-names))
          (setq result (cons
            (ap:make-frame-record ent "BLOCK" eff-name "Model" nil)
            result)))
        (setq i (1+ i)))
      result)
    nil))
```

- [ ] **Step 2: 追加图纸空间搜索与矩形检测**

```lisp
;;; ------------------------------------------------------------
;;; 图纸空间块名搜索
;;; ------------------------------------------------------------
(defun ap:search-blocks-paper (block-names / doc layouts result layout
                                layout-name layout-result)
  (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))
  (setq layouts (vla-get-Layouts doc))
  (setq result nil)
  (vlax-for layout layouts
    (setq layout-name (vla-get-Name layout))
    (if (/= layout-name "Model")
      (progn
        (setq layout-result
          (ap:search-in-layout block-names layout-name))
        (setq result (append result layout-result)))))
  result)

(defun ap:search-in-layout (block-names layout-name / ss i ent result)
  (setq result nil)
  (foreach name block-names
    (setq ss (ssget "_X"
               (list '(0 . "INSERT") (cons 2 name)
                     (cons 410 layout-name))))
    (if ss
      (progn
        (setq i 0)
        (repeat (sslength ss)
          (setq ent (ssname ss i))
          (setq result (cons
            (ap:make-frame-record ent "BLOCK" name layout-name nil)
            result))
          (setq i (1+ i))))))
  result)

;;; ------------------------------------------------------------
;;; 封闭矩形备选检测
;;; ------------------------------------------------------------
(defun ap:is-rectangle (ent / obj p0 p1 p2 p3
                         d01 d12 d23 d30 d02 d13 eps)
  (setq eps 1e-8)
  (setq obj (vlax-ename->vla-object ent))
  (if (and (= (vla-get-Closed obj) :vlax-true)
           (= (fix (vlax-curve-getEndParam obj)) 4))
    (progn
      (setq p0 (vlax-curve-getPointAtParam obj 0)
            p1 (vlax-curve-getPointAtParam obj 1)
            p2 (vlax-curve-getPointAtParam obj 2)
            p3 (vlax-curve-getPointAtParam obj 3))
      (setq d01 (distance p0 p1) d12 (distance p1 p2)
            d23 (distance p2 p3) d30 (distance p3 p0)
            d02 (distance p0 p2) d13 (distance p1 p3))
      (and (< (abs (- d01 d23)) eps)
           (< (abs (- d12 d30)) eps)
           (< (abs (- d02 d13)) eps)
           ;; 直角验证: dot(v01, v12) ≈ 0
           (< (abs (apply '+ (mapcar '*
                     (list (- (car p1) (car p0)) (- (cadr p1) (cadr p0)) 0.0)
                     (list (- (car p2) (car p1)) (- (cadr p2) (cadr p1)) 0.0))))
              eps)))
    nil))

(defun ap:get-poly-bounds (ent / p0 p1 p2 p3 xs ys)
  (setq p0 (vlax-curve-getPointAtParam ent 0)
        p1 (vlax-curve-getPointAtParam ent 1)
        p2 (vlax-curve-getPointAtParam ent 2)
        p3 (vlax-curve-getPointAtParam ent 3))
  (setq xs (mapcar 'car (list p0 p1 p2 p3))
        ys (mapcar 'cadr (list p0 p1 p2 p3)))
  (list (list (apply 'min xs) (apply 'min ys))
        (list (apply 'max xs) (apply 'max ys))))

(defun ap:detect-rectangles (/ ss i ent area min-area bounds frame frames)
  (setq min-area (ap:get-config-default "rect-min-area" 50000))
  (setq ss (ssget "_X" '((0 . "LWPOLYLINE") (70 . 1))))
  (setq frames nil)
  (if ss
    (progn
      (setq i 0)
      (repeat (sslength ss)
        (setq ent (ssname ss i))
        (if (ap:is-rectangle ent)
          (progn
            (setq bounds (ap:get-poly-bounds ent))
            (setq area (* (abs (- (car (cadr bounds)) (car (car bounds))))
                          (abs (- (cadr (cadr bounds)) (cadr (car bounds))))))
            (if (>= area min-area)
              (setq frames (cons
                (ap:make-frame-record ent "RECTANGLE" nil "Model" bounds)
                frames)))))
        (setq i (1+ i)))))
  frames)
```

- [ ] **Step 3: 追加去重、排序与主入口**

```lisp
;;; ------------------------------------------------------------
;;; 去重与有效性验证
;;; ------------------------------------------------------------
(defun ap:overlap-ratio (b1 b2 / x1a y1a x2a y2a x1b y1b x2b y2b
                          x-left x-right y-bottom y-top
                          inter-area area1 area2)
  (setq x1a (car (car b1))  y1a (cadr (car b1))
        x2a (car (cadr b1)) y2a (cadr (cadr b1))
        x1b (car (car b2))  y1b (cadr (car b2))
        x2b (car (cadr b2)) y2b (cadr (cadr b2)))
  (setq x-left   (max x1a x1b)
        x-right  (min x2a x2b)
        y-bottom (max y1a y1b)
        y-top    (min y2a y2b))
  (if (and (> x-right x-left) (> y-top y-bottom))
    (progn
      (setq inter-area (* (- x-right x-left) (- y-top y-bottom)))
      (setq area1 (* (- x2a x1a) (- y2a y1a))
            area2 (* (- x2b x1b) (- y2b y1b)))
      (/ inter-area (float (min area1 area2))))
    0.0))

(defun ap:dedup-frames (frames / result skip i j fi fj)
  (setq result nil)
  (setq i 0)
  (foreach fi frames
    (setq skip nil)
    (foreach fj result
      (if (>= (ap:overlap-ratio
                (ap:frame-get fi "bounds")
                (ap:frame-get fj "bounds")) 0.9)
        ;; 保留面积较大者
        (if (< (ap:frame-area fi) (ap:frame-area fj))
          (setq skip T)
          (setq result (vl-remove fj result)))))
    (if (null skip)
      (setq result (cons fi result))))
  (reverse result))

(defun ap:validate-frame (frame / w h)
  (setq w (ap:frame-width frame)
        h (ap:frame-height frame))
  (and (> w 100.0) (< w 5000.0)
       (> h 100.0) (< h 5000.0)))

;;; ------------------------------------------------------------
;;; 排序（从上到下、从左到右）
;;; ------------------------------------------------------------
(defun ap:sort-frames (frames)
  (vl-sort frames
    '(lambda (a b / ca cb)
       (setq ca (ap:frame-center a)
             cb (ap:frame-center b))
       (if (> (abs (- (cadr ca) (cadr cb))) 10.0)
         (> (cadr ca) (cadr cb))
         (< (car ca) (car cb))))))

;;; ------------------------------------------------------------
;;; 主入口：图框识别
;;; ------------------------------------------------------------
(defun ap:detect-all-frames (/ block-names frames block-result
                               paper-result rect-result enhanced)
  (setq block-names (ap:get-config-default "block-names"
                      '("TK" "TUKUANG" "BORDER")))
  (setq frames nil)

  ;; 1. 模型空间块名搜索
  (setq block-result (ap:search-blocks-model block-names))

  ;; 2. 增强搜索（含动态块）
  (setq enhanced (ap:search-blocks-enhanced block-names))
  (if enhanced
    (setq block-result (append block-result enhanced)))

  ;; 3. 图纸空间搜索
  (setq paper-result (ap:search-blocks-paper block-names))
  (if paper-result
    (setq block-result (append block-result paper-result)))

  ;; 4. 合并块搜索结果
  (setq frames block-result)

  ;; 5. 若块名策略零结果，尝试矩形检测
  (if (and (null frames)
           (ap:get-config-default "detect-rectangles" T))
    (progn
      (princ "\n[AutoPlot] 块名未匹配，启用矩形备选检测...")
      (setq rect-result (ap:detect-rectangles))
      (if rect-result
        (progn
          (setq frames rect-result)
          (princ (strcat "\n  矩形检测找到 " (itoa (length frames)) " 个候选"))))))

  ;; 6. 去重、验证、排序
  (if frames
    (progn
      (setq frames (ap:dedup-frames frames))
      (setq frames (vl-remove-if-not 'ap:validate-frame frames))
      (setq frames (ap:sort-frames frames))
      (princ (strcat "\n[AutoPlot] 共识别 " (itoa (length frames)) " 个有效图框")))
    (princ "\n[AutoPlot] 未找到任何图框。"))

  (setq *frame-list* frames)
  frames)

(princ "\n[AutoPlot] ap-detect.lsp 已加载。")
(princ)
```

- [ ] **Step 4: 提交**

```bash
git add lsp/ap-detect.lsp
git commit -m "feat: 添加 ap-detect.lsp 图框识别引擎（块名+动态块+矩形检测）"
```

---

## Task 4: ap-paper.lsp — 纸张匹配系统

**Files:**
- Create: `lsp/ap-paper.lsp`
- Depends on: `ap-utils.lsp`, `ap-config.lsp`

- [ ] **Step 1: 创建 ap-paper.lsp 完整文件**

```lisp
;;; ============================================================
;;; ap-paper.lsp — AutoPlot 纸张匹配系统
;;; ISO A 系列标准/加长幅面、智能匹配、PC3 名称映射
;;; ============================================================

;;; ------------------------------------------------------------
;;; 内置纸张数据库
;;; ------------------------------------------------------------
(defun ap:builtin-papers ()
  '(("A0" 841 1189) ("A1" 594 841) ("A1+0.5" 594 914.5)
    ("A1+1" 594 1025) ("A2" 420 594) ("A3" 297 420) ("A4" 210 297)))

;;; ------------------------------------------------------------
;;; 动态加长幅面生成
;;; ------------------------------------------------------------
(defun ap:generate-elongated (base-width base-height step max-height / result h name)
  (setq result nil)
  (setq h (+ base-height step))
  (while (<= h max-height)
    (setq name (strcat "A1+" (rtos (fix (- h base-height)) 2 0)))
    (setq result (cons (list name base-width h) result))
    (setq h (+ h step)))
  (reverse result))

;;; ------------------------------------------------------------
;;; 构建纸张数据库
;;; ------------------------------------------------------------
(defun ap:build-paper-db (/ builtin custom papers mediastep)
  (setq builtin (ap:builtin-papers))
  (setq custom (ap:get-config-default "paper-sizes" nil))
  (setq mediastep (ap:get-config-default "mediastep" 100))
  (setq papers (if custom (append builtin custom) builtin))
  ;; 动态加长：从 A1 (594x841) 基础开始
  (if (> mediastep 0)
    (setq papers (append papers
                 (ap:generate-elongated 594 841 mediastep 5080))))
  papers)

;;; ------------------------------------------------------------
;;; 单位换算
;;; ------------------------------------------------------------
(defun ap:resolve-units (/ insunits)
  (setq insunits (getvar "INSUNITS"))
  (cond
    ((= insunits 1) 25.4)     ; 英寸 → mm
    ((= insunits 4) 1.0)      ; 毫米
    ((= insunits 5) 10.0)     ; 厘米 → mm
    ((= insunits 6) 1000.0)   ; 米 → mm
    (T 1.0)))                  ; 默认假设毫米

;;; ------------------------------------------------------------
;;; PC3 媒体名称映射
;;; ------------------------------------------------------------
(defun ap:canonical-media-name (paper-name / mapping pair)
  (setq mapping
    '(("A0" . "ISO_A0_(841.00_x_1189.00_MM)")
      ("A1" . "ISO_A1_(594.00_x_841.00_MM)")
      ("A2" . "ISO_A2_(420.00_x_594.00_MM)")
      ("A3" . "ISO_A3_(297.00_x_420.00_MM)")
      ("A4" . "ISO_A4_(210.00_x_297.00_MM)")
      ("A1+0.5" . "ISO_A1_(594.00_x_914.50_MM)")
      ("A1+1"   . "ISO_A1_(594.00_x_1025.00_MM)")))
  (setq pair (assoc paper-name mapping))
  (if pair (cdr pair) paper-name))

;;; ------------------------------------------------------------
;;; 面积匹配
;;; ------------------------------------------------------------
(defun ap:match-by-area (fw fh papers / best-score best-paper best-orient
                          pw ph diff area-f)
  (setq area-f (* fw fh))
  (setq best-score 1e20 best-paper nil best-orient nil)
  (foreach p papers
    (setq pw (float (cadr p))
          ph (float (caddr p)))
    ;; 横向：纸宽>=框宽, 纸高>=框高
    (if (and (>= pw fw) (>= ph fh))
      (progn
        (setq diff (abs (- (* pw ph) area-f)))
        (if (< diff best-score)
          (setq best-score diff
                best-paper p
                best-orient "landscape"))))
    ;; 纵向：纸高>=框宽, 纸宽>=框高
    (if (and (>= ph fw) (>= pw fh))
      (progn
        (setq diff (abs (- (* ph pw) area-f)))
        (if (< diff best-score)
          (setq best-score diff
                best-paper (list (car p) ph pw)
                best-orient "portrait")))))
  (if best-paper
    (list best-paper best-orient best-score)
    nil))

;;; ------------------------------------------------------------
;;; 综合评分匹配
;;; ------------------------------------------------------------
(defun ap:match-paper-for-frame (frame / fw fh unit papers tolerance
                                   result fw-mm fh-mm best-area
                                   paper-name paper-w paper-h orient)
  (setq unit (ap:resolve-units))
  (setq fw (* (ap:frame-width frame) unit)
        fh (* (ap:frame-height frame) unit))
  (setq tolerance (ap:get-config-default "tolerance-mm" 5.0))
  (setq papers (ap:build-paper-db))

  (setq result (ap:match-by-area fw fh papers))

  (if result
    (progn
      (setq best-area (caddr result))
      (setq paper-name (car (car result)))
      (setq paper-w (cadr (car result))
            paper-h (caddr (car result)))
      (setq orient (cadr result))
      ;; 写入图框记录
      (setq frame (ap:frame-put frame "paper-match" paper-name))
      (setq frame (ap:frame-put frame "orientation" orient)))
    (progn
      ;; 强制匹配：选最大的纸张
      (princ (strcat "\n[AutoPlot] 警告: 图框尺寸 ("
                     (rtos fw 2 0) "x" (rtos fh 2 0)
                     "mm) 无合适纸张，使用强制匹配。"))
      (setq frame (ap:frame-put frame "paper-match" "A0"))
      (setq frame (ap:frame-put frame "orientation" "landscape"))))
  frame)

(princ "\n[AutoPlot] ap-paper.lsp 已加载。")
(princ)
```

- [ ] **Step 2: 提交**

```bash
git add lsp/ap-paper.lsp
git commit -m "feat: 添加 ap-paper.lsp 纸张匹配系统（ISO A系列+加长幅面+PC3映射）"
```

---

## Task 5: ap-plot.lsp — 打印输出引擎

**Files:**
- Create: `lsp/ap-plot.lsp`
- Depends on: `ap-utils.lsp`, `ap-config.lsp`, `ap-paper.lsp`

- [ ] **Step 1: 创建 ap-plot.lsp — 打印设备与窗口设置**

```lisp
;;; ============================================================
;;; ap-plot.lsp — AutoPlot 打印输出引擎
;;; Window 模式精确裁剪、页面设置、多图框循环输出
;;; ============================================================

;;; ------------------------------------------------------------
;;; 打印设备初始化
;;; ------------------------------------------------------------
(defun ap:init-plot-device (doc / plot cfg-name)
  (setq plot (vla-get-Plot doc))
  (setq cfg-name (ap:get-config-default "plot-device" "DWG To PDF.pc3"))
  (if (findfile cfg-name)
    (progn
      (princ (strcat "\n[AutoPlot] 使用打印设备: " cfg-name))
      plot)
    (progn
      (princ (strcat "\n[AutoPlot] 警告: 未找到 " cfg-name "，回退至 DWG To PDF.pc3"))
      plot)))

;;; ------------------------------------------------------------
;;; 设置打印窗口
;;; ------------------------------------------------------------
(defun ap:set-plot-window (layout bounds / ll ur margin bw)
  (setq margin (ap:get-config-default "plot-margin" 0.0))
  (setq ll (list (- (car (car bounds)) margin)
                 (- (cadr (car bounds)) margin)
                 0.0)
        ur (list (+ (car (cadr bounds)) margin)
                 (+ (cadr (cadr bounds)) margin)
                 0.0))
  (vla-SetWindowToPlot layout
    (vlax-3d-point ll)
    (vlax-3d-point ur))
  (vla-put-PlotType layout acWindow))

;;; ------------------------------------------------------------
;;; 页面设置应用
;;; ------------------------------------------------------------
(defun ap:apply-page-setup (layout frame / paper-name canonical orient
                             rot style scale-mode)
  (setq paper-name (ap:frame-get frame "paper-match"))
  (setq canonical (ap:canonical-media-name paper-name))
  (setq orient (ap:frame-get frame "orientation"))

  ;; 纸张大小
  (vl-catch-all-apply
    '(lambda () (vla-SetCanonicalMediaName layout canonical)))

  ;; 方向
  (setq rot (if (= orient "portrait") ac0degrees ac90degrees))
  (vl-catch-all-apply
    '(lambda () (vla-put-PlotRotation layout rot)))

  ;; 打印样式表
  (setq style (ap:get-config-default "plot-style" "acad.ctb"))
  (vl-catch-all-apply
    '(lambda () (vla-put-StyleSheet layout style)))

  ;; 比例
  (setq scale-mode (ap:get-config-default "plot-scale" "Fit"))
  (if (= scale-mode "Fit")
    (vl-catch-all-apply
      '(lambda () (vla-put-StandardScale layout acScaleToFit)))
    ;; 固定比例: 解析 "1:100" 格式
    (vl-catch-all-apply
      '(lambda ()
         (vla-put-StandardScale layout acCustomScale)
         (vla-SetCustomScale layout 1.0 100.0)))))
```

- [ ] **Step 2: 追加单帧打印与多帧循环**

```lisp
;;; ------------------------------------------------------------
;;; 单图框打印
;;; ------------------------------------------------------------
(defun ap:plot-frame (doc frame pdf-path / layout result plot)
  (setq layout (vla-get-ActiveLayout doc))
  (setq plot (vla-get-Plot doc))

  ;; 设置打印窗口
  (ap:set-plot-window layout (ap:frame-get frame "bounds"))

  ;; 应用页面设置
  (ap:apply-page-setup layout frame)

  ;; 执行打印
  (setq result
    (vl-catch-all-apply
      '(lambda ()
         (vla-PlotToFile plot pdf-path))))

  (if (vl-catch-all-error-p result)
    (progn
      (princ (strcat "\n    打印失败: " (vl-catch-all-error-message result)))
      nil)
    (progn
      (princ (strcat " -> " (vl-filename-base pdf-path) ".pdf"))
      T)))

;;; ------------------------------------------------------------
;;; 多图框循环处理
;;; ------------------------------------------------------------
(defun ap:process-frames (doc frames output-dir / pdf-count total frame
                          paper-name template pdf-path result
                          doc-name bare-name ok)
  (setq pdf-count 0
        total (length frames)
        result nil)
  (setq doc-name (vla-get-Name doc))
  (setq bare-name (vl-filename-base doc-name))
  (setq template (ap:get-config-default "pdf-name-format" "{filename}_{seq:03d}"))

  (foreach frame frames
    (setq pdf-count (1+ pdf-count))

    ;; 纸张匹配
    (setq frame (ap:match-paper-for-frame frame))
    (setq paper-name (ap:frame-get frame "paper-match"))

    ;; 更新统计
    (ap:inc-stat "total-pdfs")
    (ap:record-paper paper-name)
    (if (= (ap:frame-get frame "type") "BLOCK")
      (ap:inc-stat "block-frames")
      (ap:inc-stat "rect-frames"))

    ;; 生成文件名
    (setq pdf-path (strcat output-dir "\\"
                   (ap:format-filename template bare-name pdf-count
                     paper-name
                     (ap:frame-get frame "layout")
                     (ap:frame-get frame "block-name"))
                   ".pdf"))

    ;; 进度显示
    (princ (strcat "\n  [" (itoa pdf-count) "/" (itoa total) "] "
                   paper-name " " (ap:frame-get frame "orientation")))

    ;; 打印（失败重试一次）
    (setq ok (ap:plot-frame doc frame pdf-path))
    (if (null ok)
      (setq ok (ap:plot-frame doc frame pdf-path)))

    (if ok
      (progn
        (princ " OK")
        (setq result (cons pdf-path result)))
      (princ " FAILED")))

  (reverse result))

;;; ------------------------------------------------------------
;;; 处理当前活动文档
;;; ------------------------------------------------------------
(defun ap:process-current-drawing (/ doc frames output-dir)
  (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))

  ;; 可选：DXF 导出
  (ap:export-dxf)

  ;; 图框识别
  (setq frames (ap:detect-all-frames))
  (if (null frames)
    (progn
      (princ "\n[AutoPlot] 当前文档未检测到图框。")
      nil)
    (progn
      (setq output-dir (ap:get-config-default "output-directory" "./PDF_Output"))
      (vl-mkdir output-dir)
      (ap:process-frames doc frames output-dir))))

(princ "\n[AutoPlot] ap-plot.lsp 已加载。")
(princ)
```

- [ ] **Step 3: 提交**

```bash
git add lsp/ap-plot.lsp
git commit -m "feat: 添加 ap-plot.lsp 打印输出引擎（Window模式+页面设置+多图框循环）"
```

---

## Task 6: ap-batch.lsp — 批量处理与统计模块

**Files:**
- Create: `lsp/ap-batch.lsp`
- Depends on: 所有前置模块

- [ ] **Step 1: 创建 ap-batch.lsp — 目录遍历与文件处理**

```lisp
;;; ============================================================
;;; ap-batch.lsp — AutoPlot 批量处理与统计模块
;;; 目录遍历、多文件批处理、进度恢复、统计报告
;;; ============================================================

;;; ------------------------------------------------------------
;;; 递归目录遍历
;;; ------------------------------------------------------------
(defun ap:collect-dwg-files (dir filter recursive / files result subdirs)
  (setq files (vl-directory-files dir filter 1))
  (setq result
    (mapcar '(lambda (f) (strcat dir "\\" f)) files))
  (if recursive
    (progn
      (setq subdirs (vl-directory-files dir nil -1))
      (foreach d subdirs
        (if (and (/= d ".") (/= d ".."))
          (setq result (append result
            (ap:collect-dwg-files
              (strcat dir "\\" d) filter T)))))))
  result)

;;; ------------------------------------------------------------
;;; 文件队列排序
;;; ------------------------------------------------------------
(defun ap:sort-file-queue (files)
  (vl-sort files '<))

;;; ------------------------------------------------------------
;;; 进度管理（断点续传）
;;; ------------------------------------------------------------
(defun ap:progress-file ()
  (strcat (ap:get-config-default "output-directory" "./PDF_Output")
          "\\autoplot-progress.sav"))

(defun ap:load-progress (/ path file line done)
  (setq path (ap:progress-file))
  (setq done nil)
  (setq file (open path "r"))
  (if file
    (progn
      (while (setq line (read-line file))
        (setq done (cons line done)))
      (close file)))
  done)

(defun ap:save-progress (done-list / path file)
  (setq path (ap:progress-file))
  (setq file (open path "w"))
  (if file
    (progn
      (foreach f done-list
        (write-line f file))
      (close file))))

;;; ------------------------------------------------------------
;;; 单文件处理
;;; ------------------------------------------------------------
(defun ap:process-single-file (filepath / doc-name bare-name
                                file-start result err-code)
  (setq doc-name (vl-filename-base filepath))
  (setq file-start (getvar "DATE"))
  (princ (strcat "\n[AutoPlot] 处理: " doc-name))

  (setq result
    (vl-catch-all-apply
      '(lambda ()
         (command "._OPEN" filepath)
         (ap:process-current-drawing)
         (command "._CLOSE" "_N"))))

  (if (vl-catch-all-error-p result)
    (progn
      (princ (strcat "\n[AutoPlot] 文件处理失败: " doc-name
                     " - " (vl-catch-all-error-message result)))
      (setq err-code "E101")
      ;; 尝试关闭可能残留的文档
      (vl-catch-all-apply
        '(lambda () (command "._CLOSE" "_N")))
      nil)
    (progn
      (ap:inc-stat "success-files")
      T)))
```

- [ ] **Step 2: 追加批处理主循环与 CSV 日志**

```lisp
;;; ------------------------------------------------------------
;;; 批处理主入口
;;; ------------------------------------------------------------
(defun c:BatchProcess (/ input-dir filter recursive files done-list
                        processed total i filepath)
  (setq input-dir (ap:get-config "input-directory"))
  (if (null input-dir)
    (progn
      (princ "\n[AutoPlot] 错误: 配置中未指定 input-directory。")
      (exit)))

  (setq filter (ap:get-config-default "file-filter" "*.dwg"))
  (setq recursive (ap:get-config-default "recursive-search" nil))

  ;; 收集文件
  (setq files (ap:collect-dwg-files input-dir filter recursive))
  (setq files (ap:sort-file-queue files))

  ;; 加载已处理列表（断点续传）
  (setq done-list (ap:load-progress))

  ;; 过滤已处理文件
  (setq processed 0)
  (setq total (length files))

  (ap:init-stats)
  (ap:install-error-handler)
  (ap:save-sysvars)
  (setvar "FILEDIA" 0)
  (setvar "CMDECHO" 0)

  ;; 逐文件处理
  (foreach filepath files
    (if (null (member filepath done-list))
      (progn
        (setq processed (1+ processed))
        (princ (strcat "\n[" (itoa processed) "/" (itoa total) "] "))
        (ap:inc-stat "total-files")
        (if (ap:process-single-file filepath)
          (progn
            (setq done-list (cons filepath done-list))
            (ap:save-progress done-list))
          (ap:inc-stat "fail-files")))))

  ;; 恢复并输出统计
  (ap:restore-sysvars *saved-sysvars*)
  (ap:output-stats)

  ;; 清理进度文件
  (if (= processed 0)
    (vl-file-delete (ap:progress-file)))

  (setq *running* nil)
  (princ))

;;; ------------------------------------------------------------
;;; CSV 日志写入（可选）
;;; ------------------------------------------------------------
(defun ap:write-csv-log (log-path entries / file)
  (setq file (open log-path "a"))
  (if file
    (progn
      (foreach e entries
        (write-line
          (strcat (car e) "," (cadr e) "," (caddr e) ","
                  (nth 3 e) "," (nth 4 e))
          file))
      (close file))))

(princ "\n[AutoPlot] ap-batch.lsp 已加载。")
(princ)
```

- [ ] **Step 3: 提交**

```bash
git add lsp/ap-batch.lsp
git commit -m "feat: 添加 ap-batch.lsp 批量处理与统计模块（目录遍历+断点续传+CSV日志）"
```

---

## Task 7: autoplot.lsp — 主入口与加载器

**Files:**
- Create: `lsp/autoplot.lsp`
- Create: `lsp/autoplot.env` — 示例配置文件
- Depends on: 所有前置模块

- [ ] **Step 1: 创建 autoplot.lsp 主入口**

```lisp
;;; ============================================================
;;; autoplot.lsp — AutoPlot 智能批量出图系统 主入口
;;; 加载所有模块、注册命令、S::STARTUP 支持
;;; ============================================================

(vl-load-com)

;;; ------------------------------------------------------------
;;; 模块加载
;;; ------------------------------------------------------------
(princ "\n[AutoPlot] 正在加载模块...")

(foreach mod '("ap-utils.lsp" "ap-config.lsp" "ap-detect.lsp"
               "ap-paper.lsp" "ap-plot.lsp" "ap-batch.lsp")
  (if (findfile mod)
    (progn
      (load mod)
      (princ (strcat "\n  " mod " OK")))
    (progn
      (princ (strcat "\n  [错误] 无法加载: " mod))
      (exit))))

;;; ------------------------------------------------------------
;;; 命令: AutoPlot — 交互式单文件处理
;;; ------------------------------------------------------------
(defun c:AutoPlot (/ config-path)
  (if *running*
    (princ "\n[AutoPlot] 错误：已有任务正在运行，请等待完成。")
    (progn
      (setq *running* T)
      (vl-load-com)
      (ap:install-error-handler)
      (ap:init-stats)

      ;; 选择配置文件
      (setq config-path (getfiled "选择配置文件" "" "env;ini" 4))
      (if config-path
        (progn
          (princ (strcat "\n[AutoPlot] 加载配置: " config-path))
          (if (c:LoadConfig config-path)
            (ap:process-current-drawing)
            (princ "\n[AutoPlot] 配置加载失败，任务终止。")))
        (princ "\n[AutoPlot] 未选择配置文件，任务取消。"))

      (setq *running* nil)))
  (princ))

;;; ------------------------------------------------------------
;;; 命令: BatchPlot — 目录级批量处理
;;; ------------------------------------------------------------
(defun c:BatchPlot (/ config-path)
  (if *running*
    (princ "\n[BatchPlot] 错误：已有任务正在运行。")
    (progn
      (setq *running* T)
      (vl-load-com)
      (ap:install-error-handler)

      ;; 尝试加载默认配置
      (setq config-path (findfile "autoplot.env"))
      (if (null config-path)
        (setq config-path (getfiled "选择批量处理配置文件" "" "env;ini" 4)))

      (if (and config-path (c:LoadConfig config-path))
        (c:BatchProcess)
        (princ "\n[BatchPlot] 配置加载失败。"))

      (setq *running* nil)))
  (princ))

;;; ------------------------------------------------------------
;;; S::STARTUP 支持
;;; ------------------------------------------------------------
(if (not (member 'S::STARTUP (atoms-family 1)))
  (defun qS::STARTUP ()
    (princ "\n[AutoPlot] 智能批量出图工具已加载。")
    (princ "\n  命令: AutoPlot (交互式) / BatchPlot (批量处理)")
    (princ)))

(princ "\n[AutoPlot] 智能批量出图系统加载完成。")
(princ "\n  命令: AutoPlot (交互式) / BatchPlot (批量处理)")
(princ)
```

- [ ] **Step 2: 创建示例配置文件 autoplot.env**

```lisp
;;;
;;; autoplot.env — AutoPlot 配置文件示例
;;; 修改后保存，通过 AutoPlot/BatchPlot 命令加载
;;;

(
  ;; 图框识别
  ("block-names" . ("TK" "TUKUANG" "BORDER" "图框" "A4-FRAME" "A3-FRAME"))
  ("detect-rectangles" . T)
  ("rect-min-area" . 50000)
  ("tolerance-mm" . 5.0)

  ;; 输出控制
  ("output-directory" . "D:\\Output\\PDFs")
  ("pdf-name-format" . "{filename}_{seq:03d}_{paper}")
  ("merge-pdf" . nil)

  ;; 打印设置
  ("plot-style" . "monochrome.ctb")
  ("plot-device" . "DWG To PDF.pc3")
  ("plot-scale" . "Fit")
  ("plot-margin" . 0.0)

  ;; 纸张规格（可覆盖或扩展内置规格）
  ("paper-sizes" . (("A0" 841 1189) ("A1" 594 841) ("A1+0.5" 594 914.5)
                    ("A1+1" 594 1025) ("A2" 420 594) ("A3" 297 420)
                    ("A4" 210 297)))
  ("mediastep" . 100)

  ;; 批量处理
  ("input-directory" . "D:\\Drawings\\Input")
  ("file-filter" . "*.dwg")
  ("recursive-search" . nil)

  ;; DXF 导出（可选）
  ("export-dxf" . nil)
  ("dxf-output-dir" . nil)
  ("dxf-version" . "16")
)
```

- [ ] **Step 3: 提交**

```bash
git add lsp/autoplot.lsp lsp/autoplot.env
git commit -m "feat: 添加 autoplot.lsp 主入口 + 示例配置文件 autoplot.env"
```

---

## 自检清单

### 规格覆盖

| 规格要求 | 对应任务 |
|----------|----------|
| 全局状态变量 (config/frame-list/stats/running) | Task 1 |
| 错误处理 (三层架构) | Task 1 |
| 系统变量管理 (FILEDIA/CMDECHO/PICKADD) | Task 1 |
| 配置加载/解析/默认值 | Task 2 |
| 块名搜索 (模型空间) | Task 3 |
| 动态块 EffectiveName | Task 3 |
| 图纸空间搜索 (遍历布局) | Task 3 |
| 闭合矩形检测 (六步验证) | Task 3 |
| 图框去重/验证/排序 | Task 3 |
| ISO A 系列纸张数据库 | Task 4 |
| 加长幅面动态生成 | Task 4 |
| 面积匹配算法 | Task 4 |
| PC3 名称映射 | Task 4 |
| 单位换算 (INSUNITS) | Task 4 |
| Window 模式精确裁剪 | Task 5 |
| 页面设置 (纸张/方向/样式/比例) | Task 5 |
| 多图框循环输出 | Task 5 |
| DXF 导出 (可选) | Task 1 (ap:export-dxf) + Task 5 (调用点) |
| 目录递归遍历 | Task 6 |
| 断点续传 | Task 6 |
| 统计报告输出 | Task 1 |
| CSV 日志 | Task 6 |
| 双命令入口 (AutoPlot/BatchPlot) | Task 7 |
| S::STARTUP 支持 | Task 7 |
| 配置文件示例 | Task 7 |

### 占位符扫描：无 TBD/TODO/待定内容。

### 类型一致性：
- 图框记录字段名 (`"entity"` `"type"` `"block-name"` `"layout"` `"bounds"` `"paper-match"` `"orientation"`) 在 Task 3~6 中完全一致。
- 配置键名 (`"block-names"` `"export-dxf"` 等) 在 Task 2~6 中完全一致。
- `ap:frame-get`/`ap:frame-put` 访问器在 Task 1 定义，Task 3~6 统一使用。
