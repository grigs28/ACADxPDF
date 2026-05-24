;;; ============================================================
;;; ap-plot.lsp — AutoPlot 打印输出引擎
;;; 双引擎：vla 方式 + -PLOT 命令方式
;;; ============================================================

;;; ------------------------------------------------------------
;;; 方式 A：vla SetWindowToPlot + PlotToFile
;;; ------------------------------------------------------------
(defun ap:plot-frame-vla (doc frame pdf-path / layout plot bounds ll ur
                          printer plot-style paper-name orient rot
                          canonical pdf-fwd
                          sa-ll sa-ur r1 r2 r3 r4 r5 r6 r7)
  (setq layout (vla-get-ActiveLayout doc))
  (setq plot (vla-get-Plot doc))
  (setq bounds (ap:frame-get frame "bounds"))
  (setq ll (car bounds) ur (cadr bounds))
  (setq printer (ap:get-config-default "plot-device" "DWG To PDF.pc3"))
  (setq plot-style (ap:get-config-default "plot-style" "monochrome.ctb"))
  (setq paper-name (ap:frame-get frame "paper-match"))
  (setq orient (ap:frame-get frame "orientation"))
  (setq canonical (ap:canonical-media-name paper-name))
  (setq rot (if (= orient "portrait") ac0degrees ac90degrees))
  (setq pdf-fwd (vl-string-translate "\\" "/" pdf-path))
  (if (findfile pdf-fwd) (vl-file-delete pdf-fwd))

  ;; 设打印设备
  (setq r1 (vl-catch-all-apply '(lambda () (vla-put-ConfigName layout printer))))
  (_log (strcat "    vla_config=" (if (vl-catch-all-error-p r1) (strcat "ERR:" (vl-catch-all-error-message r1)) "OK")))
  (vla-RefreshPlotDeviceInfo layout)

  ;; 设纸张（CanonicalMediaName 是属性，用 put 不用 Set）
  (setq r2 (vl-catch-all-apply '(lambda () (vla-put-CanonicalMediaName layout canonical))))
  (_log (strcat "    vla_paper=" (if (vl-catch-all-error-p r2) (strcat "ERR:" (vl-catch-all-error-message r2)) "OK")))

  ;; 设旋转、样式、缩放
  (vl-catch-all-apply '(lambda () (vla-put-PlotRotation layout rot)))
  (vl-catch-all-apply '(lambda () (vla-put-StyleSheet layout plot-style)))
  (vl-catch-all-apply '(lambda () (vla-put-StandardScale layout acScaleToFit)))
  (vl-catch-all-apply '(lambda () (vla-put-CenterPlot layout :vlax-true)))

  ;; SetWindowToPlot — 纯 x,y
  (setq sa-ll (vlax-make-safearray vlax-vbDouble '(0 . 1)))
  (vlax-safearray-fill sa-ll (list (car ll) (cadr ll)))
  (setq sa-ur (vlax-make-safearray vlax-vbDouble '(0 . 1)))
  (vlax-safearray-fill sa-ur (list (car ur) (cadr ur)))
  (_log (strcat "    vla_xy LL=" (rtos (car ll) 2 1) "," (rtos (cadr ll) 2 1)
                 " UR=" (rtos (car ur) 2 1) "," (rtos (cadr ur) 2 1)))

  (setq r5 (vl-catch-all-apply '(lambda () (vla-SetWindowToPlot layout sa-ll sa-ur))))
  (_log (strcat "    vla_setWin=" (if (vl-catch-all-error-p r5) (strcat "ERR:" (vl-catch-all-error-message r5)) "OK")))

  ;; put-PlotType = acWindow（必须在 SetWindowToPlot 之后）
  (setq r6 (vl-catch-all-apply '(lambda () (vla-put-PlotType layout acWindow))))
  (_log (strcat "    vla_plotType=" (if (vl-catch-all-error-p r6) (strcat "ERR:" (vl-catch-all-error-message r6)) "OK")))

  ;; PlotToFile
  (setq r7 (vl-catch-all-apply '(lambda () (vla-PlotToFile plot pdf-fwd))))
  (_log (strcat "    vla_plotToFile=" (if (vl-catch-all-error-p r7) (strcat "ERR:" (vl-catch-all-error-message r7)) "OK")))

  ;; 等文件
  (setq _wt 0)
  (while (and (null (findfile pdf-fwd)) (< _wt 60))
    (command "_.DELAY" 500)
    (setq _wt (1+ _wt)))
  (if (findfile pdf-fwd)
    (progn (_log (strcat "    vla_result: " (itoa (vl-file-size pdf-fwd)) "B")) T)
    (progn (_log "    vla_result: NO_FILE") nil)))


;;; ------------------------------------------------------------
;;; 方式 B：-PLOT 命令（一次性参数，脚本模式唯一可行方式）
;;; ------------------------------------------------------------
(defun ap:plot-frame-cmd (doc frame pdf-path / bounds ll ur paper-name orient
                          printer plot-style pdf-fwd result)
  (setvar "CTAB" "Model")
  (setvar "BACKGROUNDPLOT" 0)
  (setvar "PUBLISHCOLLATE" 0)
  (setvar "FILEDIA" 0)
  (setvar "CMDDIA" 0)
  (setvar "EXPERT" 5)
  (setvar "NOMUTT" 1)

  (setq bounds (ap:frame-get frame "bounds"))
  (setq ll (car bounds) ur (cadr bounds))
  (setq paper-name (ap:frame-get frame "paper-match"))
  (setq orient (ap:frame-get frame "orientation"))
  (setq printer (ap:get-config-default "plot-device" "DWG To PDF.pc3"))
  (setq plot-style (ap:get-config-default "plot-style" "monochrome.ctb"))
  (setq paper-name (ap:plot-paper-name paper-name))
  (setq pdf-fwd (vl-string-translate "\\" "/" pdf-path))

  (setq result
    (vl-catch-all-apply
      '(lambda ()
         (command "_.-PLOT" "Y" ""
           printer
           paper-name
           "M"
           (if (= orient "portrait") "P" "L")
           "N"
           "W"
           (strcat (rtos (car ll) 2 2) "," (rtos (cadr ll) 2 2))
           (strcat (rtos (car ur) 2 2) "," (rtos (cadr ur) 2 2))
           "F" "C" "Y" plot-style "N" ""
           pdf-fwd
           "N" "Y"))))

  (if (vl-catch-all-error-p result)
    (progn (_log (strcat "    cmd_ERR: " (vl-catch-all-error-message result))) nil)
    T))


;;; ------------------------------------------------------------
;;; 写 manifest 文件
;;; ------------------------------------------------------------
(defun ap:write-manifest (path frames / lines n f)
  (setq lines nil)
  (setq n 0)
  (foreach f frames
    (setq n (1+ n))
    (setq lines (cons
      (strcat (itoa n) "|"
              (ap:frame-get f "paper-match") "|"
              (ap:frame-get f "orientation") "|"
              (if (ap:frame-get f "block-name") (ap:frame-get f "block-name") "RECT"))
      lines)))
  (ap:write-file-lines path (reverse lines)))

;;; ------------------------------------------------------------
;;; 多图框循环处理 — 默认用 vla，失败回退 -PLOT
;;; ------------------------------------------------------------
(defun ap:process-frames (doc frames output-dir / total idx frame
                          paper-name template pdf-path ok results
                          doc-name bare-name mode)
  (_log "  plot_start")
  (setvar "CTAB" "Model")
  (setvar "BACKGROUNDPLOT" 0)
  (setvar "PUBLISHCOLLATE" 0)
  (setvar "FILEDIA" 0)
  (setvar "CMDDIA" 0)
  (setvar "EXPERT" 5)
  (setvar "NOMUTT" 1)

  ;; 从配置读模式：vla 或 cmd
  (setq mode (ap:get-config-default "plot-mode" "cmd"))

  (setq total (length frames)
        results nil
        idx 0)
  (setq doc-name (vla-get-Name doc))
  (setq bare-name (vl-filename-base doc-name))
  (setq template (ap:get-config-default "pdf-name-format" "{filename}_{seq:03d}"))

  (_log (strcat "  mode=" mode " frames=" (itoa total)))

  (foreach frame frames
    (setq idx (1+ idx))
    (setq frame (ap:match-paper-for-frame frame))
    (setq paper-name (ap:frame-get frame "paper-match"))
    (setq pdf-path (strcat output-dir "/"
                   (ap:format-filename template bare-name idx
                     paper-name
                     (ap:frame-get frame "layout")
                     (if (ap:frame-get frame "block-name") (ap:frame-get frame "block-name") "RECT"))
                   ".pdf"))

    (_log (strcat "  frame " (itoa idx)))
    (if (findfile pdf-path) (vl-file-delete pdf-path))

    ;; 按模式选择打印函数
    (if (= mode "vla")
      (setq ok (ap:plot-frame-vla doc frame pdf-path))
      (setq ok (ap:plot-frame-cmd doc frame pdf-path)))

    ;; vla 失败则回退 -PLOT
    (if (and (null ok) (= mode "vla"))
      (progn
        (_log "    fallback to cmd")
        (setq ok (ap:plot-frame-cmd doc frame pdf-path))))

    (if ok
      (setq results (cons pdf-path results))))

  (ap:write-manifest (strcat output-dir "/_manifest.txt") frames)
  (reverse results))

;;; ------------------------------------------------------------
;;; 处理当前活动文档
;;; ------------------------------------------------------------
(defun ap:process-current-drawing (/ doc frames output-dir pf-result)
  (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))
  (ap:export-dxf)
  (setq frames (ap:detect-all-frames))
  (if (null frames)
    (progn (_log "No frames detected.") nil)
    (progn
      (setq output-dir (ap:get-config-default "output-directory" "./PDF_Output"))
      (vl-mkdir output-dir)
      (setq pf-result (ap:process-frames doc frames output-dir))
      pf-result)))

(princ "\n[AutoPlot] ap-plot.lsp loaded.")
(princ)
