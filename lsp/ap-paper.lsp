;;; ============================================================
;;; ap-paper.lsp — AutoPlot 纸张匹配系统
;;; 自动检测比例，短边定图幅，长边定加长
;;; ============================================================

;;; ------------------------------------------------------------
;;; 基准纸张（无 A4）(名称 短边mm 长边mm)
;;; ------------------------------------------------------------
(defun ap:base-papers ()
  '(("A3" 297 420)
    ("A2" 420 594)
    ("A1" 594 841)
    ("A0" 841 1189)))

;;; ------------------------------------------------------------
;;; 工具函数
;;; ------------------------------------------------------------
(defun ap:ceil (x / i)
  (setq i (fix x))
  (if (> x (float i)) (1+ i) i))

(defun ap:format-elong (n / s i)
  (setq i (fix n))
  (if (< (abs (- n (float i))) 0.001)
    (itoa i)
    (progn
      (setq s (rtos n 2 4))
      (while (and (> (strlen s) 1)
                  (= (substr s (strlen s) 1) "0"))
        (setq s (substr s 1 (1- (strlen s)))))
      (if (and (> (strlen s) 1)
               (= (substr s (strlen s) 1) "."))
        (setq s (substr s 1 (1- (strlen s)))))
      s)))

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
;;; 纸张尺寸计算（支持加长）
;;; 返回: (短边 长边)
;;; ------------------------------------------------------------
(defun ap:paper-dimensions (paper-name / pos base-name elong base)
  (setq pos (vl-string-search "+" paper-name))
  (if pos
    (progn
      (setq base-name (substr paper-name 1 pos))
      (setq elong (atof (substr paper-name (+ pos 2)))))
    (progn
      (setq base-name paper-name)
      (setq elong 0)))
  (setq base (assoc base-name (ap:base-papers)))
  (if base
    (list (cadr base) (+ (caddr base) (* elong (cadr base))))
    '(594 841)))

;;; ------------------------------------------------------------
;;; 自动检测比例
;;; 遍历 (比例 × 纸幅) 组合，短边匹配 + 长边 >= 基准长边
;;; 返回: (比例 纸幅) 或 nil
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
          ;; 长边必须 >= 基准长边（允许加长）
          (setq pl (/ frame-long (float scale)))
          (if (>= pl (- (caddr p) 0.5))
            ;; 匹配：选 diff 最小的，diff 相同选大纸幅
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
;;; 核心匹配：自动检测比例，短边定图幅，长边定加长
;;; ------------------------------------------------------------
(defun ap:match-paper-for-frame (frame / fw fh
                                   frame-short frame-long orient
                                   detected scale base-s base-l base-name
                                   p-long elong step n ename long-val)
  ;; 直接使用绘图单位，不做 INSUNITS 换算
  ;; 自动检测比较 frame/scale ≈ paper_size，单位会互相消掉
  (setq fw (ap:frame-width frame)
        fh (ap:frame-height frame))

  (setq frame-short (min fw fh)
        frame-long  (max fw fh))
  (setq orient (if (> fw fh) "landscape" "portrait"))

  ;; 自动检测比例
  (setq detected (ap:auto-detect-scale frame-short frame-long))

  (if detected
    (progn
      (setq scale (car detected))
      (setq base-s (cadr (cadr detected)))
      (setq base-l (caddr (cadr detected)))
      (setq base-name (car (cadr detected))))
    ;; 检测失败：用配置的比例，强制匹配最小能容纳的纸
    (progn
      (setq scale (float (ap:get-config-default "drawing-scale" 1.0)))
      (setq base-s 841 base-l 1189 base-name "A0")
      (foreach p (ap:base-papers)
        (if (>= (cadr p) (- (/ frame-short scale) 0.5))
          (progn
            (setq base-name (car p)
                  base-s (cadr p)
                  base-l (caddr p)))))))

  ;; 长边确定加长
  (setq p-long (/ frame-long (float scale)))
  (if (> p-long (+ base-l 0.5))
    (progn
      (setq elong (/ (- p-long base-l) (float base-s)))
      (setq step (if (= base-name "A3") 0.125 0.25))
      (setq n (* step (ap:ceil (/ elong step))))
      (setq ename (strcat base-name "+" (ap:format-elong n)))
      (setq long-val (+ base-l (* n base-s))))
    (progn
      (setq ename base-name)
      (setq long-val base-l)))

  (setq frame (ap:frame-put frame "paper-match" ename))
  (setq frame (ap:frame-put frame "orientation" orient))
  (princ (strcat "\n    [纸幅] " ename " ("
    (rtos base-s 2 0) "x" (rtos long-val 2 0) "mm) "
    orient " 1:" (rtos scale 2 0)))
  frame)

;;; ------------------------------------------------------------
;;; 获取标准纸名（用于 -PLOT 命令）
;;; 加长版映射到能容纳的最小标准纸
;;; ------------------------------------------------------------
(defun ap:standard-paper-for-plot (paper-name / dims pw ph bases best)
  (setq dims (ap:paper-dimensions paper-name))
  (setq pw (car dims) ph (cadr dims))
  (setq bases (ap:base-papers))
  (setq best nil)
  (foreach p bases
    (if (null best)
      (if (and (>= (cadr p) (- pw 0.5))
               (>= (caddr p) (- ph 0.5)))
        (setq best p))))
  (if (null best) (setq best '("A0" 841 1189)))
  (car best))

;;; ------------------------------------------------------------
;;; PC3 媒体名称映射
;;; ------------------------------------------------------------
(defun ap:canonical-media-name (paper-name / std dims)
  (setq std (ap:standard-paper-for-plot paper-name))
  (setq dims (ap:paper-dimensions std))
  (strcat "ISO_" std
    "_(" (rtos (car dims) 2 2) "_x_" (rtos (cadr dims) 2 2) "_MM)"))

;;; ------------------------------------------------------------
;;; -PLOT 命令用的纸张名
;;; ------------------------------------------------------------
(defun ap:plot-paper-name (paper-name / std dims pw ph unit-str)
  (setq std (ap:standard-paper-for-plot paper-name))
  (setq dims (ap:paper-dimensions std))
  (setq pw (float (car dims))
        ph (float (cadr dims)))
  (setq unit-str (ap:get-config-default "acad-unit" "毫米"))
  (strcat "ISO full bleed " std
    " (" (rtos pw 2 2) " x " (rtos ph 2 2) " " unit-str ")"))

(princ "\n[AutoPlot] ap-paper.lsp 已加载。")
(princ)
