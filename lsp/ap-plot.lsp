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
(defun ap:set-plot-window (layout bounds / ll ur margin)
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

  (vl-catch-all-apply
    '(lambda () (vla-SetCanonicalMediaName layout canonical)))

  (setq rot (if (= orient "portrait") ac0degrees ac90degrees))
  (vl-catch-all-apply
    '(lambda () (vla-put-PlotRotation layout rot)))

  (setq style (ap:get-config-default "plot-style" "acad.ctb"))
  (vl-catch-all-apply
    '(lambda () (vla-put-StyleSheet layout style)))

  (setq scale-mode (ap:get-config-default "plot-scale" "Fit"))
  (if (= scale-mode "Fit")
    (vl-catch-all-apply
      '(lambda () (vla-put-StandardScale layout acScaleToFit)))
    (vl-catch-all-apply
      '(lambda ()
         (vla-put-StandardScale layout acCustomScale)
         (vla-SetCustomScale layout 1.0 100.0)))))

;;; ------------------------------------------------------------
;;; 单图框打印
;;; ------------------------------------------------------------
(defun ap:plot-frame (doc frame pdf-path / layout result plot)
  (setq layout (vla-get-ActiveLayout doc))
  (setq plot (vla-get-Plot doc))

  (ap:set-plot-window layout (ap:frame-get frame "bounds"))
  (ap:apply-page-setup layout frame)

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

    (setq frame (ap:match-paper-for-frame frame))
    (setq paper-name (ap:frame-get frame "paper-match"))

    (ap:inc-stat "total-pdfs")
    (ap:record-paper paper-name)
    (if (= (ap:frame-get frame "type") "BLOCK")
      (ap:inc-stat "block-frames")
      (ap:inc-stat "rect-frames"))

    (setq pdf-path (strcat output-dir "\\"
                   (ap:format-filename template bare-name pdf-count
                     paper-name
                     (ap:frame-get frame "layout")
                     (ap:frame-get frame "block-name"))
                   ".pdf"))

    (princ (strcat "\n  [" (itoa pdf-count) "/" (itoa total) "] "
                   paper-name " " (ap:frame-get frame "orientation")))

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

  (ap:export-dxf)

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
