import math
import pytest
from photobook_workshop.layout import (
    photo_trees,
    bounded_trees,
    boxes,
    crop_transform,
    mosaic,
    justified_rows,
)


@pytest.mark.parametrize(
    "n,count", [(1, 1), (2, 2), (3, 12), (4, 120), (5, 1680), (6, 30240)]
)
def test_exact_slicing_tree_counts_and_membership(n, count):
    photos = [{"width": 300 + i * 20, "height": 400} for i in range(n)]
    trees = photo_trees(photos)
    assert len(trees) == count
    for _, tree in trees[:: max(1, len(trees) // 30)]:
        rects = boxes(tree, 0, 0, 900, 900, 2)
        assert sorted(r[0] for r in rects) == list(range(n))
        assert all(r[3] > 0 and r[4] > 0 for r in rects)


def test_large_mosaic_search_is_bounded_without_dropping_images():
    photos = [{"width": 300 + i * 10, "height": 400} for i in range(24)]
    trees = bounded_trees(photos)
    assert len(trees) <= 24
    for _, tree in trees:
        assert sorted(r[0] for r in boxes(tree, 0, 0, 3000, 3000, 1)) == list(range(24))
    with pytest.raises(ValueError):
        photo_trees(photos)


def test_crop_focus_and_ppi_are_top_left():
    source = {"width": 400, "height": 200}
    rect = [10, 20, 100, 100]
    left = crop_transform(source, rect, [0, 0])
    right = crop_transform(source, rect, [1, 1])
    assert left["draw"] == [10, 20, 200, 100]
    assert right["draw"] == [-90, 20, 200, 100]
    assert left["crop_fraction"] == 0.5 and left["source_ppi"] == 144
    portrait = crop_transform({"width": 200, "height": 400}, rect, [1, 1])
    assert portrait["draw"] == [10, -80, 100, 200]


def test_impossible_crop_limit_is_visible():
    with pytest.raises(ValueError, match="No mosaic"):
        mosaic([{"width": 400, "height": 100}], (0, 0, 100, 100), crop_limit=0.18)


def test_authored_rows_close_edges_and_measure_image_interior():
    photos = [
        {"id": str(i), "width": width, "height": 100}
        for i, width in enumerate([101, 149, 87])
    ]
    rects = justified_rows(
        photos, [{"height": 100, "images": ["0", "1", "2"]}], 500, 100, border=8
    )
    assert rects[0][1] == 8 and rects[-1][1] + rects[-1][3] == 492
    for left, right in zip(rects, rects[1:]):
        assert left[1] + left[3] + 16 == right[1]
    assert all(r[4] == 84 for r in rects)
    with pytest.raises(ValueError):
        justified_rows(photos, [{"height": 100, "images": ["0", "1"]}], 500, 100)
    with pytest.raises(ValueError):
        justified_rows(
            photos, [{"height": 100, "images": ["0", "1", "2"]}], 500, 100, border=60
        )


def test_rows_reject_overlap_and_repeated_placements():
    photos = [{"id": str(i), "width": 100, "height": 100} for i in range(2)]
    with pytest.raises(ValueError):
        justified_rows(
            photos,
            [
                {"height": 100, "images": ["0"]},
                {"height": 100, "y": 50, "images": ["1"]},
            ],
            100,
            200,
        )
    with pytest.raises(ValueError):
        justified_rows(photos, [{"height": 100, "images": ["0", "0"]}], 100, 100)
