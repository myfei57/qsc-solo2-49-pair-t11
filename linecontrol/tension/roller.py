"""Tension roller controller."""

from __future__ import annotations

from ..store.filestore import FileStore
from .gain import GainOps
from .persist import PersistOps
from .ramp import RampOps
from .setpoint import SetpointOps
from .speed import SpeedOps
from .state import TensionState


class Roller(GainOps, SetpointOps, SpeedOps, PersistOps, RampOps):
    """Holds the tension setpoint and the conditions that permit speed-up."""

    def __init__(self, store: FileStore) -> None:
        self._store = store
        self._state = TensionState()

    @property
    def store(self) -> FileStore:
        return self._store

    @property
    def data(self) -> TensionState:
        return self._state

    def snapshot(self) -> dict[str, object]:
        log = self.data.gain_log
        active = log.active()
        pending = log.pending()
        return {
            "setpoint": self.setpoint(),
            "gain": self.gain(),
            "speed": self.speed(),
            "allowed": self.speed_permitted(),
            "effective": self.data.effective_tension(),
            "transitions": list(self.data.machine.history()),
            "gainVersion": active.version if active else 0,
            "pendingGain": pending.gain if pending else None,
            "pendingVersion": pending.version if pending else None,
            "gainVersions": [version.to_dict() for version in log.versions],
        }
