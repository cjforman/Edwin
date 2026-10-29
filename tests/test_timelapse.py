"""Unit tests for the timelapse's layer detection and frame naming (no camera, no printer)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
timelapse = pytest.importorskip("timelapse")  # needs cv2


def test_layer_counter_wins_when_the_duet_reports_one():
    assert timelapse.layer_changed(1, None, 0.35, None)
    assert timelapse.layer_changed(2, 1, 0.55, 0.35)
    assert not timelapse.layer_changed(2, 2, 0.75, 0.55)  # Z moved but the counter did not


def test_z_change_counts_as_a_layer_without_a_counter():
    assert timelapse.layer_changed(None, None, 5.0, None)  # first observation
    assert timelapse.layer_changed(None, None, 0.35, 5.0)  # down to the first layer
    assert timelapse.layer_changed(None, None, 0.55, 0.35)
    assert not timelapse.layer_changed(None, None, 0.55, 0.55)
    assert not timelapse.layer_changed(None, None, 0.551, 0.55)  # jitter
    assert not timelapse.layer_changed(None, None, None, 0.55)


def test_frame_names_sort_in_sequence():
    names = [timelapse.frame_name(1, None, 5.0), timelapse.frame_name(2, None, 0.35),
             timelapse.frame_name(3, None, 10.15), timelapse.frame_name(4, None, 10.35, done=True)]
    assert names == ["layer_0001_z005.00.jpg", "layer_0002_z000.35.jpg", "layer_0003_z010.15.jpg",
                     "layer_9999_final.jpg"]
    assert sorted(names) == names
    assert timelapse.frame_name(7, 7, 1.55) == "layer_0007_L007.jpg"


def test_grab_retries_once():
    calls = []

    class Cam:
        def grab(self):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError("frame never arrived")
            return "f", "img"

    assert timelapse.grab_with_retry(Cam()) == ("f", "img")
    assert len(calls) == 2

    class Dead:
        def grab(self):
            raise TimeoutError("still nothing")

    with pytest.raises(TimeoutError):
        timelapse.grab_with_retry(Dead())
