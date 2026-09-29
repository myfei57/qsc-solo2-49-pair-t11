"""Tension setpoint, gain calibration and the speed ramp."""

from __future__ import annotations

import pytest

from linecontrol.domain.records import (
    GainVersionLog,
    TensionSnapshot,
    utcnow,
)
from linecontrol.store.filestore import FileStore
from linecontrol.store.keys import KEY_GAIN_VERSIONS, KEY_TENSION
from linecontrol.store.snapshot import save_json
from linecontrol.tension.errors import TensionError, TensionErrorCode
from linecontrol.tension.roller import Roller
from linecontrol.tension.state import SpeedState, SpeedStep


def build(tmp_path) -> Roller:
    return Roller(FileStore(tmp_path))


def test_calibration_creates_a_pending_version_without_live_change(tmp_path) -> None:
    roller = build(tmp_path)
    roller.apply_setpoint(80.0)
    version = roller.recalibrate(1.2)
    assert version.version == 1
    assert version.confirmed is False
    # The pending calibration is on disk ...
    assert roller.store.exists(KEY_GAIN_VERSIONS)
    # ... but the running coil keeps the old gain.
    assert roller.gain() == pytest.approx(1.0)
    assert roller.data.effective_tension() == pytest.approx(80.0)
    pending = roller.pending_gain()
    assert pending is not None and pending.gain == pytest.approx(1.2)
    assert roller.active_gain_version() is None


def test_confirmation_activates_the_pending_gain(tmp_path) -> None:
    roller = build(tmp_path)
    roller.apply_setpoint(80.0)
    roller.recalibrate(1.2)
    confirmed = roller.confirm_gain()
    assert confirmed.version == 1
    assert confirmed.confirmed is True
    assert roller.gain() == pytest.approx(1.2)
    assert roller.data.effective_tension() == pytest.approx(96.0)
    assert roller.pending_gain() is None
    assert roller.active_gain_version() == confirmed


def test_gain_validation(tmp_path) -> None:
    roller = build(tmp_path)
    with pytest.raises(TensionError) as caught:
        roller.recalibrate(0.0)
    assert caught.value.code is TensionErrorCode.INVALID_GAIN
    # 1.0 vs default 1.0 (tolerance 0.05) is rejected as noise.
    roller.recalibrate(1.2)
    with pytest.raises(TensionError) as caught:
        roller.recalibrate(1.21)
    assert caught.value.code is TensionErrorCode.INVALID_GAIN


def test_confirm_without_a_pending_version_fails(tmp_path) -> None:
    roller = build(tmp_path)
    with pytest.raises(TensionError) as caught:
        roller.confirm_gain()
    assert caught.value.code is TensionErrorCode.NO_PENDING_GAIN


def test_confirm_an_unknown_version_fails(tmp_path) -> None:
    roller = build(tmp_path)
    roller.recalibrate(1.2)
    with pytest.raises(TensionError) as caught:
        roller.confirm_gain(99)
    assert caught.value.code is TensionErrorCode.UNKNOWN_GAIN_VERSION


def test_effective_tension_only_follows_a_confirmed_gain(tmp_path) -> None:
    roller = build(tmp_path)
    roller.apply_setpoint(80.0)
    roller.recalibrate(1.25)
    assert roller.data.effective_tension() == pytest.approx(80.0)
    roller.confirm_gain()
    assert roller.data.effective_tension() == pytest.approx(100.0)


def test_a_new_calibration_while_running_does_not_move_the_live_gain(tmp_path) -> None:
    roller = build(tmp_path)
    roller.apply_setpoint(80.0)
    roller.recalibrate(1.2)
    roller.confirm_gain()
    assert roller.gain() == pytest.approx(1.2)
    # Operator submits a fresh calibration while the coil is running.
    newer = roller.recalibrate(1.35)
    assert newer.version == 2
    assert roller.gain() == pytest.approx(1.2)
    # Only the confirmed version drives the running tension.
    assert roller.data.effective_tension() == pytest.approx(96.0)
    roller.confirm_gain()
    assert roller.gain() == pytest.approx(1.35)


def test_speed_up_requires_a_persisted_tension(tmp_path) -> None:
    roller = build(tmp_path)
    with pytest.raises(TensionError) as caught:
        roller.allow_speedup()
    assert caught.value.code is TensionErrorCode.NOT_PERSISTED
    save_json(
        roller.store,
        KEY_TENSION,
        TensionSnapshot(value=93.0, gain=1.1, saved=utcnow()),
    )
    roller.recalibrate(1.1)
    roller.confirm_gain()
    snapshot = roller.allow_speedup()
    assert snapshot.value == pytest.approx(93.0)
    assert roller.setpoint() == pytest.approx(93.0)
    # The gain comes from the confirmed version, not the seam snapshot.
    assert roller.gain() == pytest.approx(1.1)
    assert roller.speed_permitted() is True
    assert roller.speed() == SpeedState.RUNNING


