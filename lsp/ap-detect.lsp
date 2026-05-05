;;; ============================================================
;;; ap-detect.lsp — AutoPlot 图框识别引擎
;;; 块名匹配（含动态块）、封闭矩形备选检测、去重排序
;;; ============================================================

;;; ------------------------------------------------------------
;;; 图框记录构造与访问
;;; ------------------------------------------------------------
(defun ap:block-has-tch (blk-name / blk ent)
  (setq blk (tblsearch "BLOCK" blk-name))
  (if blk
    (progn
      (setq ent (cdr (assoc -2 blk)))
      (while ent
        (if (wcmatch (cdr (assoc 0 (entget ent))) "TCH_*,ACAD_TABLE")
          (progn (setq ent nil) T)
          (setq ent (entnext ent)))))))

(defun ap:insert-bounds-entget (ent / ed ins sx sy blk-name blk et
                                ent2 ed2 p pts mn-x mn-y mx-x mx-y)
  ;; Compute INSERT bounds from block definition via entget (no VLA)
  (setq ed (entget ent))
  (setq ins (cdr (assoc 10 ed)))
  (setq sx (cdr (assoc 41 ed)))
  (setq sy (cdr (assoc 42 ed)))
  (setq blk-name (cdr (assoc 2 ed)))
  (if (null sx) (setq sx 1.0))
  (if (null sy) (setq sy 1.0))
  (setq blk (tblsearch "BLOCK" blk-name))
  (if (null blk) nil
    (progn
      (setq mn-x 1e20 mn-y 1e20 mx-x -1e20 mx-y -1e20)
      (setq ent2 (cdr (assoc -2 blk)))
      (setq pts nil)
      (while ent2
        (setq ed2 (entget ent2))
        (setq et (cdr (assoc 0 ed2)))
        (cond
          ((= et "LINE")
           (setq p (cdr (assoc 10 ed2)))
           (setq pts (cons p pts))
           (setq p (cdr (assoc 11 ed2)))
           (setq pts (cons p pts)))
          ((= et "LWPOLYLINE")
           (foreach item ed2
             (if (= (car item) 10) (setq pts (cons (cdr item) pts)))))
          ((= et "INSERT")
           (setq p (list 0.0 0.0))
           (setq pts (cons p pts))))
        (setq ent2 (entnext ent2)))
      (if (null pts) nil
        (progn
          (foreach p pts
            (setq mn-x (min mn-x (car p)) mn-y (min mn-y (cadr p))
                  mx-x (max mx-x (car p)) mx-y (max mx-y (cadr p))))
          (list
            (list (+ (* mn-x sx) (car ins)) (+ (* mn-y sy) (cadr ins)))
            (list (+ (* mx-x sx) (car ins)) (+ (* mx-y sy) (cadr ins)))))))))

(defun ap:make-frame-record (ent ftype block-name layout bounds / actual-bounds
                            obj minpt maxpt min-list max-list bb-result
                            etype ed blk-name has-tch)
  (if bounds
    (setq actual-bounds bounds)
    (progn
      (setq ed (entget ent))
      (setq etype (cdr (assoc 0 ed)))
      (cond
        ;; INSERT with TCH entities: use entget fallback
        ((and (= etype "INSERT")
              (setq blk-name (cdr (assoc 2 ed)))
              (ap:block-has-tch blk-name))
         (setq actual-bounds (ap:insert-bounds-entget ent)))
        ;; Other entities: try vla-GetBoundingBox
        (T
          (setq obj (vlax-ename->vla-object ent))
          (setq minpt nil maxpt nil)
          (setq bb-result (vl-catch-all-apply
            '(lambda ()
              (vla-GetBoundingBox obj 'minpt 'maxpt)
              (list (vlax-safearray->list minpt)
                    (vlax-safearray->list maxpt)))))
          (if (vl-catch-all-error-p bb-result)
            (setq actual-bounds nil)
            (setq actual-bounds bb-result))))))
  (if (null actual-bounds)
    nil
    (list
      (cons "entity" ent)
      (cons "type" ftype)
      (cons "block-name" block-name)
      (cons "layout" layout)
      (cons "bounds" actual-bounds)
      (cons "paper-match" nil)
      (cons "orientation" nil))))


