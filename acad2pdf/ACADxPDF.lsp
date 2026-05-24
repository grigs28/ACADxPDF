;;;=====================================================================
;;; ACADxPDF - DWG to PDF Batch Converter (Model Space Multi-Border)
;;;=====================================================================
;;; Features:
;;; - Border detection: Block name matching (keywords) first, then closed rectangles.
;;; - Each border exported as separate PDF using Window mode.
;;; - Paper size auto-matching (A0~A4) with elongated sizes support (custom user size).
;;; - Batch process all DWG files in a selected directory.
;;; - Output statistics: total time, PDF count, average time per file.
;;; - Plot style retrieved from system (default monochrome.ctb).
;;;=====================================================================

(vl-load-com)

;;-----------------------------
;; User configurable variables
;;-----------------------------
(defun *default-plot-style* ()
  (or (getenv "DefaultPlotStyle")
      "monochrome.ctb"))

(defun *border-keywords* ()
  '("TK" "TUKUANG" "BORDER" "FRAME" "TITLE" "图框" "边框"))

(defun *printer-name* ()
  "DWG To PDF.pc3")

(defun *paper-tolerance-mm* ()
  5.0)   ; tolerance for standard size matching

(defun *standard-sizes* ()
  '(
    ("A0" 841.0 1189.0)
    ("A1" 594.0 841.0)
    ("A2" 420.0 594.0)
    ("A3" 297.0 420.0)
    ("A4" 210.0 297.0)
   ))

;;-----------------------------
;; Helper: get orientation
;;-----------------------------
(defun get-orientation (w h)
  (if (>= w h) "L" "P"))

;;-----------------------------
;; Helper: generate standard paper name for DWG To PDF.pc3
;; Input: standard short name, width(mm), height(mm)
;; Output: string like "ISO full bleed A1 (841.00 x 594.00 毫米)"
;;-----------------------------
(defun format-paper-name (name w h)
  (strcat "ISO full bleed " name
          " (" (rtos w 2 2) " x " (rtos h 2 2) " 毫米)"))

;;-----------------------------
;; Match paper size from width/height (mm)
;; Returns: (paper_name_or_User w h orientation) 
;;          If custom, paper_name_or_User = "User", w/h are user dimensions.
;;          orientation = "L" or "P"
;;-----------------------------
(defun match-paper-size (w h)
  (setq tol *paper-tolerance-mm*)
  (setq short-side (min w h)
        long-side  (max w h)
        orient (if (>= w h) "L" "P"))
  ;; first try standard sizes (short side match)
  (setq best-std nil
        best-short-diff nil)
  (foreach std (*standard-sizes*)
    (setq std-name (car std)
          std-w   (cadr std)
          std-h   (caddr std)
          std-short (min std-w std-h)
          std-long  (max std-w std-h))
    (if (<= (abs (- short-side std-short)) tol)
      (progn
        (setq long-diff (abs (- long-side std-long)))
        (if (or (null best-short-diff) (< long-diff (abs best-short-diff)))
          (setq best-std std
                best-short-diff long-diff))))
  )
  (if best-std
    (progn
      (setq std-name (car best-std)
            std-w    (cadr best-std)
            std-h    (caddr best-std))
      ;; check if elongated
      (setq std-short (min std-w std-h)
            std-long  (max std-w std-h))
      (if (<= (abs (- long-side std-long)) tol)
        ;; exact standard
        (list (format-paper-name std-name std-w std-h) w h orient)
        ;; elongated: use custom user size with actual border dimensions
        (list "User" w h orient)
      )
    )
    ;; no standard short side matched -> custom user size
    (list "User" w h orient)
  ))

