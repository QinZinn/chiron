"""Line grouping: the step that decides whether OCR text reads in order."""

from chiron_ocr.layout import LOW_CONFIDENCE, Box, box_from_polygon, group_lines, page_summary


def box(text, x, y, w=40, h=20, score=0.95):
    return Box(text, score, x, y, x + w, y + h)


def test_boxes_on_one_visual_line_are_joined_left_to_right():
    # Arrive right-to-left, as detection order is not guaranteed.
    lines = group_lines([box("B·S·cosα", 200, 102), box("Φ =", 100, 100)])
    assert [l.text for l in lines] == ["Φ = B·S·cosα"]


def test_lines_are_ordered_top_to_bottom():
    lines = group_lines([box("line two", 10, 60), box("line one", 10, 10)])
    assert [l.text for l in lines] == ["line one", "line two"]


def test_a_big_heading_does_not_swallow_the_lines_under_it():
    # Median height, not mean: one tall box must not widen the tolerance
    # enough to merge every following line into it.
    heading = box("ELECTROMAGNETIC INDUCTION", 10, 0, w=300, h=80)
    body = [box(f"point {i}", 10, 100 + i * 30) for i in range(4)]
    lines = group_lines([heading, *body])
    assert len(lines) == 5


def test_a_line_is_as_confident_as_its_weakest_box():
    lines = group_lines([box("sharp", 10, 10, score=0.99), box("faint", 60, 10, score=0.41)])
    assert lines[0].confidence == 0.41


def test_empty_boxes_are_ignored():
    assert group_lines([box("   ", 10, 10)]) == []


def test_rotated_polygons_become_their_bounds():
    b = box_from_polygon("x", 0.9, [[10, 5], [50, 8], [48, 30], [8, 27]])
    assert (b.x_min, b.y_min, b.x_max, b.y_max) == (8, 5, 50, 30)


def test_page_summary_flags_but_keeps_low_confidence_lines():
    lines = group_lines([box("clear", 10, 10, score=0.97), box("smudged", 10, 60, score=LOW_CONFIDENCE - 0.1)])
    summary = page_summary(lines)
    assert summary["text"] == "clear\nsmudged", "low-confidence text is flagged, never dropped"
    assert summary["low_confidence_count"] == 1


def test_an_empty_page_has_no_confidence_rather_than_zero():
    assert page_summary([])["mean_confidence"] is None
