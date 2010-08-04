
(defvar coverage-annotation-file ".coverage.el")
(defvar coverage-annotations nil)

(defun find-coverage-annotation-file ()
  (let ((dir (file-name-directory buffer-file-name))
        (olddir "/"))
    (while (and (not (equal dir olddir))
                (not (file-regular-p (concat dir coverage-annotation-file))))
      (setq olddir dir
            dir (file-name-directory (directory-file-name dir))))
    (and (not (equal dir olddir)) (concat dir coverage-annotation-file))
))

(defun load-coverage-annotations ()
  (let* ((annotation-file (find-coverage-annotation-file))
         (coverage
          (with-temp-buffer
            (insert-file-contents annotation-file)
            (let ((form (read (current-buffer))))
              (eval form)))))
    (setq coverage-annotations coverage)
    coverage
    ))

(defun coverage-unannotate ()
  (save-excursion
    (dolist (ov (overlays-in (point-min) (point-max)))
      (delete-overlay ov))
    (setq coverage-this-buffer-is-annotated nil)
    (message "Removed annotations")
))

;; in emacs22, it will be possible to put the annotations in the fringe. Set
;; a display property for one of the characters in the line, using
;; (right-fringe BITMAP FACE), where BITMAP should probably be right-triangle
;; or so, and FACE should probably be '(:foreground "red"). We can also
;; create new bitmaps, with faces. To do tartans will require a lot of
;; bitmaps, and you've only got about 8 pixels to work with.

;; unfortunately emacs21 gives us less control over the fringe. We can use
;; overlays to put letters on the left or right margins (in the text area,
;; overriding actual program text), and to modify the text being displayed
;; (by changing its background color, or adding a box around each word).

(defun coverage-annotate (show-code)
  (let ((allcoverage (load-coverage-annotations))
        (filename-key (expand-file-name buffer-file-truename))
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
          (setq coverage-this-buffer-is-annotated t)
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
            (message (format "Added annotations: %d uncovered lines"
                             (safe-length uncovered-code-lines)))
            )
          )
      (message "unable to find coverage for this file"))
))

(defun coverage-toggle-annotations (show-code)
  (interactive "P")
  (if coverage-this-buffer-is-annotated
      (coverage-unannotate)
    (coverage-annotate show-code))
)


(setq coverage-this-buffer-is-annotated nil)
(make-variable-buffer-local 'coverage-this-buffer-is-annotated)

(define-minor-mode coverage-annotation-minor-mode
  "Minor mode to annotate code-coverage information"
  nil
  " CA"
  '(
    ("\C-c\C-a" . coverage-toggle-annotations)
    )

  () ; forms run on mode entry/exit
)

(defun maybe-enable-coverage-mode ()
  (if (string-match "/src/allmydata/" (buffer-file-name))
      (coverage-annotation-minor-mode t)
    ))

(add-hook 'python-mode-hook 'maybe-enable-coverage-mode)
