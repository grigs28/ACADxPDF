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
  (defun S::STARTUP ()
    (princ "\n[AutoPlot] 智能批量出图工具已加载。")
    (princ "\n  命令: AutoPlot (交互式) / BatchPlot (批量处理)")
    (princ)))

(princ "\n[AutoPlot] 智能批量出图系统加载完成。")
(princ "\n  命令: AutoPlot (交互式) / BatchPlot (批量处理)")
(princ)
