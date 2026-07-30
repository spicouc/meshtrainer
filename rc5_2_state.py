"""RC5.2 Phase 2 W4: State machine."""
from enum import Enum

class UnitState(Enum):
    OPEN = "OPEN"
    SERVER_ACTIVATION_READY = "SERVER_ACTIVATION_READY"
    CUT_ACTIVATION_ACCEPTED = "CUT_ACTIVATION_ACCEPTED"
    CUT_GRADIENT_READY = "CUT_GRADIENT_READY"
    CUT_GRADIENT_DELIVERED = "CUT_GRADIENT_DELIVERED"
    DELTA_VERIFIED = "DELTA_VERIFIED"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"
    EXPIRED = "EXPIRED"

TERMINAL = {UnitState.COMMITTED, UnitState.ABORTED, UnitState.EXPIRED}

_TRANSITIONS = {
    UnitState.OPEN: {UnitState.SERVER_ACTIVATION_READY},
    UnitState.SERVER_ACTIVATION_READY: {UnitState.CUT_GRADIENT_READY, UnitState.ABORTED, UnitState.EXPIRED},
    UnitState.CUT_ACTIVATION_ACCEPTED: {UnitState.CUT_GRADIENT_READY, UnitState.ABORTED, UnitState.EXPIRED},
    UnitState.CUT_GRADIENT_READY: {UnitState.CUT_GRADIENT_DELIVERED, UnitState.ABORTED, UnitState.EXPIRED},
    UnitState.CUT_GRADIENT_DELIVERED: {UnitState.DELTA_VERIFIED, UnitState.ABORTED, UnitState.EXPIRED},
    UnitState.DELTA_VERIFIED: {UnitState.COMMITTED, UnitState.ABORTED, UnitState.EXPIRED},
    UnitState.COMMITTED: set(),
    UnitState.ABORTED: set(),
    UnitState.EXPIRED: set(),
}

def can_transition(current: UnitState, next_state: UnitState) -> bool:
    return next_state in _TRANSITIONS.get(current, set())
