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
(defun ap:get-effective-name (ent / obj)
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

(defun ap:detect-rectangles (/ ss i ent area min-area bounds frames)
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

(defun ap:dedup-frames (frames / result skip fi fj)
  (setq result nil)
  (foreach fi frames
    (setq skip nil)
    (foreach fj result
      (if (>= (ap:overlap-ratio
                (ap:frame-get fi "bounds")
                (ap:frame-get fj "bounds")) 0.9)
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
