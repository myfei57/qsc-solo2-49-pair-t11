"""Snapshot recovery on start-up."""

from __future__ import annotations

from pathlib import Path

from linecontrol.domain.records import (
    GainVersionLog,
    PumpSnapshot,
    RinseSnapshot,
    TensionSnapshot,
)
from linecontrol.line.machine import Line
from linecontrol.store.keys import (
    KEY_GAIN,
    KEY_GAIN_VERSIONS,
    KEY_PUMP,
    KEY_RINSE,
    KEY_TENSION,
)
from linecontrol.store.snapshot import load_json, save_float, save_json


def test_recovery_of_an_empty_directory(tmp_path: Path) -> None:
    line = Line(tmp_path)
    state = line.recover()
    assert state.loaded == ()
    assert line.phase.value == "idle"
    assert line.pump.running() is False


def test_recovery_rebuilds_every_controller(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    save_json(line.store, KEY_TENSION, TensionSnapshot(value=91.0, gain=1.15))
    save_float(line.store, KEY_GAIN, 1.15)
    save_json(line.store, KEY_PUMP, PumpSnapshot(running=True))
    save_json(line.store, KEY_RINSE, RinseSnapshot(active=True))
    line.store.save("pump.hours", b"240.0")
    line.store.save("strip.track", b'{"position": 55.0, "seam_id": "s-1"}')
    line.store.save("pickle.acid", b'{"level": 6.5, "offset": 0.75}')
    line.store.save("weld.latch", b'{"interlock": true, "alarm": true}')

    recovered = Line(tmp_path)
    state = recovered.recover()

    assert "tension.current" in state.loaded
    assert recovered.tension.gain() == 1.15
    active = recovered.tension.active_gain_version()
    assert active is not None and active.gain == 1.15 and active.confirmed
    assert recovered.tension.setpoint() == 91.0
    assert recovered.pump.running() is True
    assert recovered.pump.run_seconds() == 240.0
    assert recovered.strip.track_position() == 55.0
    assert recovered.pickle.level() == 6.5
    assert recovered.pickle.offset() == 0.75
    assert recovered.weld.interlock_ok() is False
    assert recovered.pickle.rinsing() is False


def test_recovery_stops_a_restored_rinse_and_lifts_rolls(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    save_json(line.store, KEY_RINSE, RinseSnapshot(active=True))

    recovered = Line(tmp_path)
    recovered.recover()

    assert recovered.pickle.rinsing() is False
    assert recovered.exit.roll_up() is True
    assert recovered.exit.exhaust_ok() is False
    persisted, ok = recovered.pickle.rinse_persisted()
    assert ok is True and persisted.active is False


def test_recovery_restores_the_audit_log(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    line.load_coil("C-9", 40.0)
    line.weld_and_speedup()
    line.audit.flush()
    assert len(line.audit.events()) > 0

    recovered = Line(tmp_path)
    recovered.recover()
    assert len(recovered.audit.events()) > 0


def test_recovery_leaves_a_foreign_gain_alone(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    line.store.save(KEY_GAIN, b"0")
    recovered = Line(tmp_path)
    recovered.recover()
    assert recovered.tension.gain() == 1.0


def test_recovery_ignores_an_unconfirmed_gain_version(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    line.recalibrate_tension(1.2)
    line.confirm_tension_gain()
    # A newer calibration submitted but never confirmed.
    line.recalibrate_tension(1.4)
    assert line.tension.gain() == 1.2

    recovered = Line(tmp_path)
    recovered.recover()

    # Restart resumes the confirmed version, not the pending one.
    assert recovered.tension.gain() == 1.2
    pending = recovered.tension.pending_gain()
    assert pending is not None and pending.gain == 1.4
    assert recovered.tension.active_gain_version().version == 1
    assert KEY_GAIN_VERSIONS in recovered.recover().loaded


def test_recovery_uses_the_confirmed_gain_even_if_the_seam_snapshot_differs(
    tmp_path: Path,
) -> None:
    line = Line(tmp_path)
    line.recover()
    line.recalibrate_tension(1.2)
    line.confirm_tension_gain()
    # The running seam snapshot happens to carry an older gain.
    save_json(line.store, KEY_TENSION, TensionSnapshot(value=91.0, gain=1.15))

    recovered = Line(tmp_path)
    recovered.recover()

    assert recovered.tension.gain() == 1.2
    assert recovered.tension.setpoint() == 91.0


def test_recovery_persists_the_migrated_gain_version_log(tmp_path: Path) -> None:
    line = Line(tmp_path)
    line.recover()
    save_float(line.store, KEY_GAIN, 1.15)

    recovered = Line(tmp_path)
    recovered.recover()

    # The legacy float snapshot is imported into a confirmed version log.
    log, ok = load_json(recovered.store, KEY_GAIN_VERSIONS, GainVersionLog)
    assert ok is True
    assert log.active_version == 1
    assert log.versions[0].confirmed is True

    # A further restart reads the log directly instead of re-migrating.
    again = Line(tmp_path)
    state = again.recover()
    assert KEY_GAIN_VERSIONS in state.loaded
    assert again.tension.gain() == 1.15
