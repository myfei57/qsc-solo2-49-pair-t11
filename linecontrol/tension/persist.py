"""Reading and restoring the persisted tension snapshot."""

from __future__ import annotations

from ..domain.records import TensionSnapshot
from ..store.keys import KEY_TENSION
from ..store.snapshot import load_json


class PersistOps:
    """Mixed into :class:`~linecontrol.tension.roller.Roller`."""

    def persisted_tension(self) -> tuple[TensionSnapshot, bool]:
        return load_json(self.store, KEY_TENSION, TensionSnapshot)

    def restore_tension(self, snapshot: TensionSnapshot) -> None:
        if snapshot.value <= 0:
            return
        self.data.setpoint = snapshot.value
        if snapshot.gain > 0 and not self.data.gain_confirmed:
            # Legacy snapshot fallback: a confirmed calibration version
            # installed earlier in recovery must not be overwritten here.
            self.data.gain = snapshot.gain
            self.data.gain_confirmed = True
        self.data.persisted = True
        self.data.machine.force("permitted")
