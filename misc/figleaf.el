
;(require 'gnus-start)

; (defun gnus-load (file)
;   "Load FILE, but in such a way that read errors can be reported."
;   (with-temp-buffer
;     (insert-file-contents file)
;     (while (not (eobp))
;       (condition-case type
; 	  (let ((form (read (current-buffer))))
; 	    (eval form))
; 	(error
; 	 (unless (eq (car type) 'end-of-file)
; 	   (let ((error (format "Error in %s line %d" file
; 				(count-lines (point-min) (point)))))
; 	     (ding)
; 	     (unless (gnus-yes-or-no-p (concat error "; continue? "))
; 	       (error "%s" error)))))))))

(defvar figleaf-annotation-file ".figleaf.el")
(defvar figleaf-annotations nil)

(defun load-figleaf-annotations ()
  (let ((coverage
         (with-temp-buffer
           (insert-file-contents figleaf-annotation-file)
           (let ((form (read (current-buffer))))
             (eval form)))))
    (setq figleaf-annotations coverage)
    coverage
    ))

(defun figleaf-unannotate ()
  (interactive)
  (save-excursion
    (dolist (ov (overlays-in (point-min) (point-max)))
      (delete-overlay ov))
))

(defun figleaf-annotate (filename)
  (interactive)
  (let* ((allcoverage (load-figleaf-annotations))
         (thiscoverage (gethash filename allcoverage))
         (covered-lines (car thiscoverage))
         (code-lines (car (cdr thiscoverage)))
         )
    (save-excursion
      (dolist (ov (overlays-in (point-min) (point-max)))
        (delete-overlay ov))
      (dolist (covered-line covered-lines)
        (goto-line covered-line)
        ;;(add-text-properties (point) (line-end-position) '(face bold) )
        (overlay-put (make-overlay (point) (line-end-position))
                                        ;'before-string "C"
                                        ;'face '(background-color . "green")
                     'face '(:background "dark green")
                     )
        )
      (dolist (code-line code-lines)
        (goto-line code-line)
        (overlay-put (make-overlay (point) (line-end-position))
                                        ;'before-string "D"
                     ;'face '(:background "blue")
                     ;'face '(:underline "blue")
                     'face '(:box "blue")
                     )
        )
)))
