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

;;; ------------------------------------------------------------
;;; 统计信息管理
;;; ------------------------------------------------------------
(defun ap:init-stats ()
  (setq *stats*
    (list
      (cons "start-time" (getvar "DATE"))
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

(defun ap:update-stats (key value / pair)
  (setq pair (assoc key *stats*))
  (if pair
    (setq *stats*
      (subst (cons key value) pair *stats*))
    (setq *stats* (cons (cons key value) *stats*))))

(defun ap:inc-stat (key / cur)
  (setq cur (cdr (assoc key *stats*)))
  (ap:update-stats key (1+ (if cur cur 0))))

(defun ap:record-paper (paper-name / pair cur pd-list)
  (setq pd-list (cdr (assoc "paper-dist" *stats*)))
  (setq pair (assoc paper-name pd-list))
  (if pair
    (setq cur (1+ (cdr pair)))
    (setq cur 1))
  (if pair
    (setq pd-list (subst (cons paper-name cur) pair pd-list))
    (setq pd-list (cons (cons paper-name cur) pd-list)))
  (ap:update-stats "paper-dist" pd-list))

;;; ------------------------------------------------------------
;;; 统计报告输出
;;; ------------------------------------------------------------
(defun ap:format-elapsed (seconds / m s)
  (setq m (fix (/ seconds 60.0))
        s (rem (fix seconds) 60))
  (strcat (itoa m) " 分 " (itoa s) " 秒"))

(defun ap:output-stats (/ start end elapsed total ok fail pdfs
                          blk-f rect-f paper-d)
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
  (princ (strcat "\n  |  输出目录: " (if *config* (ap:get-config-default "output-directory" "./PDF_Output") "N/A")))
  (princ "\n  +===================================================+")
  (princ "\n")
  (princ))

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
  (if (and b (cadr b) (car b))
    (abs (- (car (cadr b)) (car (car b))))
    0.0))

(defun ap:frame-height (frame / b)
  (setq b (cdr (assoc "bounds" frame)))
  (if (and b (cadr b) (car b))
    (abs (- (cadr (cadr b)) (cadr (car b))))
    0.0))

(defun ap:frame-center (frame / b)
  (setq b (cdr (assoc "bounds" frame)))
  (if (and b (car b) (cadr b))
    (ap:point-center (car b) (cadr b))
    '(0.0 0.0)))

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
  (setq result (vl-string-subst filename "{filename}" result))
  (setq result (vl-string-subst
                 (strcat (if (< seq 100) "0" "")
                         (if (< seq 10) "0" "")
                         (itoa seq))
                 "{seq:03d}" result))
  (setq result (vl-string-subst
                 (strcat (if (< seq 10) "0" "") (itoa seq))
                 "{seq:02d}" result))
  (setq result (vl-string-subst (itoa seq) "{seq:d}" result))
  (if paper
    (setq result (vl-string-subst paper "{paper}" result))
    (setq result (vl-string-subst "UNKNOWN" "{paper}" result)))
  (setq result (vl-string-subst (if layout layout "Model") "{layout}" result))
  (setq result (vl-string-subst (if blockname blockname "RECT") "{blockname}" result))
  (setq result (vl-string-subst
                 (menucmd "M=$(edtime,$(getvar,date),YYYYMODD)")
                 "{date}" result))
  result)

;;; ------------------------------------------------------------
;;; DXF 导出（可选）
;;; ------------------------------------------------------------
(defun ap:export-dxf (/ out-dir dxf-path dxf-ver doc-name bare-name result)
  (if (and *config* (ap:get-config "export-dxf"))
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