;;-----------------------------
;; Get bounding box of a block reference (WCS)
;; Returns: (xmin ymin xmax ymax) or nil
;;-----------------------------
(defun get-block-bounding-box (blk)
  (if (and (vlax-property-available-p blk 'InsertionPoint)
           (vlax-property-available-p blk 'Rotation))
    (progn
      (setq obj (vlax-ename->vla-object blk))
      (if (not (vl-catch-all-error-p (vl-catch-all-apply 'vla-GetBoundingBox (list obj 'minpt 'maxpt))))
        (progn
          (setq minpt (vlax-safearray->list minpt)
                maxpt (vlax-safearray->list maxpt))
          (list (car minpt) (cadr minpt) (car maxpt) (cadr maxpt)))
        nil))
    nil))

;;-----------------------------
;; Check if entity is a closed rectangle (LWPOLYLINE)
;; Returns: (xmin ymin xmax ymax) or nil
;;-----------------------------
(defun rect-from-lwpolyline (ent)
  (setq obj (vlax-ename->vla-object ent))
  (if (and (= (vla-get-Closed obj) :vlax-true)
           (= (vla-get-ObjectName obj) "AcDbPolyline"))
    (progn
      (setq pts-list '())
      (setq coords (vlax-get obj 'Coordinates))
      (setq i 0)
      (repeat (/ (length coords) 2)
        (setq x (nth i coords)
              y (nth (1+ i) coords))
        (setq pts-list (cons (list x y) pts-list))
        (setq i (+ i 2)))
      (setq pts-list (reverse pts-list))
      (if (= (length pts-list) 4)
        (progn
          (setq xs (mapcar 'car pts-list)
                ys (mapcar 'cadr pts-list))
          (list (apply 'min xs) (apply 'min ys)
                (apply 'max xs) (apply 'max ys)))
        nil))
    nil))

;;-----------------------------
;; Find all closed rectangles in ModelSpace (LWPOLYLINE 4 vertices)
;; Returns: list of (xmin ymin xmax ymax)
;;-----------------------------
(defun find-modelspace-rectangles ()
  (setq ss (ssget "_X" '((0 . "LWPOLYLINE") (410 . "Model"))))
  (setq rects nil)
  (if ss
    (repeat (setq i (sslength ss))
      (setq e (ssname ss (setq i (1- i))))
      (setq box (rect-from-lwpolyline e))
      (if box (setq rects (cons box rects)))))
  rects)

;;-----------------------------
;; Find blocks matching keywords in ModelSpace
;; Returns: list of (xmin ymin xmax ymax block_name)
;;-----------------------------
(defun find-matching-blocks ()
  (setq kw (*border-keywords*))
  ;; build regex-like filter: insert block name containing any keyword
  (setq filter-list '())
  (foreach k kw
    (setq filter-list (cons (cons 2 (strcat "*" k "*")) filter-list)))
  (setq ss (ssget "_X" (append '((0 . "INSERT") (410 . "Model")) filter-list)))
  (setq rects nil)
  (if ss
    (repeat (setq i (sslength ss))
      (setq e (ssname ss (setq i (1- i))))
      (setq box (get-block-bounding-box e))
      (if box
        (setq rects (cons (append box (list (cdr (assoc 2 (entget e))))) rects)))))
  rects)

;;-----------------------------
;; Merge overlapping/contained rectangles: keep outermost only
;; Input: list of (xmin ymin xmax ymax ...)
;; Output: filtered list
;;-----------------------------
(defun filter-outermost-rectangles (rects)
  (setq result '())
  (foreach r rects
    (setq x0 (car r) y0 (cadr r) x1 (caddr r) y1 (cadddr r))
    (setq contained nil)
    (foreach r2 rects
      (if (not (equal r r2))
        (progn
          (setq x20 (car r2) y20 (cadr r2) x21 (caddr r2) y21 (cadddr r2))
          (if (and (>= x0 x20) (>= y0 y20) (<= x1 x21) (<= y1 y21))
            (setq contained t)))))
    (if (not contained)
      (setq result (cons r result))))
  result)

;;-----------------------------
;; Get all borders in current drawing (ModelSpace)
;; Returns: list of ((xmin ymin xmax ymax) name)
;;-----------------------------
(defun get-all-borders (/ borders blocks rects all)
  ;; priority: block matching first
  (setq blocks (find-matching-blocks))
  (if blocks
    (setq borders blocks)
    (setq borders (find-modelspace-rectangles)))
  (if (null borders) (setq borders '()))
  ;; filter outermost
  (setq all (filter-outermost-rectangles borders))
  ;; convert to list format
  (mapcar '(lambda (r) (list (list (car r) (cadr r) (caddr r) (cadddr r))
                      (if (cadddr r) (cadddr r) "RECT")))
          all))

;;-----------------------------
;; Plot a single border to PDF
;; dwg: drawing full path (used for output name)
;; border: (xmin ymin xmax ymax)
;; outdir: directory for pdf files
;; index: border index (for naming)
;; plot-style: ctb file name
;; Returns: pdf file path created or nil
;;-----------------------------
(defun plot-border (dwg border outdir idx plot-style / x0 y0 x1 y1 w-mm h-mm paper-info printer orient plot-cmd pdf-fname pdf-path)
  (setq x0 (car border)
        y0 (cadr border)
        x1 (caddr border)
        y1 (cadddr border))
  ;; calculate width & height in mm (assuming drawing units = mm)
  (setq w-mm (- x1 x0)
        h-mm (- y1 y0))
  ;; match paper size
  (setq paper-info (match-paper-size w-mm h-mm))
  (setq paper-name (car paper-info)
        paper-w (cadr paper-info)
        paper-h (caddr paper-info)
        orient (cadddr paper-info))
  (setq printer (*printer-name*))
  ;; build plot command list
  (setq plot-cmd
    (list "_.-PLOT"
          "Y"                      ; detailed config
          ""                       ; use current layout/Model
          printer
          )
    )
  ;; handle paper size: either standard name or "User" with dimensions
  (if (= paper-name "User")
    (setq plot-cmd (append plot-cmd (list "User" (rtos paper-w 2 2) (rtos paper-h 2 2)
                                          (if (= (getvar "MEASUREMENT") 1) "MM" "Inches"))))
    (setq plot-cmd (append plot-cmd (list paper-name)))
  )
  ;; generate pdf file name (remove invalid filename characters)
  (setq safe-base (vl-string-translate "\\/*?<>|" "______" (vl-filename-base dwg)))
  (setq pdf-fname (strcat outdir "\\" safe-base "_" (itoa idx) ".pdf"))
  (setq plot-cmd (append plot-cmd
    (list
      "M"                          ; mm units
      orient                       ; orientation L/P
      "N"                          ; no reverse
      "W"                          ; window
      (strcat (rtos x0 2 8) "," (rtos y0 2 8))
      (strcat (rtos x1 2 8) "," (rtos y1 2 8))
      "F"                          ; fit to paper
      "C"                          ; center
      "Y"                          ; use plot style
      plot-style
      "N"                          ; no lineweights
      ""                           ; shaded viewport options
      pdf-fname
      "N"                          ; don't save page setup
      "Y"                          ; plot
    )))
  ;; execute plot command
  (apply 'command plot-cmd)
  ;; check if pdf created
  (if (findfile pdf-fname) pdf-fname nil))

;;-----------------------------
;; Process a single DWG file
;; Returns: (success pdf-count total-time-seconds)
;;-----------------------------
(defun process-dwg (dwg-file outdir plot-style / start-time doc borders count pdf path old-cmddia old-filedia)
  (setq start-time (getvar "MILLISECS"))
  (princ (strcat "\nProcessing: " dwg-file))
  (setq pdf-count 0)
  (if (findfile dwg-file)
    (progn
      ;; save original settings
      (setq old-cmddia (getvar "CMDDIA"))
      (setq old-filedia (getvar "FILEDIA"))
      (setvar "CMDDIA" 0)
      (setvar "FILEDIA" 0)
      (setvar "EXPERT" 5)
      (setvar "PROXYNOTICE" 0)

      (setq doc (vla-open (vla-get-documents (vlax-get-acad-object)) dwg-file))
      (vla-activate doc)   ; ensure document is active
      ;; ensure we are in ModelSpace
      (vla-put-activespace (vla-get-activedocument (vlax-get-acad-object)) acModelSpace)
      
      (setq borders (get-all-borders))
      (if (null borders)
        (progn
          (princ "\nNo borders detected, plotting entire drawing as single PDF.")
          (setq safe-base (vl-string-translate "\\/*?<>|" "______" (vl-filename-base dwg-file)))
          (setq pdf-fname (strcat outdir "\\" safe-base "_full.pdf"))
          (command "_.-PLOT"
                   "Y" "" (*printer-name*)
                   "ISO full bleed A1 (841.00 x 594.00 毫米)"
                   "M" "L" "N"
                   "E"   ; extents
                   "F" "C" "Y" plot-style "N" "" pdf-fname "N" "Y")
          (if (findfile pdf-fname) (setq pdf-count 1))
        )
        (progn
          (setq idx 1)
          (foreach b borders
            (setq border (car b))
            (if (setq pdf (plot-border dwg-file border outdir idx plot-style))
              (setq pdf-count (1+ pdf-count)))
            (setq idx (1+ idx))))
      )
      (vla-close doc :vlax-false)
      ;; restore settings
      (setvar "CMDDIA" old-cmddia)
      (setvar "FILEDIA" old-filedia)
      (setvar "EXPERT" 0)
      (setvar "PROXYNOTICE" 1)
      (list pdf-count (/ (- (getvar "MILLISECS") start-time) 1000.0))
    )
    (progn
      (princ (strcat "\nFile not found: " dwg-file))
      (list 0 0.0)
    )
  ))

;;-----------------------------
;; Batch process: select directory, then convert all DWG
;;-----------------------------
(defun c:ACADxPDF ( / outdir plot-style dir dwg-list total-files total-pdfs total-time pdf-count elapsed result full-path)
  (prompt "\n=== ACADxPDF - DWG to PDF Batch Converter ===\n")
  ;; get output directory
  (setq outdir (getstring 1 "\nOutput directory (absolute path): "))
  (if (or (null outdir) (= outdir ""))
    (setq outdir (getvar "DWGPREFIX"))
  )
  (if (not (findfile outdir))
    (progn
      (prompt "\nDirectory does not exist. Creating now...")
      (vl-mkdir outdir)
    )
  )
  ;; plot style
  (setq plot-style (getstring 1 (strcat "\nPlot style (default: " (*default-plot-style*) "): ")))
  (if (= plot-style "")
    (setq plot-style (*default-plot-style*))
  )
  ;; input directory
  (setq dir (getstring 1 "\nInput directory containing DWG files: "))
  (if (and dir (findfile dir))
    (progn
      (setq dwg-list (vl-directory-files dir "*.dwg" 1))
      (setq total-files (length dwg-list))
      (if (= total-files 0)
        (prompt "\nNo DWG files found.\n")
        (progn
          (setq total-pdfs 0)
          (setq total-time 0.0)
          (prompt (strcat "\nFound " (itoa total-files) " DWG file(s).\n"))
          (foreach dwg dwg-list
            (setq full-path (strcat dir "\\" dwg))
            (setq result (process-dwg full-path outdir plot-style))
            (setq pdf-count (car result))
            (setq elapsed (cadr result))
            (setq total-pdfs (+ total-pdfs pdf-count))
            (setq total-time (+ total-time elapsed))
            (princ (strcat "\n -> " (itoa pdf-count) " PDF(s) in " (rtos elapsed 2 1) "s\n"))
          )
          (prompt "\n================== SUMMARY ==================\n")
          (prompt (strcat "Total DWG files: " (itoa total-files) "\n"))
          (prompt (strcat "Total PDF created: " (itoa total-pdfs) "\n"))
          (prompt (strcat "Total time: " (rtos total-time 2 1) " seconds\n"))
          (if (> total-files 0)
            (prompt (strcat "Average per DWG: " (rtos (/ total-time total-files) 2 1) " seconds\n")))
          (prompt "==============================================\n")
        )
      )
    )
    (prompt "\nInvalid directory.\n")
  )
  (princ)
)

;;-----------------------------
;; Optional: command to process a single DWG file
;;-----------------------------
(defun c:ACADxPDFSingle ( / dwg outdir plot-style result pdf-count elapsed default-out)
  (setq dwg (getfiled "Select DWG file" "" "dwg" 0))
  (if dwg
    (progn
      (setq default-out (strcat (vl-filename-directory dwg) "\\PDF"))
      (setq outdir (getstring 1 (strcat "\nOutput directory <" default-out ">: ")))
      (if (= outdir "") (setq outdir default-out))
      (if (not (findfile outdir)) (vl-mkdir outdir))
      (setq plot-style (getstring 1 (strcat "\nPlot style <" (*default-plot-style*) ">: ")))
      (if (= plot-style "") (setq plot-style (*default-plot-style*)))
      (setq result (process-dwg dwg outdir plot-style))
      (setq pdf-count (car result))
      (setq elapsed (cadr result))
      (if (> pdf-count 0)
        (alert (strcat "Done! Created " (itoa pdf-count) " PDF(s) in " (rtos elapsed 2 1) " s"))
        (alert "No PDF generated. Check borders or drawing."))
    )
  )
  (princ)
)

(princ "\nACADxPDF loaded. Commands: ACADxPDF (batch), ACADxPDFSingle (single file).\n")
(princ)