def test_speed_up_ignores_gain_in_the_seam_snapshot(tmp_path) -> None:
    roller = build(tmp_path)
    roller.recalibrate(1.2)
    roller.confirm_gain()
    # An old seam snapshot carries a different gain; it must not override
    # the confirmed version when the line speeds up again.
    save_json(
        roller.store,
        KEY_TENSION,
        TensionSnapshot(value=93.0, gain=1.1, saved=utcnow()),
    )
    roller.allow_speedup()
    assert roller.gain() == pytest.approx(1.2)


def test_ramp_plan_scales_with_the_target(tmp_path) -> None:
    roller = build(tmp_path)
    save_json(roller.store, KEY_TENSION, TensionSnapshot(value=88.0, saved=utcnow()))
    roller.allow_speedup()
    short = roller.plan_speedup(1.5)
    assert len(short.steps) == 3
    assert short.steps[-1].speed == pytest.approx(1.5)
    long = roller.plan_speedup(3.0)
    assert len(long.steps) == 5
    for step in long.steps:
        roller.apply_step(step)
    assert roller.speed() in {SpeedState.RUNNING, SpeedState.FULL}


def test_ramp_requires_persistence_and_positive_target(tmp_path) -> None:
    roller = build(tmp_path)
    with pytest.raises(TensionError) as caught:
        roller.plan_speedup(0.0)
    assert caught.value.code is TensionErrorCode.INVALID_SPEED
    with pytest.raises(TensionError) as caught:
        roller.plan_speedup(2.0)
    assert caught.value.code is TensionErrorCode.RAMP_UNREADY


def test_apply_step_requires_a_ready_roller(tmp_path) -> None:
    roller = build(tmp_path)
    with pytest.raises(TensionError) as caught:
        roller.apply_step(SpeedStep(speed=1.0, setpoint=80.0, step_index=1))
    assert caught.value.code is TensionErrorCode.RAMP_UNREADY


def test_restore_tension_rebuilds_the_setpoint(tmp_path) -> None:
    roller = build(tmp_path)
    roller.restore_tension(TensionSnapshot(value=70.0, gain=1.3, saved=utcnow()))
    assert roller.setpoint() == pytest.approx(70.0)
    # The seam snapshot gain must not change the live gain on restore.
    assert roller.gain() == pytest.approx(1.0)
    assert roller.speed_permitted() is True
    empty = build(tmp_path / "other")
    empty.restore_tension(TensionSnapshot(value=0.0, saved=utcnow()))
    assert empty.speed_permitted() is False


def test_restore_gain_versions_applies_only_the_confirmed_one(tmp_path) -> None:
    roller = build(tmp_path)
    log = GainVersionLog.from_dict(
        {
            "versions": [
                {"version": 1, "gain": 1.1, "created": utcnow().isoformat(),
                 "confirmedAt": utcnow().isoformat()},
                {"version": 2, "gain": 1.3, "created": utcnow().isoformat(),
                 "confirmedAt": None},
            ],
            "activeVersion": 1,
        }
    )
    roller.restore_gain_versions(log)
    assert roller.gain() == pytest.approx(1.1)
    pending = roller.pending_gain()
    assert pending is not None and pending.version == 2
    # Confirming the pending version after restart activates it.
    roller.confirm_gain()
    assert roller.gain() == pytest.approx(1.3)


def test_restore_gain_versions_imports_the_legacy_float_as_confirmed(tmp_path) -> None:
    roller = build(tmp_path)
    roller.restore_gain_versions(GainVersionLog(), legacy_gain=1.15)
    assert roller.gain() == pytest.approx(1.15)
    active = roller.active_gain_version()
    assert active is not None
    assert active.version == 1 and active.confirmed is True
    assert roller.store.exists(KEY_GAIN_VERSIONS)
    # A restart from the now-migrated log keeps the confirmed baseline.
    again = build(tmp_path / "again")
    log = roller.gain_versions()
    again.restore_gain_versions(log, legacy_gain=9.9)
    assert again.gain() == pytest.approx(1.15)


def test_snapshot_exposes_the_speed_machine(tmp_path) -> None:
    roller = build(tmp_path)
    view = roller.snapshot()
    assert view["speed"] == SpeedState.STANDBY
    assert view["transitions"] == [SpeedState.STANDBY]
    assert view["gainVersion"] == 0
    assert view["pendingGain"] is None
    assert view["gainVersions"] == []
