// state.js — estat global lleuger de la UI (no és font de veritat).
// La font de veritat és sempre l'API/DB; això només cacheja per a la UI.

export const state = {
  backends: [],
  settings: null,
  system: null,
  jobs: [],
  datasets: [],
  currentJob: null,
  currentJobEvents: [],
  sse: null,
  sseStatus: "disconnected",
};

export function set(partial) {
  Object.assign(state, partial);
}

export function get() {
  return state;
}
