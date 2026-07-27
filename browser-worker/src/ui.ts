/**
 * ui.ts — RC3.2 Browser Worker UI (main thread).
 *
 * Comunica amb el Web Worker via postMessage.
 * Mostra: estat, botons, progrés, log.
 *
 * Configuració (per ordre de prioritat):
 *   1. window.__RC3_TEST_CONFIG__.coordinatorUrl (mode test)
 *   2. sessionStorage.getItem('coordinator-url')
 *   3. http://localhost:8791
 */

// ── Type declarations ────────────────────────────────────────────────

interface RC3TestConfig {
  coordinatorUrl?: string;
}

declare global {
  interface Window {
    __RC3_TEST_CONFIG__?: RC3TestConfig;
    __workerUI?: () => WorkerUI;
  }
}

// ── Worker message types ─────────────────────────────────────────────

interface WorkerMessage {
  type: 'state' | 'log' | 'progress' | 'result' | 'error';
  state?: string;
  message?: string;
  pct?: number;
  accepted?: boolean;
  taskId?: string;
}

// ── UI class ─────────────────────────────────────────────────────────

export class WorkerUI {
  private worker: Worker;
  private statusEl: HTMLElement;
  private logEl: HTMLElement;
  private progressEl: HTMLElement;
  private startBtn: HTMLElement;
  private cancelBtn: HTMLElement;
  private pauseBtn: HTMLElement;
  private resumeBtn: HTMLElement;

  constructor() {
    // Create minimal UI elements
    this.statusEl = document.getElementById('worker-status') || this.createEl('div', 'worker-status');
    this.logEl = document.getElementById('worker-log') || this.createEl('pre', 'worker-log');
    this.progressEl = document.getElementById('worker-progress') || this.createEl('div', 'worker-progress');
    this.startBtn = document.getElementById('btn-start') || this.createEl('button', 'btn-start');
    this.cancelBtn = document.getElementById('btn-cancel') || this.createEl('button', 'btn-cancel');
    this.pauseBtn = document.getElementById('btn-pause') || this.createEl('button', 'btn-pause');
    this.resumeBtn = document.getElementById('btn-resume') || this.createEl('button', 'btn-resume');

    this.startBtn.textContent = '▶ Start';
    this.cancelBtn.textContent = '■ Cancel';
    this.pauseBtn.textContent = '⏸ Pause';
    this.resumeBtn.textContent = '▶ Resume';

    this.cancelBtn.setAttribute('disabled', 'true');
    this.pauseBtn.setAttribute('disabled', 'true');
    this.resumeBtn.setAttribute('disabled', 'true');

    // Create Worker
    this.worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' });

    // Resolve coordinator URL: test config > sessionStorage > default
    const testConfig = typeof window.__RC3_TEST_CONFIG__ !== 'undefined'
      ? window.__RC3_TEST_CONFIG__
      : undefined;
    const coordinatorUrl =
      testConfig?.coordinatorUrl ??
      sessionStorage.getItem('coordinator-url') ??
      'http://localhost:8791';
    this.worker.postMessage({ type: 'config', coordinatorUrl });

    // Wire buttons
    this.startBtn.addEventListener('click', () => this.start());
    this.cancelBtn.addEventListener('click', () => this.cancel());
    this.pauseBtn.addEventListener('click', () => this.pause());
    this.resumeBtn.addEventListener('click', () => this.resume());

    // Listen for Worker messages
    this.worker.addEventListener('message', (event: MessageEvent<WorkerMessage>) => {
      this.handleMessage(event.data);
    });

    this.log('Worker UI initialized');
    this.setButtons('initial');
  }

  // ── Public API (for test instrumentation) ──────────────────────────

  /** Expose the worker for direct test access. Only available in test mode. */
  getWorker(): Worker {
    return this.worker;
  }

  // ── Actions ──────────────────────────────────────────────────────

  start(): void {
    this.log('Starting worker...');
    this.worker.postMessage({ type: 'start' });
    this.setButtons('running');
  }

