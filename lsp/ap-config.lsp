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
