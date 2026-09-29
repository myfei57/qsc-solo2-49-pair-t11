"""Gain calibration kept as an explicit version log.

Submitting a calibration creates a *pending* version on disk but never touches
the live gain that the running coil is using. A separate confirmation step
activates a version; only confirmed versions are applied to the roller and
restored after a restart.
"""

from __future__ import annotations

from ..domain.events import utcnow
from ..domain.records import GainVersion, GainVersionLog
from ..store.keys import KEY_GAIN_VERSIONS
from ..store.snapshot import save_json
from .errors import TensionError, TensionErrorCode
from .state import GAIN_TOLERANCE


class GainOps:
    """Mixed into :class:`~linecontrol.tension.roller.Roller`."""

    def gain_versions(self) -> GainVersionLog:
        return self.data.gain_log

    def pending_gain(self) -> GainVersion | None:
        return self.data.gain_log.pending()

    def active_gain_version(self) -> GainVersion | None:
        return self.data.gain_log.active()

    def recalibrate(self, gain: float) -> GainVersion:
        """Submit a new calibration as a pending version.

        The live gain is deliberately left untouched, so a coil already
        running keeps the gain it started with until the version is
        confirmed.
        """

        if gain <= 0:
            raise TensionError(TensionErrorCode.INVALID_GAIN, "gain must be positive")
        latest = self.data.gain_log.last()
        if latest is not None and abs(gain - latest.gain) < GAIN_TOLERANCE:
            raise TensionError(
                TensionErrorCode.INVALID_GAIN,
                f"gain {gain} within tolerance of version {latest.version}",
            )
        number = (latest.version + 1) if latest is not None else 1
        version = GainVersion(version=number, gain=float(gain))
        self.data.gain_log = GainVersionLog(
            versions=self.data.gain_log.versions + (version,),
            active_version=self.data.gain_log.active_version,
        )
        self._save_gain_versions()
        return version

    def confirm_gain(self, number: int | None = None) -> GainVersion:
        """Activate a pending version; defaults to the newest pending one.

        This is the only operation allowed to change the live gain.
        """

        log = self.data.gain_log
        if number is None:
            target = log.pending()
            if target is None:
                raise TensionError(
                    TensionErrorCode.NO_PENDING_GAIN, "no pending gain version to confirm"
                )
        else:
            target = next(
                (
                    version
                    for version in reversed(log.versions)
                    if version.version == number
                ),
                None,
            )
            if target is None:
                raise TensionError(
                    TensionErrorCode.UNKNOWN_GAIN_VERSION,
                    f"gain version {number} does not exist",
                )
        if target.confirmed and log.active_version == target.version:
            return target
        confirmed = target.as_confirmed(utcnow())
        updated = tuple(
            confirmed if version.version == target.version else version
            for version in log.versions
        )
        self.data.gain_log = GainVersionLog(
            versions=updated,
            active_version=confirmed.version,
        )
        self.data.gain = confirmed.gain
        self._save_gain_versions()
        return confirmed

    def restore_gain_versions(self, log: GainVersionLog, legacy_gain: float = 0.0) -> None:
        """Rebuild the version log and the live gain during start-up recovery.

        The legacy single-value snapshot (``tension.gain``) from older
        installations is imported once as an already-confirmed baseline so
        the behaviour of existing data dirs is preserved.
        """

        if not log.versions and legacy_gain > 0:
            baseline = GainVersion(version=1, gain=float(legacy_gain))
            baseline = baseline.as_confirmed(utcnow())
            log = GainVersionLog(versions=(baseline,), active_version=baseline.version)
            self.data.gain_log = log
            self._save_gain_versions()
        else:
            self.data.gain_log = log
        active = log.active()
        if active is not None:
            self.data.gain = active.gain

    def gain(self) -> float:
        return self.data.gain

    def _save_gain_versions(self) -> None:
        save_json(self.store, KEY_GAIN_VERSIONS, self.data.gain_log)
