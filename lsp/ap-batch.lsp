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
(defun ap:process-single-file (filepath / doc-name file-start result)
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
      (vl-catch-all-apply
        '(lambda () (command "._CLOSE" "_N")))
      nil)
    (progn
      (ap:inc-stat "success-files")
      T)))

;;; ------------------------------------------------------------
;;; 批处理主入口
;;; ------------------------------------------------------------
(defun c:BatchProcess (/ input-dir filter recursive files done-list
                        processed total filepath)
  (setq input-dir (ap:get-config "input-directory"))
  (if (null input-dir)
    (progn
      (princ "\n[AutoPlot] 错误: 配置中未指定 input-directory。")
      (exit)))

  (setq filter (ap:get-config-default "file-filter" "*.dwg"))
  (setq recursive (ap:get-config-default "recursive-search" nil))

  (setq files (ap:collect-dwg-files input-dir filter recursive))
  (setq files (ap:sort-file-queue files))

  (setq done-list (ap:load-progress))

  (setq processed 0)
  (setq total (length files))

  (ap:init-stats)
  (ap:install-error-handler)
  (ap:save-sysvars)
  (setvar "FILEDIA" 0)
  (setvar "CMDECHO" 0)

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

  (ap:restore-sysvars *saved-sysvars*)
  (ap:output-stats)

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
