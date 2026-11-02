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


UNGUARDED_TFREE = ";Drop the bed\nM561\nG90\nG53 G1 Z{global.SafeZ} F1200\n;Move In\nG53 G1 X212.5 Y100 F50000\n"
GUARDED_TFREE = UNGUARDED_TFREE.replace("G53 G1 Z{global.SafeZ} F1200",
                                        duet.PARK_GUARD + "\n  G53 G1 Z{global.SafeZ} F1200")


def test_park_check_refuses_a_tall_part_with_the_unguarded_macro(fake):
    fake.files["0:/sys/tfree2.g"] = UNGUARDED_TFREE.encode()
    d = client(fake)
    with pytest.raises(SystemExit, match="179.2 mm tall .* SafeZ 65.0 mm"):
        agent.park_check(d, 2, 179.15)
    assert "echo global.SafeZ" in fake.gcode_log


def test_park_check_passes_a_short_part_or_a_guarded_macro(fake):
    fake.files["0:/sys/tfree2.g"] = UNGUARDED_TFREE.encode()
    d = client(fake)
    r = agent.park_check(d, 2, 30.0)
    assert r == {"max_z": 30.0, "safe_z": 65.0, "park_guarded": False, "tall": False}
    assert agent.park_check(d, 2, 59.0)["tall"] is False
    fake.files["0:/sys/tfree2.g"] = GUARDED_TFREE.encode()
    assert agent.park_check(d, 2, 179.15) == {"max_z": 179.15, "safe_z": 65.0, "park_guarded": True, "tall": True}


def test_park_check_refuses_a_tall_part_when_the_macro_cannot_be_read(fake):
    d = client(fake)  # no sys/tfree2.g on the fake
    with pytest.raises(SystemExit, match="could not be checked"):
        agent.park_check(d, 2, 100.0)
    assert agent.park_check(d, 2, 30.0)["park_guarded"] is None


def test_end_gcode_turns_the_heater_off_before_parking():
    import toolchanger
    raw = "G10 S220 P0" + chr(10) + "M116" + chr(10) + "T2" + chr(10) + "G1 X1 Y1 E1" + chr(10) + "M104 S0" + chr(10) + "G28 X0"
    lines = [ln.split(";")[0].strip() for ln in toolchanger.prepare(raw, 2).splitlines()]
    assert lines.index("G10 P2 S0 R0") < lines.index("T-1", 1)
    assert toolchanger.check(toolchanger.prepare(raw, 2), 2)[0] == []


def test_at_night_is_eleven_to_eight():
    def at(h):
        return agent.at_night(time.struct_time((2026, 11, 2, h, 30, 0, 0, 306, 0)))
    assert at(23) and at(0) and at(7) and not at(8) and not at(22) and not at(12)


def _run_print(fake, sample, monkeypatch, tmp_path):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(duet_usb, "find_port", lambda: None)
    def finished(d, poll=3.0):  # the job ends while we "watch"
        fake.job_file, fake.status = None, "idle"
        return "idle"
    monkeypatch.setattr(agent, "watch", finished)
    monkeypatch.setattr(agent, "bed_check", lambda job, skip=False: {"clear": True})
    return agent.print_job(client(fake), sample, 2, "auto", "size", True, None, 0, job=str(tmp_path))


def test_a_print_that_fails_at_night_gets_the_heaters_turned_off(fake, sample, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "at_night", lambda now=None: True)
    fake.aborted = True
    assert _run_print(fake, sample, monkeypatch, tmp_path) == 1
    after = fake.gcode_log[fake.gcode_log.index(next(g for g in fake.gcode_log if g.startswith("M32"))):]
    assert "G10 P2 S0 R0" in after and "M140 S0" in after


def test_a_print_that_fails_by_day_keeps_its_heat(fake, sample, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "at_night", lambda now=None: False)
    fake.aborted = True
    assert _run_print(fake, sample, monkeypatch, tmp_path) == 1
    assert "M140 S0" not in fake.gcode_log


def test_a_completed_print_sends_no_extra_heat_off(fake, sample, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "at_night", lambda now=None: True)
    assert _run_print(fake, sample, monkeypatch, tmp_path) == 0
    assert "M140 S0" not in fake.gcode_log


def test_start_gcode_wipes_the_brush_after_the_tool_is_hot():
    import prusa
    import toolchanger
    raw = "G10 S220 P0" + chr(10) + "M116" + chr(10) + "T2" + chr(10) + "G1 X1 Y1 E1" + chr(10) + "M104 S0"
    lines = [ln.split(";")[0].strip() for ln in toolchanger.prepare(raw, 2).splitlines()]
    i = lines.index("M116 P2")
    assert lines[i + 1] == "G1 X300 Y167.4 F10000"
    assert lines[i + 2:i + 10].count("G1 X341.5 F5000") == 4 and lines[i + 10] == "G1 X280 F10000"
    assert toolchanger.check(toolchanger.prepare(raw, 2), 2)[0] == []
    start = prusa.toolchanger_gcode(2)["start_gcode"]
    assert start.index("M116 P2") < start.index("G1 X341.5") < start.index("G1 Z5 F5000")
