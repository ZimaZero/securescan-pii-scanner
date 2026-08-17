#!/usr/bin/env python3
"""Count frontal faces in a PIL image without identifying anyone.

This is face DETECTION (presence count), never face RECOGNITION. No face
regions, crops, landmarks, or embeddings are stored or reported.
"""

from __future__ import annotations

import importlib
import threading

_thread_local = threading.local()


def _get_cascade(cv2):
    """Return this thread's cached CascadeClassifier, building it once.

    Reused across scan_folder's worker threads (~8ms/image saved measured
    vs. reconstructing per call) — one cascade per thread, never shared
    across threads, so no locking is needed around the classifier itself.
    A cascade that failed to load (.empty()) is cached too: reloading the
    same file would fail identically every time, so caching it just skips
    the repeat attempt rather than changing the outcome.
    """
    cascade = getattr(_thread_local, "cascade", None)
    if cascade is None:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        _thread_local.cascade = cascade
    return cascade


def detect_faces(pil_image) -> int:
    """Return the number of frontal faces, or -1 when detection is unavailable.

    The Haar parameters are fixed to the 19-image selection benchmark:
    9/9 face-bearing files hit and 0 false positives on 10 no-face documents.
    Any dependency, conversion, or cascade failure is advisory-only and must
    never be able to fail the caller's scan.
    """
    try:
        cv2 = importlib.import_module("cv2")
        np = importlib.import_module("numpy")
        cascade = _get_cascade(cv2)
        if cascade.empty():
            return -1

        rgb = np.asarray(pil_image.convert("RGB"))
        grayscale = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        faces = cascade.detectMultiScale(
            grayscale,
            scaleFactor=1.1,
            minNeighbors=5,
        )
        return len(faces)
    except Exception:
        return -1
