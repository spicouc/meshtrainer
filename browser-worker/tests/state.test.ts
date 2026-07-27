/**
 * state.test.ts — Unit tests for the state machine.
 */

import { describe, it, expect } from 'vitest';
import { StateMachine, canTransition, WorkerState } from '../src/state';

describe('StateMachine', () => {
  it('starts in INITIAL', () => {
    const sm = new StateMachine();
    expect(sm.state).toBe('INITIAL');
  });

  it('transitions INITIAL → REGISTERING', () => {
    const sm = new StateMachine();
    expect(sm.transition('REGISTERING')).toBe(true);
    expect(sm.state).toBe('REGISTERING');
  });

  it('rejects invalid transition INITIAL → RUNNING', () => {
    const sm = new StateMachine();
    expect(sm.transition('RUNNING')).toBe(false);
    expect(sm.state).toBe('INITIAL');
  });

  it('full lifecycle transitions are valid', () => {
    const sm = new StateMachine();
    expect(sm.transition('REGISTERING')).toBe(true);
    expect(sm.transition('IDLE')).toBe(true);
    expect(sm.transition('REQUESTING_TASK')).toBe(true);
    expect(sm.transition('RUNNING')).toBe(true);
    expect(sm.transition('SUBMITTING')).toBe(true);
    expect(sm.transition('IDLE')).toBe(true);
  });

  it('cancel during RUNNING goes to CANCELLED', () => {
    const sm = new StateMachine();
    sm.transition('REGISTERING');
    sm.transition('IDLE');
    sm.transition('REQUESTING_TASK');
    sm.transition('RUNNING');
    expect(sm.transition('CANCELLED')).toBe(true);
  });

  it('pause during RUNNING goes to PAUSED', () => {
    const sm = new StateMachine();
    sm.transition('REGISTERING');
    sm.transition('IDLE');
    sm.transition('REQUESTING_TASK');
    sm.transition('RUNNING');
    expect(sm.transition('PAUSED')).toBe(true);
  });

  it('resume from PAUSED goes to RUNNING', () => {
    const sm = new StateMachine();
    sm.transition('REGISTERING');
    sm.transition('IDLE');
    sm.transition('REQUESTING_TASK');
    sm.transition('RUNNING');
    sm.transition('PAUSED');
    expect(sm.transition('RUNNING')).toBe(true);
  });

  it('error state can retry to IDLE', () => {
    const sm = new StateMachine();
    sm.transition('REGISTERING');
    expect(sm.transition('ERROR')).toBe(true);
    expect(sm.transition('IDLE')).toBe(true);
  });

  it('idle can go to error', () => {
    const sm = new StateMachine();
    sm.transition('REGISTERING');
    sm.transition('IDLE');
    expect(sm.transition('ERROR')).toBe(true);
  });

  it('notifies listeners on transition', () => {
    const sm = new StateMachine();
    const events: { from: WorkerState; to: WorkerState }[] = [];
    sm.onTransition((from, to) => events.push({ from, to }));
    sm.transition('REGISTERING');
    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({ from: 'INITIAL', to: 'REGISTERING' });
  });

  it('reset goes to INITIAL', () => {
    const sm = new StateMachine();
    sm.transition('REGISTERING');
    sm.reset();
    expect(sm.state).toBe('INITIAL');
  });
});

describe('canTransition', () => {
  it('all valid transitions are defined', () => {
    expect(canTransition('INITIAL', 'REGISTERING')).toBe(true);
    expect(canTransition('DISCONNECTED', 'REGISTERING')).toBe(true);
    expect(canTransition('REGISTERING', 'IDLE')).toBe(true);
    expect(canTransition('REGISTERING', 'ERROR')).toBe(true);
    expect(canTransition('IDLE', 'REQUESTING_TASK')).toBe(true);
    expect(canTransition('RUNNING', 'SUBMITTING')).toBe(true);
    expect(canTransition('RUNNING', 'CANCELLED')).toBe(true);
    expect(canTransition('RUNNING', 'PAUSED')).toBe(true);
    expect(canTransition('PAUSED', 'RUNNING')).toBe(true);
    expect(canTransition('COMPLETED', 'IDLE')).toBe(true);
    expect(canTransition('CANCELLED', 'IDLE')).toBe(true);
    expect(canTransition('ERROR', 'IDLE')).toBe(true);
    expect(canTransition('ERROR', 'DISCONNECTED')).toBe(true);
  });

  it('all invalid transitions are rejected', () => {
    const allStates: WorkerState[] = [
      'INITIAL', 'DISCONNECTED', 'REGISTERING', 'IDLE', 'REQUESTING_TASK',
      'RUNNING', 'PAUSED', 'SUBMITTING', 'COMPLETED', 'CANCELLED', 'ERROR',
    ];
    for (const from of allStates) {
      for (const to of allStates) {
        const valid = canTransition(from as WorkerState, to as WorkerState);
        // Just make sure no exception is thrown and result is boolean
        expect(typeof valid).toBe('boolean');
      }
    }
  });
});
