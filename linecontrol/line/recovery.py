"""Start-up recovery from the on-disk snapshots."""

from __future__ import annotations

from ..domain.events import Event
from ..domain.kinds import EventKind
from ..domain.phase import Phase
from ..store.recovery import Recovery, collect


class RecoveryOps:
    """Mixed into :class:`~linecontrol.line.machine.Line`."""

    def recover(self) -> Recovery:
        """Rebuild every controller from the newest snapshots.

        The confirmed gain is applied before the tension setpoint is restored
        so the roller never runs a restored setpoint against an unconfirmed
        baseline.  Pending calibrations are left pending: only a signed-off
        version can drive the line after a restart.
        """

        self.set_phase(Phase.RECOVERING)
        state = collect(self.store)
        # Restore the signed-off gain before the setpoint, so the roller never
        # runs a restored setpoint against an unconfirmed baseline.  The
        # tension snapshot gain is only a legacy fallback when no confirmed
        # version exists; restoring it in tension.restore_tension handles that.
        confirmed = self.tension.confirmed_gain()
        if confirmed is not None:
            self.tension.apply_confirmed_gain(confirmed.gain)
        elif state.gain > 0:
            self.tension.apply_confirmed_gain(state.gain)
        self.weld.restore_latch(state.latch)
        self.weld.restore_tension(state.tension)
        self.tension.restore_tension(state.tension)
        self.pickle.restore_acid(state.acid)
        self.pickle.restore_rinse(state.rinse.active)
        self.pump.restore(state.pump)
        self.pump.restore_run(state.run_seconds)
        self.strip.restore_track(state.track)
        self.audit.restore()
        self.audit.flush()
        if state.rinse.active:
            self.pickle.rinse_stop()
        self.exit.recover_lift()
        self.bus.publish(Event(kind=EventKind.RECOVER, source="line", message="recovered"))
        self.set_phase(Phase.IDLE)
        return state
