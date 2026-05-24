;;; ============================================================
;;; ap-paper.lsp — AutoPlot 纸张匹配系统
;;; 基于 PC3 自定义纸张表，按图框尺寸自动选择最合适纸张
;;; ============================================================

;;; ------------------------------------------------------------
;;; PC3 可用纸张表 (名称 短边mm 长边mm)
;;; 统一命名: A0 用 1/8 步进(+1-8..+8-8), 其余 1/4 步进(+1-4..+8-4)
;;; 公式: 长边 = base_long × (1 + N/M), 最大 A1+8-4 = 2523mm
;;; ------------------------------------------------------------
(defun ap:pc3-papers ()
  '(
    ;; --- A3 系列 (1/4 步进, base 297×420) ---
    ("A3 297x420"           297  420)
    ("A3+1-4 297x525"       297  525)
    ("A3+2-4 297x630"       297  630)
    ("A3+3-4 297x735"       297  735)
    ("A3+4-4 297x840"       297  840)
    ("A3+5-4 297x945"       297  945)
    ("A3+6-4 297x1050"      297 1050)
    ("A3+7-4 297x1155"      297 1155)
    ("A3+8-4 297x1260"      297 1260)
    ;; --- A2 系列 (1/4 步进, base 420×594) ---
    ("A2 420x594"           420  594)
    ("A2+1-4 420x742"       420  742)
    ("A2+2-4 420x891"       420  891)
    ("A2+3-4 420x1040"      420 1040)
    ("A2+4-4 420x1188"      420 1188)
    ("A2+5-4 420x1336"      420 1336)
    ("A2+6-4 420x1485"      420 1485)
    ("A2+7-4 420x1634"      420 1634)
    ("A2+8-4 420x1782"      420 1782)
    ;; --- A1 系列 (1/4 步进, base 594×841) ---
    ("A1 594x841"           594  841)
    ("A1+1-4 594x1051"      594 1051)
    ("A1+2-4 594x1262"      594 1262)
    ("A1+3-4 594x1472"      594 1472)
    ("A1+4-4 594x1682"      594 1682)
    ("A1+5-4 594x1892"      594 1892)
    ("A1+6-4 594x2102"      594 2102)
    ("A1+7-4 594x2313"      594 2313)
    ("A1+8-4 594x2523"      594 2523)
    ;; --- A0 系列 (1/8 步进, base 841×1189) ---
    ("A0 841x1189"          841 1189)
    ("A0+1-8 841x1338"      841 1338)
    ("A0+2-8 841x1486"      841 1486)
    ("A0+3-8 841x1635"      841 1635)
    ("A0+4-8 841x1784"      841 1784)
    ("A0+5-8 841x1932"      841 1932)
    ("A0+6-8 841x2081"      841 2081)
    ("A0+7-8 841x2229"      841 2229)
    ("A0+8-8 841x2378"      841 2378)
  ))

;;; ------------------------------------------------------------
;;; 工具函数
;;; ------------------------------------------------------------
(defun ap:ceil (x / i)
  (setq i (fix x))
  (if (> x (float i)) (1+ i) i))

;;; ------------------------------------------------------------
;;; 单位换算
;;; ------------------------------------------------------------
(defun ap:resolve-units (/ insunits)
  (setq insunits (getvar "INSUNITS"))
  (cond
    ((= insunits 1) 25.4)
    ((= insunits 4) 1.0)
    ((= insunits 5) 10.0)
    ((= insunits 6) 1000.0)
    (T 1.0)))