  cancel(): void {
    this.log('Cancelling...');
    this.worker.postMessage({ type: 'cancel' });
    this.setButtons('cancelling');
  }

  pause(): void {
    this.log('Pausing...');
    this.worker.postMessage({ type: 'pause' });
  }

  resume(): void {
    this.log('Resuming...');
    this.worker.postMessage({ type: 'resume' });
  }

  // ── Message handling ─────────────────────────────────────────────

  private handleMessage(msg: WorkerMessage): void {
    switch (msg.type) {
      case 'state':
        this.statusEl.textContent = `State: ${msg.state}`;
        this.updateButtons(msg.state as string);
        break;
      case 'log':
        this.log(msg.message || '');
        break;
      case 'progress':
        this.progressEl.textContent = `Progress: ${msg.pct}%`;
        break;
      case 'result':
        this.log(`Task ${msg.taskId}: ${msg.accepted ? 'ACCEPTED ✓' : 'REJECTED ✗'}`);
        this.setButtons('idle');
        break;
      case 'error':
        this.log(`ERROR: ${msg.message}`);
        this.setButtons('idle');
        break;
    }
  }

  // ── Button state management ──────────────────────────────────────

  private setButtons(mode: 'initial' | 'running' | 'cancelling' | 'idle'): void {
    switch (mode) {
      case 'initial':
        this.startBtn.removeAttribute('disabled');
        this.cancelBtn.setAttribute('disabled', 'true');
        this.pauseBtn.setAttribute('disabled', 'true');
        this.resumeBtn.setAttribute('disabled', 'true');
        break;
      case 'running':
        this.startBtn.setAttribute('disabled', 'true');
        this.cancelBtn.removeAttribute('disabled');
        this.pauseBtn.removeAttribute('disabled');
        this.resumeBtn.setAttribute('disabled', 'true');
        break;
      case 'cancelling':
        this.startBtn.setAttribute('disabled', 'true');
        this.cancelBtn.setAttribute('disabled', 'true');
        this.pauseBtn.setAttribute('disabled', 'true');
        this.resumeBtn.setAttribute('disabled', 'true');
        break;
      case 'idle':
        this.startBtn.removeAttribute('disabled');
        this.cancelBtn.setAttribute('disabled', 'true');
        this.pauseBtn.setAttribute('disabled', 'true');
        this.resumeBtn.setAttribute('disabled', 'true');
        break;
    }
  }

  private updateButtons(state: string): void {
    // Auto-update buttons based on worker state
    switch (state) {
      case 'RUNNING':
        this.cancelBtn.removeAttribute('disabled');
        this.pauseBtn.removeAttribute('disabled');
        this.resumeBtn.setAttribute('disabled', 'true');
        break;
      case 'PAUSED':
        this.resumeBtn.removeAttribute('disabled');
        this.cancelBtn.removeAttribute('disabled');
        this.pauseBtn.setAttribute('disabled', 'true');
        break;
      case 'IDLE':
        this.setButtons('idle');
        break;
      case 'ERROR':
      case 'DISCONNECTED':
        this.startBtn.removeAttribute('disabled');
        break;
    }
  }

  // ── Utilities ────────────────────────────────────────────────────

  private log(message: string): void {
    const time = new Date().toISOString().slice(11, 19);
    this.logEl.textContent += `[${time}] ${message}\n`;
    this.logEl.scrollTop = this.logEl.scrollHeight;
  }

  private createEl(tag: string, id: string): HTMLElement {
    const el = document.createElement(tag);
    el.id = id;
    document.body.appendChild(el);
    return el;
  }
}

// ── Auto-init ─────────────────────────────────────────────────────────

if (typeof document !== 'undefined') {
  const init = () => {
    const ui = new WorkerUI();
    // Expose workerUI for test instrumentation (only in test mode)
    if (typeof window.__RC3_TEST_CONFIG__ !== 'undefined') {
      window.__workerUI = () => ui;
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}
