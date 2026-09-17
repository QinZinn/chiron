import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from chiron_ocr.geometry import PAD_X, PAD_Y, crop_quad, pad, upright


def page_with_mark() -> np.ndarray:
    """20x40 black page with one white pixel near the top-left corner."""
    img = np.zeros((20, 40, 3), dtype=np.uint8)
    img[1, 2] = 255
    return img


def test_upright_zero_is_identity():
    img = page_with_mark()
    assert upright(img, 0) is img


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_upright_undoes_a_clockwise_turn(angle):
    img = page_with_mark()
    turned = np.rot90(img, k=-(angle // 90))  # negative k: clockwise
    assert np.array_equal(upright(turned, angle), img)


def margins(height: int) -> tuple[int, int]:
    return int(round(height * PAD_Y)) + 2, int(round(height * PAD_X)) + 2


def test_crop_quad_axis_aligned_box():
    img = np.arange(30 * 50 * 3, dtype=np.uint8).reshape(30, 50, 3)
    crop = crop_quad(img, [[10, 5], [40, 5], [40, 20], [10, 20]])
    y, x = margins(15)
    assert crop.shape == (15 + 2 * y, 30 + 2 * x, 3)
    assert np.array_equal(crop[y, x], img[5, 10])


def test_pad_uses_the_crop_background_colour():
    crop = np.full((20, 60, 3), 200, dtype=np.uint8)
    crop[8:12, 10:50] = 0  # a stroke of ink
    padded = pad(crop)
    assert tuple(padded[0, 0]) == (200, 200, 200)


def test_crop_quad_straightens_a_slanted_line():
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    quad = [[20, 40], [180, 20], [182, 40], [22, 60]]
    crop = crop_quad(img, quad)
    height, width = crop.shape[:2]
    _, x = margins(20)  # the straightened line is 20 px tall
    assert width > height
    assert 155 <= width - 2 * x <= 165


def test_crop_quad_turns_vertical_text_sideways():
    img = np.zeros((200, 100, 3), dtype=np.uint8)
    crop = crop_quad(img, [[40, 10], [60, 10], [60, 190], [40, 190]])
    y, x = margins(20)
    assert crop.shape[:2] == (20 + 2 * y, 180 + 2 * x)


def test_crop_quad_degenerate_polygon_does_not_crash():
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    assert crop_quad(img, [[5, 5], [5, 5], [5, 5], [5, 5]]).size > 0