;;; ------------------------------------------------------------
;;; 纸张尺寸计算（支持 +N-M 加长格式）
;;; 公式: short=base_short, long=base_long × (1 + N/M)
;;; ------------------------------------------------------------
(defun ap:paper-dimensions (paper-name / pos base-name rest dash-pos
                                      num denom base)
  (setq pos (vl-string-search "+" paper-name))
  (if pos
    (progn
      (setq base-name (substr paper-name 1 pos))
      (setq rest (substr paper-name (+ pos 2)))
      (setq dash-pos (vl-string-search "-" rest))
      (if dash-pos
        (setq num   (atof (substr rest 1 dash-pos))
              denom (atof (substr rest (+ dash-pos 2))))
        (setq num (atof rest) denom 1.0)))
    (progn
      (setq base-name paper-name)
      (setq num 0.0 denom 1.0)))
  (setq base (assoc base-name
    '(("A3" 297 420) ("A2" 420 594) ("A1" 594 841) ("A0" 841 1189))))
  (if base
    (list (cadr base)
          (fix (+ 0.5 (+ (caddr base)
                         (* (caddr base) (/ num denom))))))
    '(594 841)))

;;; ------------------------------------------------------------
;;; 基准纸张（兼容旧接口）
;;; ------------------------------------------------------------
(defun ap:base-papers ()
  '(("A3" 297 420)
    ("A2" 420 594)
    ("A1" 594 841)
    ("A0" 841 1189)))

;;; ------------------------------------------------------------
;;; 自动检测比例
;;; ------------------------------------------------------------
(defun ap:auto-detect-scale (frame-short frame-long / scales bases
                              best-scale best-base best-diff
                              scale p ps pl diff-s)
  (setq scales (ap:get-config-default "drawing-scales"
    '(1 2 5 10 20 25 50 75 100 150 200 300 500 1000)))
  (setq bases (ap:base-papers))
  (setq best-scale nil best-base nil best-diff 1e20)

  (foreach scale scales
    (foreach p bases
      (setq ps (/ frame-short (float scale)))
      (setq diff-s (abs (- ps (cadr p))))
      (if (< diff-s 2.0)
        (progn
          (setq pl (/ frame-long (float scale)))
          (if (>= pl (- (caddr p) 0.5))
            (if (or (< diff-s best-diff)
                    (and (= diff-s best-diff)
                         best-base
                         (> (cadr p) (cadr best-base))))
              (setq best-scale scale
                    best-base p
                    best-diff diff-s)))))))
  (if best-scale
    (list best-scale best-base)
    nil))

;;; ------------------------------------------------------------
;;; 核心匹配：检测比例 → 选最小容纳纸张
;;; ------------------------------------------------------------
(defun ap:match-paper-for-frame (frame / fw fh
                                   frame-short frame-long orient
                                   detected scale
                                   papers best best-area
                                   p-ps p-pl p-area tolerance)
  (setq fw (ap:frame-width frame)
        fh (ap:frame-height frame))
  (setq frame-short (min fw fh)
        frame-long  (max fw fh))
  (setq orient (if (> fw fh) "landscape" "portrait"))

  ;; 自动检测比例
  (setq detected (ap:auto-detect-scale frame-short frame-long))
  (if detected
    (setq scale (float (car detected)))
    (setq scale (float (ap:get-config-default "drawing-scale" 1.0))))

  ;; 图框在纸张空间中的尺寸（mm）
  (setq p-ps (/ frame-short scale)
        p-pl (/ frame-long scale))

  ;; 从 PC3 纸张表中选最小容纳的纸张
  (setq papers (ap:pc3-papers))
  (setq best nil best-area 1e20)
  (setq tolerance 2.0)  ;; 容差 2mm

  (foreach p papers
    (if (and (>= (cadr p) (- p-ps tolerance))
             (>= (caddr p) (- p-pl tolerance)))
      (progn
        (setq p-area (* (cadr p) (caddr p)))
        (if (< p-area best-area)
          (setq best p best-area p-area)))))

  (if best
    (progn
      ;; paper-match 存 PC3 完整名称（如 "A1 594x841"）
      (setq frame (ap:frame-put frame "paper-match" (car best)))
      (setq frame (ap:frame-put frame "orientation" orient))
      (princ (strcat "\n    [纸幅] " (car best) " "
        orient " 1:" (rtos scale 2 0)))
      frame)
    ;; 兜底：用 A0
    (progn
      (setq frame (ap:frame-put frame "paper-match" "A0 841x1189"))
      (setq frame (ap:frame-put frame "orientation" orient))
      (princ (strcat "\n    [纸幅] A0 841x1189 " orient " 1:" (rtos scale 2 0) " (fallback)"))
      frame)))

;;; ------------------------------------------------------------
;;; 获取标准纸名（兼容旧接口）
;;; ------------------------------------------------------------
(defun ap:standard-paper-for-plot (paper-name / pos base)
  (setq pos (vl-string-search " " paper-name))
  (if pos
    (substr paper-name 1 pos)
    paper-name))

;;; ------------------------------------------------------------
;;; PC3 canonical 媒体名称（vla 用）
;;; ------------------------------------------------------------
(defun ap:canonical-media-name (paper-name / name dims)
  (setq name (ap:standard-paper-for-plot paper-name))
  (setq dims (ap:paper-dimensions name))
  (strcat "ISO_" name
    "_(" (rtos (car dims) 2 2) "_x_" (rtos (cadr dims) 2 2) "_MM)"))

;;; ------------------------------------------------------------
;;; -PLOT 命令用的纸张名（直接用 PC3 名称）
;;; ------------------------------------------------------------
(defun ap:plot-paper-name (paper-name)
  paper-name)

(princ "\n[AutoPlot] ap-paper.lsp loaded.")
(princ)
