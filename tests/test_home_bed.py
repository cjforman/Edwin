"""Homing before a print and the bed check (fake Duet; the camera is stubbed)."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agent  # noqa: E402
import duet  # noqa: E402
import duet_usb  # noqa: E402
from test_printagent import client, fake, sample  # noqa: E402,F401


def test_home_sends_g28_and_waits_for_homed(fake, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    fake.homed = False
    d = client(fake)
    assert d.home() == {"X": True, "Y": True, "Z": True}
    assert "G28" in fake.gcode_log


def test_home_refuses_with_a_tool_on_the_carriage(fake):
    fake.homed = False
    fake.current_tool = 2
    d = client(fake)
    with pytest.raises(duet.DuetError, match="T2 is on the carriage"):
        d.home()
    assert "G28" not in fake.gcode_log


def test_print_job_homes_then_checks_the_bed_then_starts(fake, sample, monkeypatch, tmp_path):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(duet_usb, "find_port", lambda: None)
    monkeypatch.setattr(agent, "watch", lambda d, poll=3.0: "idle")
    order = []
    monkeypatch.setattr(agent, "bed_check", lambda job, skip=False: order.append("bed") or {"clear": True})
    fake.homed = False
    d = client(fake)
    agent.print_job(d, sample, 2, "auto", "size", True, None, 0, job=str(tmp_path))
    g28 = next(i for i, g in enumerate(fake.gcode_log) if g == "G28")
    m32 = next(i for i, g in enumerate(fake.gcode_log) if g.startswith("M32"))
    assert g28 < m32 and order == ["bed"]


def test_upload_without_start_does_not_home(fake, sample, monkeypatch, tmp_path):
    monkeypatch.setattr(duet_usb, "find_port", lambda: None)
    fake.homed = False
    d = client(fake)
    with pytest.raises(SystemExit, match="not homed"):
        agent.print_job(d, sample, 2, "auto", "size", False, None, 0, job=str(tmp_path))
    assert "G28" not in fake.gcode_log


def test_bed_check_stops_the_print_when_something_is_on_the_bed(monkeypatch, tmp_path):
    bed = pytest.importorskip("bed")
    monkeypatch.setattr(bed, "snapshot", lambda out: out)
    monkeypatch.setattr(bed, "check", lambda p: {"score": 0.31, "threshold": 0.08, "clear": False})
    with pytest.raises(SystemExit, match="bed not clear"):
        agent.bed_check(str(tmp_path))
    monkeypatch.setattr(bed, "check", lambda p: {"score": 0.02, "threshold": 0.08, "clear": True})
    assert agent.bed_check(str(tmp_path))["clear"]
    assert agent.bed_check(str(tmp_path), skip=True) is None


def test_bed_check_continues_without_a_reference_or_camera(monkeypatch, tmp_path):
    bed = pytest.importorskip("bed")
    monkeypatch.setattr(bed, "snapshot", lambda out: out)
    monkeypatch.setattr(bed, "check", lambda p: (_ for _ in ()).throw(FileNotFoundError("no reference")))
    assert agent.bed_check(str(tmp_path)) is None
    monkeypatch.setattr(bed, "snapshot", lambda out: (_ for _ in ()).throw(TimeoutError("no reply to ping")))
    assert agent.bed_check(str(tmp_path)) is None


def test_bed_score_sees_an_object_but_not_a_change_of_light():
    bed = pytest.importorskip("bed")
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(1)
    empty = (90 + 20 * rng.random((1200, 1600, 3))).astype(np.uint8)
    empty[:500] = 40  # the back of the printer, darker
    darker = (empty * 0.5).astype(np.uint8)  # same scene, half the light
    with_cube = empty.copy()
    with_cube[700:950, 650:900] = 200  # a bright block on the bed
    assert bed.score(darker, empty) < 0.01
    assert bed.score(with_cube, empty) > bed.THRESHOLD
