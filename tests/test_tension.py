"""Tension setpoint, gain calibration and the speed ramp."""

from __future__ import annotations

import pytest

from linecontrol.domain.records import (
    GainVersion,
    TensionSnapshot,
    utcnow,
)
from linecontrol.store.filestore import FileStore
from linecontrol.store.keys import KEY_GAIN, KEY_GAIN_VERSIONS, KEY_TENSION
from linecontrol.store.snapshot import save_json
from linecontrol.tension.errors import TensionError, TensionErrorCode
from linecontrol.tension.roller import Roller
from linecontrol.tension.state import SpeedState, SpeedStep


def build(tmp_path) -> Roller:
    return Roller(FileStore(tmp_path))


def test_calibration_stays_pending_and_off_the_live_gain(tmp_path) -> None:
    roller = build(tmp_path)
    roller.apply_setpoint(80.0)
    entry = roller.recalibrate(1.2)
    assert entry.status == GainVersion.PENDING
    assert entry.version == 2
    # The running coil keeps the old gain ...
    assert roller.gain() == pytest.approx(1.0)
    assert roller.data.effective_tension() == pytest.approx(80.0)
    assert not roller.store.exists(KEY_GAIN)
    # ... but the candidate is recorded in the version ledger.
    assert roller.store.exists(KEY_GAIN_VERSIONS)
    pending = roller.pending_gain()
    assert pending is not None and pending.gain == pytest.approx(1.2)


def test_calibration_dedupes_values_within_tolerance(tmp_path) -> None:
    roller = build(tmp_path)
    first = roller.recalibrate(1.2)
    assert first.version == 2
    same = roller.recalibrate(1.21)
    assert same.version == first.version
    # A different value opens another pending candidate.
    assert roller.recalibrate(1.4).version == 3
    # Repeating the confirmed baseline while a candidate is open still
    # records a new pending version instead of silently no-opping.
    assert roller.recalibrate(1.01).version == 4
    roller.confirm_gain()
    # Once confirmed, repeating that value returns the confirmed entry.
    again = roller.recalibrate(1.0)
    assert again.status == GainVersion.CONFIRMED and again.version == 4
    # A discarded value can be recalibrated as a brand-new pending version.
    reopened = roller.recalibrate(1.2)
    assert reopened.status == GainVersion.PENDING and reopened.version == 5
    with pytest.raises(TensionError) as caught:
        roller.recalibrate(0.0)
    assert caught.value.code is TensionErrorCode.INVALID_GAIN


def test_confirming_a_version_applies_and_persists_it(tmp_path) -> None:
    roller = build(tmp_path)
    roller.apply_setpoint(80.0)
    entry = roller.recalibrate(1.25)
    confirmed = roller.confirm_gain()
    assert confirmed.version == entry.version
    assert confirmed.status == GainVersion.CONFIRMED
    assert confirmed.confirmed is not None
    assert roller.gain() == pytest.approx(1.25)
    assert roller.data.gain_confirmed is True
    assert roller.data.effective_tension() == pytest.approx(100.0)
    assert roller.pending_gain() is None

    rebuilt = Roller(roller.store)
    assert rebuilt.confirmed_gain().version == confirmed.version


def test_confirming_rejects_the_other_pending_candidates(tmp_path) -> None:
    roller = build(tmp_path)
    roller.recalibrate(1.15)
    roller.recalibrate(1.2)
    confirmed = roller.confirm_gain()
    assert confirmed.gain == pytest.approx(1.2)
    statuses = {v.version: v.status for v in roller.gain_versions()}
    assert statuses[2] == GainVersion.DISCARDED
    assert statuses[3] == GainVersion.CONFIRMED
    with pytest.raises(TensionError) as caught:
        roller.confirm_gain()
    assert caught.value.code is TensionErrorCode.GAIN_NOT_PENDING


def test_confirming_a_specific_version_must_be_pending(tmp_path) -> None:
    roller = build(tmp_path)
    roller.recalibrate(1.2)
    roller.confirm_gain(2)
    with pytest.raises(TensionError) as caught:
        roller.confirm_gain(2)
    assert caught.value.code is TensionErrorCode.GAIN_NOT_PENDING
    with pytest.raises(TensionError) as caught:
        roller.confirm_gain(99)
    assert caught.value.code is TensionErrorCode.GAIN_NOT_PENDING


def test_discarding_a_pending_version_keeps_the_live_gain(tmp_path) -> None:
    roller = build(tmp_path)
    roller.apply_setpoint(80.0)
    roller.recalibrate(1.3)
    discarded = roller.discard_pending()
    assert discarded.status == GainVersion.DISCARDED
    assert roller.pending_gain() is None
    assert roller.gain() == pytest.approx(1.0)
    assert roller.data.effective_tension() == pytest.approx(80.0)


def test_unconfirmed_gain_never_reaches_the_live_tension(tmp_path) -> None:
    roller = build(tmp_path)
    roller.apply_setpoint(80.0)
    roller.recalibrate(1.25)
    assert roller.data.effective_tension() == pytest.approx(80.0)
    roller.confirm_gain()
    assert roller.data.effective_tension() == pytest.approx(100.0)


def test_gain_ledger_is_trimmed_to_its_cap(tmp_path) -> None:
    from linecontrol.tension.state import GAIN_LEDGER_KEEP

    roller = build(tmp_path)
    for index in range(1, GAIN_LEDGER_KEEP + 5):
        roller.recalibrate(1.0 + index * 0.1)
        roller.confirm_gain()
    versions = roller.gain_versions()
    assert len(versions) == GAIN_LEDGER_KEEP
    assert versions[-1].status == GainVersion.CONFIRMED
    assert roller.confirmed_gain() == versions[-1]


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
    snapshot = roller.allow_speedup()
    assert snapshot.value == pytest.approx(93.0)
    assert roller.setpoint() == pytest.approx(93.0)
    assert roller.gain() == pytest.approx(1.1)
    assert roller.speed_permitted() is True
    assert roller.speed() == SpeedState.RUNNING


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
    assert roller.gain() == pytest.approx(1.3)
    assert roller.speed_permitted() is True
    empty = build(tmp_path / "other")
    empty.restore_tension(TensionSnapshot(value=0.0, saved=utcnow()))
    assert empty.speed_permitted() is False


def test_a_confirmed_gain_is_not_overridden_by_a_restored_snapshot(tmp_path) -> None:
    roller = build(tmp_path)
    roller.recalibrate(1.3)
    roller.confirm_gain()
    # A snapshot welded at an older gain restores the setpoint but must not
    # roll the live gain back.
    roller.restore_tension(TensionSnapshot(value=70.0, gain=1.1, saved=utcnow()))
    assert roller.setpoint() == pytest.approx(70.0)
    assert roller.gain() == pytest.approx(1.3)
    # The same guard applies to the speed-up path.
    save_json(
        roller.store,
        KEY_TENSION,
        TensionSnapshot(value=75.0, gain=1.05, saved=utcnow()),
    )
    roller.data.persisted = False
    roller.allow_speedup()
    assert roller.setpoint() == pytest.approx(75.0)
    assert roller.gain() == pytest.approx(1.3)


def test_snapshot_exposes_the_speed_machine(tmp_path) -> None:
    roller = build(tmp_path)
    view = roller.snapshot()
    assert view["speed"] == SpeedState.STANDBY
    assert view["transitions"] == [SpeedState.STANDBY]
