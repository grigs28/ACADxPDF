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
    ((= insunits 1) 25.4)
    ((= insunits 4) 1.0)
    ((= insunits 5) 10.0)
    ((= insunits 6) 1000.0)
    (T 1.0)))

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
    (if (and (>= pw fw) (>= ph fh))
      (progn
        (setq diff (abs (- (* pw ph) area-f)))
        (if (< diff best-score)
          (setq best-score diff
                best-paper p
                best-orient "landscape"))))
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
(defun ap:match-paper-for-frame (frame / fw fh unit papers result
                                   paper-name orient)
  (setq unit (ap:resolve-units))
  (setq fw (* (ap:frame-width frame) unit)
        fh (* (ap:frame-height frame) unit))
  (setq papers (ap:build-paper-db))
  (setq result (ap:match-by-area fw fh papers))

  (if result
    (progn
      (setq paper-name (car (car result)))
      (setq orient (cadr result))
      (setq frame (ap:frame-put frame "paper-match" paper-name))
      (setq frame (ap:frame-put frame "orientation" orient)))
    (progn
      (princ (strcat "\n[AutoPlot] 警告: 图框尺寸 ("
                     (rtos fw 2 0) "x" (rtos fh 2 0)
                     "mm) 无合适纸张，使用强制匹配。"))
      (setq frame (ap:frame-put frame "paper-match" "A0"))
      (setq frame (ap:frame-put frame "orientation" "landscape"))))
  frame)

;;; ------------------------------------------------------------
;;; -PLOT 命令用的纸张名（与 Python 流程一致）
;;; ------------------------------------------------------------
(defun ap:plot-paper-name (paper-name / papers p pw ph w-str h-str unit-str)
  (setq papers '(("A0" 841 1189) ("A1" 594 841) ("A1+0.5" 594 914.5)
                 ("A1+1" 594 1025) ("A2" 420 594) ("A3" 297 420) ("A4" 210 297)))
  (setq p (assoc paper-name papers))
  (setq unit-str (ap:get-config-default "acad-unit" "毫米"))
  (if p
    (progn
      (setq pw (float (cadr p))
            ph (float (caddr p)))
      (strcat "ISO full bleed " paper-name
              " (" (rtos pw 2 2) " x " (rtos ph 2 2) " " unit-str ")"))
    (strcat "ISO full bleed A0 (841.00 x 1189.00 " unit-str ")")))

(princ "\n[AutoPlot] ap-paper.lsp 已加载。")
(princ)
