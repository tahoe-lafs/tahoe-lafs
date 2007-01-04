
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

(defun find-figleaf-annotation-file ()
  (let ((dir (file-name-directory buffer-file-name))
        (olddir "/"))
    (while (and (not (equal dir olddir))
                (not (file-regular-p (concat dir figleaf-annotation-file))))
      (setq olddir dir
            dir (file-name-directory (directory-file-name dir))))
    (and (not (equal dir olddir)) (concat dir figleaf-annotation-file))
))

(defun load-figleaf-annotations ()
  (let* ((annotation-file (find-figleaf-annotation-file))
         (coverage
          (with-temp-buffer
            (insert-file-contents annotation-file)
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
    (setq figleaf-this-buffer-is-annotated nil)
))

(defun figleaf-annotate (&optional show-code)
  (interactive "P")
  (let ((allcoverage (load-figleaf-annotations))
        (filename-key buffer-file-name)
        thiscoverage code-lines covered-lines uncovered-code-lines
        )
    (while (and (not (gethash filename-key allcoverage nil))
                (string-match "/" filename-key))
      ;; eat everything up to and including the first slash, then look again
      (setq filename-key (substring filename-key
                                    (+ 1 (string-match "/" filename-key)))))
    (setq thiscoverage (gethash filename-key allcoverage nil))
    (if thiscoverage
        (progn
          (setq figleaf-this-buffer-is-annotated t)
          (setq code-lines (nth 0 thiscoverage)
                covered-lines (nth 1 thiscoverage)
                uncovered-code-lines (nth 2 thiscoverage)
                )

          (save-excursion
            (dolist (ov (overlays-in (point-min) (point-max)))
              (delete-overlay ov))
            (if show-code
                (dolist (line code-lines)
                  (goto-line line)
                  ;;(add-text-properties (point) (line-end-position) '(face bold) )
                  (overlay-put (make-overlay (point) (line-end-position))
                                        ;'before-string "C"
                                        ;'face '(background-color . "green")
                               'face '(:background "dark green")
                               )
                  ))
            (dolist (line uncovered-code-lines)
              (goto-line line)
              (overlay-put (make-overlay (point) (line-end-position))
                                        ;'before-string "D"
                                        ;'face '(:background "blue")
                                        ;'face '(:underline "blue")
                           'face '(:box "red")
                           )
              )
            )
          )
      (message "unable to find coverage for this file"))
))

(defun figleaf-toggle-annotations (show-code)
  (interactive "P")
  (if figleaf-this-buffer-is-annotated
      (figleaf-unannotate)
    (figleaf-annotate show-code))
)


(setq figleaf-this-buffer-is-annotated nil)
(make-variable-buffer-local 'figleaf-this-buffer-is-annotated)

(define-minor-mode figleaf-annotation-minor-mode
  "Minor mode to annotate code-coverage information"
  nil
  " FA"
  '(
    ("\C-ca" . figleaf-toggle-annotations)
    )

  () ; forms run on mode entry/exit
)