;;; ------------------------------------------------------------
;;; 模型空间块名搜索
;;; ------------------------------------------------------------
(defun ap:search-blocks-model (block-names / result ss i ent rec)
  (setq result nil)
  (foreach name block-names
    (setq ss (ssget "_X" (list '(0 . "INSERT") (cons 2 name))))
    (if ss
      (progn
        (princ (strcat "\n  找到 " (itoa (sslength ss)) " 个块引用: " name))
        (setq i 0)
        (repeat (sslength ss)
          (setq ent (ssname ss i))
          (setq rec (ap:make-frame-record ent "BLOCK" name "Model" nil))
          (if rec (setq result (cons rec result)))
          (setq i (1+ i))))))
  result)

;;; ------------------------------------------------------------
;;; 动态块有效名称识别
;;; ------------------------------------------------------------
(defun ap:get-effective-name (ent / obj result)
  (setq obj (vlax-ename->vla-object ent))
  (setq result
    (vl-catch-all-apply 'vla-get-ObjectName (list obj)))
  (if (or (vl-catch-all-error-p result)
          (/= result "AcDbBlockReference"))
    nil
    (vl-catch-all-apply 'vla-get-EffectiveName (list obj))))

(defun ap:match-block-name (name patterns / p)
  ;; 用通配符模式列表匹配块名，返回匹配的模式或 nil
  (setq p nil)
  (foreach pat patterns
    (if (and (null p) (wcmatch name pat))
      (setq p pat)))
  p)

(defun ap:search-blocks-enhanced (block-names / ss-all result i ent eff-name matched rec)
  (setq ss-all (ssget "_X" '((0 . "INSERT"))))
  (if ss-all
    (progn
      (setq result nil i 0)
      (repeat (sslength ss-all)
        (setq ent (ssname ss-all i))
        (setq eff-name (ap:get-effective-name ent))
        (if eff-name
          (progn
            (setq matched (ap:match-block-name eff-name block-names))
            (if matched
              (progn
                (setq rec (ap:make-frame-record ent "BLOCK" eff-name "Model" nil))
                (if rec (setq result (cons rec result)))))))
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

(defun ap:search-in-layout (block-names layout-name / ss i ent rec result)
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
          (setq rec (ap:make-frame-record ent "BLOCK" name layout-name nil))
          (if rec (setq result (cons rec result)))
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

(defun ap:detect-rectangles (/ ss i ent area min-area bounds frames rec)
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
              (progn
                (setq rec (vl-catch-all-apply 'ap:make-frame-record (list ent "RECTANGLE" nil "Model" bounds)))
                (if (and rec (not (vl-catch-all-error-p rec)))
                  (setq frames (cons rec frames)))))))
        (setq i (1+ i)))))
  frames)

;;; ------------------------------------------------------------
;;; LINE 矩形检测（4条LINE组成的封闭矩形）
;;; ------------------------------------------------------------

;;; 浮点近似相等
(defun ap:fp-close (a b / eps)
  (setq eps 0.5)
  (< (abs (- a b)) eps))

;;; 获取LINE端点列表 ((x1 y1) (x2 y2))
(defun ap:line-endpoints (ent / p1 p2)
  (setq p1 (cdr (assoc 10 (entget ent))))
  (setq p2 (cdr (assoc 11 (entget ent))))
  (list (list (car p1) (cadr p1))
        (list (car p2) (cadr p2))))

;;; 检查两个点是否近似重合
(defun ap:pt-match (a b)
  (and (ap:fp-close (car a) (car b))
       (ap:fp-close (cadr a) (cadr b))))

;;; 从LINE端点列表中聚类提取唯一角点
(defun ap:collect-corner-pts (lines / pts clusters c p found)
  (setq pts nil)
  (foreach ln lines
    (setq pts (cons (car ln) pts))
    (setq pts (cons (cadr ln) pts)))
  (setq clusters nil)
  (foreach p pts
    (setq found nil)
    (foreach c clusters
      (if (ap:pt-match p (car c))
        (setq found T)))
    (if (null found)
      (setq clusters (cons (list p) clusters))))
  (mapcar 'car clusters))

;;; 给定4个角点按矩形排列(bl br tr tl), 检查4条边是否都在lines中存在
(defun ap:edges-exist (corners lines / c1 c2 c3 c4 found n edge)
  (setq c1 (nth 0 corners)
        c2 (nth 1 corners)
        c3 (nth 2 corners)
        c4 (nth 3 corners))
  (setq n 0)
  (foreach edge (list (list c1 c2) (list c2 c3) (list c3 c4) (list c4 c1))
    (setq found nil)
    (foreach ln lines
      (if (or (and (ap:pt-match (car edge) (car ln))
                   (ap:pt-match (cadr edge) (cadr ln)))
              (and (ap:pt-match (car edge) (cadr ln))
                   (ap:pt-match (cadr edge) (car ln))))
        (setq found T)))
    (if found (setq n (1+ n))))
  (= n 4))

;;; 检查指定位置是否存在水平线段
(defun ap:has-hline (h-lines y x1 x2 eps / found h)
  (setq found nil)
  (foreach h h-lines
    (if (and (null found)
             (< (abs (- (cadr h) y)) eps)
             (or (and (< (abs (- (car h) x1)) eps)
                      (< (abs (- (nth 2 h) x2)) eps))
                 (and (< (abs (- (car h) x2)) eps)
                      (< (abs (- (nth 2 h) x1)) eps))))
      (setq found T)))
  found)

;;; 检查指定位置是否存在垂直线段
(defun ap:has-vline (v-lines x y1 y2 eps / found v)
  (setq found nil)
  (foreach v v-lines
    (if (and (null found)
             (< (abs (- (car v) x)) eps)
             (or (and (< (abs (- (cadr v) y1)) eps)
                      (< (abs (- (nth 2 v) y2)) eps))
                 (and (< (abs (- (cadr v) y2)) eps)
                      (< (abs (- (nth 2 v) y1)) eps))))
      (setq found T)))
  found)

;;; 主函数：检测LINE组成的矩形
(defun ap:detect-line-rectangles (/ ss h-lines v-lines i ent
                                    p1 p2 dx dy eps
                                    xvals yvals found
                                    xi xj yi yj area min-area
                                    bounds rec result combo-max)
  (setq eps 1.0)
  (setq min-area (ap:get-config-default "rect-min-area" 50000))
  (setq combo-max 100000000)
  (setq ss (ssget "_X" (list (cons 0 "LINE"))))
  (setq h-lines nil v-lines nil result nil)
  (if (null ss) nil
    (progn
      (setq i 0)
      (repeat (sslength ss)
        (setq ent (ssname ss i))
        (setq p1 (cdr (assoc 10 (entget ent))))
        (setq p2 (cdr (assoc 11 (entget ent))))
        (setq dx (abs (- (car p1) (car p2))))
        (setq dy (abs (- (cadr p1) (cadr p2))))
        (cond
          ((and (> dx 50000.0) (< dy eps))
           (setq h-lines (cons (list (car p1) (cadr p1) (car p2)) h-lines)))
          ((and (< dx eps) (> dy 50000.0))
           (setq v-lines (cons (list (car p1) (cadr p1) (cadr p2)) v-lines))))
        (setq i (1+ i)))
      (setq xvals nil yvals nil)
      (foreach v v-lines
        (setq found nil)
        (foreach x xvals (if (< (abs (- (car v) x)) eps) (setq found T)))
        (if (null found) (setq xvals (cons (car v) xvals))))
      (foreach h h-lines
        (setq found nil)
        (foreach y yvals (if (< (abs (- (cadr h) y)) eps) (setq found T)))
        (if (null found) (setq yvals (cons (cadr h) yvals))))
      (if (> (* (length xvals) (length yvals) (length xvals) (length yvals)) combo-max)
        (progn
          (princ (strcat "[LINE] skip: x=" (itoa (length xvals)) " y=" (itoa (length yvals))))
          (setq result nil))
        (progn
          (foreach xi xvals
            (foreach xj xvals
              (if (< xi xj)
                (foreach yi yvals
                  (foreach yj yvals
                    (if (< yi yj)
                      (progn
                        (setq area (* (- xj xi) (- yj yi)))
                        (if (and (>= area min-area)
                                 (ap:has-hline h-lines yi xi xj eps)
                                 (ap:has-hline h-lines yj xi xj eps)
                                 (ap:has-vline v-lines xi yi yj eps)
                                 (ap:has-vline v-lines xj yi yj eps))
                          (progn
                            (setq bounds (list (list xi yi) (list xj yj)))
                            (setq rec (list
                              (cons "entity" (ssname ss 0))
                              (cons "type" "RECTANGLE")
                              (cons "block-name" nil)
                              (cons "layout" "Model")
                              (cons "bounds" bounds)
                              (cons "paper-match" nil)
                              (cons "orientation" nil)))
                            (setq result (cons rec result)))))))))))
      result)))))

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

(defun ap:dedup-frames (frames / result keep fi fj to-remove overlap b1 b2 a1 a2)
  (setq result nil)
  (foreach fi frames
    (setq keep T to-remove nil)
    (foreach fj result
      (setq b1 (vl-catch-all-apply 'ap:frame-get (list fi "bounds"))
            b2 (vl-catch-all-apply 'ap:frame-get (list fj "bounds")))
      (if (or (vl-catch-all-error-p b1) (vl-catch-all-error-p b2)
              (null b1) (null b2))
        (setq overlap 0.0)
        (progn
          (setq overlap (vl-catch-all-apply 'ap:overlap-ratio (list b1 b2)))
          (if (vl-catch-all-error-p overlap) (setq overlap 0.0))))
      (if (>= overlap 0.9)
        (progn
          (setq a1 (vl-catch-all-apply 'ap:frame-area (list fi))
                a2 (vl-catch-all-apply 'ap:frame-area (list fj)))
          (if (or (vl-catch-all-error-p a1) (vl-catch-all-error-p a2)
                  (null a1) (null a2))
            (setq keep nil)
            (if (< a1 a2)
              (setq keep nil)
              (setq to-remove (cons fj to-remove)))))))
    (foreach r to-remove
      (setq result (vl-remove r result)))
    (if keep
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

;;; 安全排序：预计算中心点，排序失败则返回未排序列表
(defun ap:safe-sort-frames (frames / centers pairs sorted result err)
  ;; 预计算中心点，构造 (center . frame) 对
  (setq pairs nil)
  (foreach f frames
    (setq centers (vl-catch-all-apply 'ap:frame-center (list f)))
    (if (vl-catch-all-error-p centers)
      (setq centers '(0.0 0.0)))
    (setq pairs (cons (cons centers f) pairs)))
  (setq pairs (reverse pairs))
  ;; 按中心点排序 pairs
  (setq sorted (vl-catch-all-apply 'vl-sort
    (list pairs
      '(lambda (a b / ca cb)
         (setq ca (car a) cb (car b))
         (if (> (abs (- (cadr ca) (cadr cb))) 10.0)
           (> (cadr ca) (cadr cb))
           (< (car ca) (car cb)))))))
  (if (vl-catch-all-error-p sorted)
    (progn
      (princ (strcat "\n[DEBUG] sort failed, using unsorted: "
                     (vl-catch-all-error-message sorted)))
      frames)
    (progn
      (setq result nil)
      (foreach p sorted
        (setq result (cons (cdr p) result)))
      (reverse result))))

;;; ------------------------------------------------------------
;;; 主入口：图框识别
;;; ------------------------------------------------------------
(defun ap:detect-all-frames (/ block-names frames block-result
                               paper-result rect-result enhanced
                               line-result line-filtered
                               lr-b br-b keep)
  (setq block-names (ap:get-config-default "block-names"
                      '("TK" "TUKUANG" "BORDER")))
  (setq frames nil)

  ;; 1. 模型空间块名搜索
  (_log "detect:step1_start")
  (setq block-result (ap:search-blocks-model block-names))
  (_log (strcat "detect:step1_done blocks=" (itoa (length block-result))))

  ;; 2. 增强搜索（含动态块）
  (_log "detect:step2_start")
  (setq enhanced (ap:search-blocks-enhanced block-names))
  (if enhanced
    (setq block-result (append block-result enhanced)))
  (_log (strcat "detect:step2_done total=" (itoa (length block-result))))

  ;; 3. 图纸空间搜索
  (_log "detect:step3_start")
  (setq paper-result (ap:search-blocks-paper block-names))
  (if paper-result
    (setq block-result (append block-result paper-result)))
  (_log (strcat "detect:step3_done total=" (itoa (length block-result))))

  ;; 4. 合并块搜索结果
  (setq frames block-result)

  ;; 5. LINE矩形补充检测 — 仅在未找到块图框时执行
  ;;    ssget "_X" LINE 在 TCH ARX 环境下可能卡死，有块结果时跳过
  (if (> (length frames) 0)
    (_log "detect:step5_skip (blocks already found)")
    (progn
      (_log "detect:step5_start")
      (setq line-result (vl-catch-all-apply 'ap:detect-line-rectangles (list)))
      (if (vl-catch-all-error-p line-result)
        (princ (strcat "\n  [DEBUG] LINE检测错误: " (vl-catch-all-error-message line-result)))
        (progn
          (princ (strcat "\n  [DEBUG] LINE检测结果: " (itoa (length line-result)) " 个"))
          ;; 过滤：排除与块检测区域重叠的矩形
          (setq line-filtered nil)
          (foreach lr line-result
            (setq keep T)
            (setq lr-b (vl-catch-all-apply 'ap:frame-get (list lr "bounds")))
            (if (and block-result lr-b (not (vl-catch-all-error-p lr-b)))
              (foreach br block-result
                (setq br-b (vl-catch-all-apply 'ap:frame-get (list br "bounds")))
                (if (and br-b (not (vl-catch-all-error-p br-b)))
                  (if (>= (ap:overlap-ratio lr-b br-b) 0.5)
                    (setq keep nil)))))
            (if keep
              (setq line-filtered (cons lr line-filtered))))
          (if line-filtered
            (progn
              (princ (strcat "\n  LINE矩形: " (itoa (length line-filtered)) " 个"))
              (setq frames (append frames (reverse line-filtered)))))))))

  ;; 6. 去重、排序
  (if frames
    (progn
      (setq frames (ap:dedup-frames frames))
      (if frames
        (progn
          (setq frames (ap:safe-sort-frames frames))
          (if frames
            (princ (strcat "\n[AutoPlot] 共识别 " (itoa (length frames)) " 个有效图框"))
            (princ "\n[AutoPlot] 排序后为空。")))
        (princ "\n[AutoPlot] 去重后为空。")))
    (princ "\n[AutoPlot] 未找到任何图框。"))

  (setq *frame-list* frames)
  frames)

(princ "\n[AutoPlot] ap-detect.lsp 已加载。")
(princ)
