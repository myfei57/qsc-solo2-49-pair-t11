"""Gain calibration for the tension roller.

A calibration never touches the running tension directly.  :meth:`recalibrate`
appends a ``pending`` entry to a version ledger; the live gain only changes
when an operator explicitly :meth:`confirms <confirm_gain>` a version.  The
confirmed baseline is written under :data:`KEY_GAIN`, so restart recovery
always restores the signed-off version rather than the last number somebody
dialled in.
"""

from __future__ import annotations

import dataclasses

from ..domain.records import GainVersion, utcnow
from ..store.keys import KEY_GAIN, KEY_GAIN_VERSIONS
from ..store.snapshot import load_versions, save_float, save_versions
from .errors import TensionError, TensionErrorCode
from .state import BASELINE_GAIN, GAIN_LEDGER_KEEP, GAIN_TOLERANCE


class GainOps:
    """Mixed into :class:`~linecontrol.tension.roller.Roller`."""

    def _versions(self) -> list[GainVersion]:
        """Return the cached ledger, loading it from disk on first use."""

        if not hasattr(self, "_gain_versions"):
            self._gain_versions = load_versions(self.store, KEY_GAIN_VERSIONS)
        return self._gain_versions

    def _store_versions(self, versions: list[GainVersion]) -> None:
        self._gain_versions = versions
        save_versions(self.store, KEY_GAIN_VERSIONS, versions)

    def _confirmed(self) -> GainVersion | None:
        return next(
            (v for v in reversed(self._versions()) if v.status == GainVersion.CONFIRMED),
            None,
        )

    def recalibrate(self, gain: float) -> GainVersion:
        """Record a calibration candidate without applying it.

        The new entry stays ``pending`` and cannot influence the running
        tension until :meth:`confirm_gain` is called.  A value within
        :data:`GAIN_TOLERANCE` of the latest entry returns that entry rather
        than creating a duplicate.
        """

        if gain <= 0:
            raise TensionError(TensionErrorCode.INVALID_GAIN, "gain must be positive")
        versions = self._versions()
        if not versions:
            # First calibration on a fresh roller: the factory gain of 1.0 is
            # the confirmed predecessor every later version derives from.
            versions = [
                GainVersion(
                    version=1,
                    gain=BASELINE_GAIN,
                    status=GainVersion.CONFIRMED,
                    calibrated=utcnow(),
                    confirmed=utcnow(),
                )
            ]
        # A repeat of an open candidate reuses it; otherwise compare against
        # the confirmed baseline.  Discarded versions never match.
        open_pending = next(
            (v for v in reversed(versions) if v.status == GainVersion.PENDING), None
        )
        if open_pending is not None and abs(gain - open_pending.gain) < GAIN_TOLERANCE:
            return open_pending
        baseline = self._confirmed() or versions[-1]
        if open_pending is None and abs(gain - baseline.gain) < GAIN_TOLERANCE:
            return baseline
        entry = GainVersion(
            version=max(v.version for v in versions) + 1,
            gain=float(gain),
            status=GainVersion.PENDING,
            calibrated=utcnow(),
        )
        self._store_versions(versions + [entry])
        return entry

    def confirm_gain(self, version: int | None = None) -> GainVersion:
        """Promote a pending version to the confirmed, live baseline.

        The confirmed gain applies immediately to anything running after the
        call and is the only version restart recovery will restore.  Any other
        pending entries are discarded so there is a single candidate queue.
        """

        versions = self._versions()
        candidate = self._select_pending(versions, version)
        now = utcnow()
        confirmed_entry = dataclasses.replace(
            candidate, status=GainVersion.CONFIRMED, confirmed=now
        )
        updated: list[GainVersion] = []
        for entry in versions:
            if entry is candidate:
                updated.append(confirmed_entry)
            elif entry.status == GainVersion.PENDING:
                updated.append(dataclasses.replace(entry, status=GainVersion.DISCARDED))
            else:
                updated.append(entry)
        self._trim(updated)
        self._store_versions(updated)
        self.data.gain = confirmed_entry.gain
        self.data.gain_confirmed = True
        save_float(self.store, KEY_GAIN, confirmed_entry.gain)
        return confirmed_entry

    def discard_pending(self, version: int | None = None) -> GainVersion:
        """Throw away a pending calibration without touching the live gain."""

        versions = self._versions()
        candidate = self._select_pending(versions, version)
        discarded = dataclasses.replace(candidate, status=GainVersion.DISCARDED)
        updated = [discarded if entry is candidate else entry for entry in versions]
        self._store_versions(updated)
        return discarded

    def _select_pending(
        self, versions: list[GainVersion], version: int | None
    ) -> GainVersion:
        if version is None:
            pending = [v for v in versions if v.status == GainVersion.PENDING]
            if not pending:
                raise TensionError(
                    TensionErrorCode.GAIN_NOT_PENDING, "no pending gain version"
                )
            return pending[-1]
        for entry in versions:
            if entry.version == version:
                if entry.status != GainVersion.PENDING:
                    raise TensionError(
                        TensionErrorCode.GAIN_NOT_PENDING,
                        f"gain version {version} is {entry.status}",
                    )
                return entry
        raise TensionError(
            TensionErrorCode.GAIN_NOT_PENDING, f"gain version {version} not found"
        )

    def _trim(self, versions: list[GainVersion]) -> None:
        """Cap ledger history.

        Oldest discarded entries go first.  If the ledger is still over the
        cap, the oldest confirmed entries are dropped, always preserving the
        newest confirmed baseline and every pending candidate.
        """

        while len(versions) > GAIN_LEDGER_KEEP:
            index = next(
                (i for i, v in enumerate(versions) if v.status == GainVersion.DISCARDED),
                None,
            )
            if index is not None:
                del versions[index]
                continue
            newest_confirmed = max(
                (i for i, v in enumerate(versions) if v.status == GainVersion.CONFIRMED),
                default=None,
            )
            if newest_confirmed is not None and newest_confirmed > 0:
                del versions[0]
            else:
                return

    def apply_confirmed_gain(self, gain: float) -> None:
        """Install a gain already confirmed elsewhere (used by recovery)."""

        if gain > 0:
            self.data.gain = float(gain)
            self.data.gain_confirmed = True

    def gain_versions(self) -> list[GainVersion]:
        return list(self._versions())

    def pending_gain(self) -> GainVersion | None:
        return next(
            (v for v in reversed(self._versions()) if v.status == GainVersion.PENDING),
            None,
        )

    def confirmed_gain(self) -> GainVersion | None:
        return self._confirmed()

    def gain(self) -> float:
        return self.data.gain
