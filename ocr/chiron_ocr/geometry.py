"""Image geometry between the detector and the recogniser (numpy + OpenCV)."""

from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np

# A crop this much taller than wide is a vertical line of text (a sideways
# margin note); VietOCR reads left to right, so it is turned first.
VERTICAL_RATIO = 1.5

# Blank margin around each line crop, as a fraction of the line's height.
# Cropped flush to the ink, VietOCR tends to invent a word or a "TP." at the end
# of the line ("… ánh sáng thuận"); with this margin the same crops read clean.
PAD_Y = 0.15
PAD_X = 0.25

_UNDO = {
    90: cv2.ROTATE_90_COUNTERCLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_CLOCKWISE,
}


def upright(image: np.ndarray, angle: int) -> np.ndarray:
    """Undo the page rotation the orientation classifier reports.

    The classifier labels a page with how far it is turned clockwise
    (0/90/180/270) — a page photographed turned 90° counter-clockwise comes back
    as "270" — so turning it back is a counter-clockwise rotation by that much.
    """
    rotate = _UNDO.get(angle % 360)
    return image if rotate is None else cv2.rotate(image, rotate)


def crop_quad(image: np.ndarray, polygon: Sequence[Sequence[float]]) -> np.ndarray:
    """Cut a detected text line out of the page and straighten it.

    `polygon` is the detector's quadrilateral, clockwise from the top-left
    corner. A perspective warp (not an axis-aligned crop) keeps a slanted
    handwritten line from dragging in half of the lines above and below.
    """
    pts = np.asarray(polygon, dtype=np.float32).reshape(4, 2)
    width = int(round(max(np.linalg.norm(pts[0] - pts[1]), np.linalg.norm(pts[3] - pts[2]))))
    height = int(round(max(np.linalg.norm(pts[0] - pts[3]), np.linalg.norm(pts[1] - pts[2]))))
    width, height = max(width, 1), max(height, 1)
    target = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    matrix = cv2.getPerspectiveTransform(pts, target)
    crop = cv2.warpPerspective(
        image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    if height >= width * VERTICAL_RATIO:
        crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return pad(crop)


def pad(crop: np.ndarray) -> np.ndarray:
    """Surround a line crop with its own background colour.

    The median colour, not white: a photo of a notebook page is grey or
    yellowish, and a white frame would read as an edge.
    """
    height = crop.shape[0]
    y = int(round(height * PAD_Y)) + 2
    x = int(round(height * PAD_X)) + 2
    background = [int(v) for v in np.median(crop.reshape(-1, crop.shape[2]), axis=0)]
    return cv2.copyMakeBorder(crop, y, y, x, x, cv2.BORDER_CONSTANT, value=background)
