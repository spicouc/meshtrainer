/**
 * rc3/state.ts — RC3.2 Worker state machine.
 *
 * Estats:
 *   INITIAL → REGISTERING → IDLE → REQUESTING_TASK → RUNNING
 *   → SUBMITTING → IDLE (loop)
 *
 * Transicions: veure RC3_2_BROWSER_WORKER.md §4
 *
 * Un sol task actiu per worker.
 */

export type WorkerState =
  | 'INITIAL'
  | 'DISCONNECTED'
  | 'REGISTERING'
  | 'IDLE'
  | 'REQUESTING_TASK'
  | 'TRAINING'
  | 'RUNNING'
  | 'PAUSED'
  | 'SUBMITTING'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'ERROR';

const VALID_TRANSITIONS: Record<WorkerState, WorkerState[]> = {
  INITIAL: ['REGISTERING'],
  DISCONNECTED: ['REGISTERING'],
  REGISTERING: ['IDLE', 'ERROR'],
  IDLE: ['REQUESTING_TASK', 'TRAINING', 'ERROR', 'DISCONNECTED'],
  TRAINING: ['RUNNING', 'IDLE', 'PAUSED', 'CANCELLED', 'ERROR'],
  REQUESTING_TASK: ['RUNNING', 'IDLE', 'ERROR'],
  RUNNING: ['SUBMITTING', 'PAUSED', 'TRAINING', 'CANCELLED', 'ERROR'],
  PAUSED: ['RUNNING', 'TRAINING', 'CANCELLED', 'ERROR'],
  SUBMITTING: ['IDLE', 'TRAINING', 'ERROR', 'COMPLETED'],
  COMPLETED: ['IDLE'],
  CANCELLED: ['IDLE'],
  ERROR: ['IDLE', 'DISCONNECTED'],
};

export function canTransition(from: WorkerState, to: WorkerState): boolean {
  return VALID_TRANSITIONS[from]?.includes(to) ?? false;
}

export type StateChangeListener = (from: WorkerState, to: WorkerState, data?: any) => void;

export class StateMachine {
  private _state: WorkerState = 'INITIAL';
  private _listeners: StateChangeListener[] = [];

  get state(): WorkerState {
    return this._state;
  }

  onTransition(listener: StateChangeListener): void {
    this._listeners.push(listener);
  }

  transition(to: WorkerState, data?: any): boolean {
    if (!canTransition(this._state, to)) {
      console.warn(`[State] Invalid transition: ${this._state} → ${to}`);
      return false;
    }
    const from = this._state;
    this._state = to;
    for (const listener of this._listeners) {
      listener(from, to, data);
    }
    return true;
  }

  /** Reset to initial state (after fatal error / page reload detection). */
  reset(): void {
    this._state = 'INITIAL';
  }
}
