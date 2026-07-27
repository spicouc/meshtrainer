/**
 * worker.ts — RC3.2 Browser Web Worker.
 *
 * Executa en un thread dedicat (Web Worker). Responsable de:
 *   - Protocol RPC amb el coordinador
 *   - Màquina d'estats
 *   - Heartbeat
 *   - Càlcul determinista (SHA-256)
 *   - Submit de resultats
 *
 * Configuració (per ordre de prioritat):
 *   1. override explícit de test (via postMessage { type: 'config' })
 *   2. __COORDINATOR_URL__ injectat per Vite (build)
 *   3. Fallback a http://localhost:8791
 *
 * Missatges rebuts (del main thread):
 *   { type: 'config', coordinatorUrl: string }
 *   { type: 'start' }
 *   { type: 'cancel' }
 *   { type: 'pause' }
 *   { type: 'resume' }
 *   { type: 'training-start' }
 *   { type: 'training-stop' }
 *
 * Missatges enviats (al main thread):
 *   { type: 'state', state: string }
 *   { type: 'log', message: string }
 *   { type: 'progress', pct: number }
 *   { type: 'result', accepted: boolean, ... }
 *   { type: 'error', message: string }
 */

import { StateMachine } from './state';
import { RpcClient, TaskAssignment } from './rpc';
import { simulatedCompute } from './compute';

// ── Configuration ────────────────────────────────────────────────────

export type WorkerConfig = {
  coordinatorUrl: string;
};

const configuredCoordinatorUrl =
  typeof __COORDINATOR_URL__ !== 'undefined'
    ? __COORDINATOR_URL__
    : undefined;

export function resolveWorkerConfig(
  override?: Partial<WorkerConfig>,
): WorkerConfig {
  return {
    coordinatorUrl:
      override?.coordinatorUrl ??
      configuredCoordinatorUrl ??
      'http://localhost:8791',
  };
}

const HEARTBEAT_INTERVAL_MS = 30000; // 30s
const BACKOFF_BASE_MS = 1000;
const BACKOFF_MAX_MS = 30000;

// ── State ────────────────────────────────────────────────────────────

const stateMachine = new StateMachine();
let rpc: RpcClient | null = null;
let currentTask: (TaskAssignment & { task_params?: Record<string, unknown> }) | null = null;
let cancelled = false;
let paused = false;
let trainingMode = false;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;

function emitState(): void {
  self.postMessage({ type: 'state', state: stateMachine.state });
}

function emitLog(message: string): void {
  self.postMessage({ type: 'log', message });
}

function emitProgress(pct: number): void {
  self.postMessage({ type: 'progress', pct });
}

function emitError(message: string): void {
  self.postMessage({ type: 'error', message });
}

// ── Heartbeat ────────────────────────────────────────────────────────

function startHeartbeat(workerId: string): void {
  stopHeartbeat();
  heartbeatTimer = setInterval(async () => {
    try {
      await rpc!.heartbeat(workerId, stateMachine.state === 'RUNNING' ? 'busy' : 'idle');
    } catch {
      // Silently ignore heartbeat failures (will be detected by lease expiry)
    }
  }, HEARTBEAT_INTERVAL_MS);
}

function stopHeartbeat(): void {
  if (heartbeatTimer !== null) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

// ── Task lifecycle ───────────────────────────────────────────────────

function cleanupTask(): void {
  currentTask = null;
  cancelled = false;
  paused = false;
}

async function runTask(task: TaskAssignment & { task_params?: Record<string, unknown> }): Promise<void> {
  currentTask = task;
  cancelled = false;
  paused = false;

  // Read configurable task params (for test isolation)
  const params = (task.task_params || {}) as Record<string, number>;
  const iterations = Math.min(Math.max(typeof params.iterations === 'number' ? params.iterations : 5, 1), 500);
  const delayMs = Math.min(Math.max(typeof params.delay_ms === 'number' ? params.delay_ms : 30, 1), 5000);

  emitLog(`Task ${task.task_id}: starting compute (${iterations} iterations, ${delayMs}ms delay)`);

  // Periodic progress simulation
  const inputHash = task.input_hash || 'default';
  const configSeed = task.config_hash
    ? parseInt(task.config_hash.slice(0, 8), 16)
    : 42;

  // Simulate multi-step progress
  for (let step = 1; step <= iterations; step++) {
    if (cancelled) {
      emitLog('Cancel detected, stopping compute');
      stateMachine.transition('CANCELLED');
      emitState();
      await sleep(500); // Let UI capture CANCELLED before IDLE
      cleanupTask();
      stateMachine.transition('IDLE');
      emitState();
      return;
    }

    // Handle pause
    while (paused && !cancelled) {
      await sleep(200);
    }
    if (cancelled) {
      emitLog('Cancel detected after pause, stopping compute');
      stateMachine.transition('CANCELLED');
      emitState();
      await sleep(500);
      cleanupTask();
      stateMachine.transition('IDLE');
      emitState();
      return;
    }

    emitProgress(Math.round((step / iterations) * 100));

    // Simulate work (in real scenario this would be gradient computation)
    await sleep(delayMs);
  }

  // Compute deterministic output
  const outputHash = await simulatedCompute(inputHash, configSeed);
  emitLog(`Task ${task.task_id}: computed hash ${outputHash.slice(0, 16)}...`);

  if (cancelled) {
    emitLog('Cancel detected after compute, dropping result');
    stateMachine.transition('CANCELLED');
    emitState();
    await sleep(500);
    cleanupTask();
    stateMachine.transition('IDLE');
    emitState();
    return;
  }

  // Submit
  stateMachine.transition('SUBMITTING');
  emitState();

  try {
    const result = await rpc!.submitResult(
      task.task_id,
      rpc!.workerId!,
      'completed',
      outputHash,
      {
        duration_seconds: (iterations * delayMs) / 1000,
        samples_processed: 1,
        steps_completed: iterations,
        worker_type: 'browser',
      },
    );

    if (result.status === 'accepted') {
      emitLog(`Task ${task.task_id}: ACCEPTED ✓`);
      self.postMessage({ type: 'result', accepted: true, taskId: task.task_id });
    } else {
      emitLog(`Task ${task.task_id}: REJECTED (${result.validation_details?.reason || 'unknown'})`);
      emitError(`Task rejected: ${result.validation_details?.reason}`);
    }
  } catch (err) {
    emitError(`Submit failed: ${err}`);
    stateMachine.transition('ERROR');
    emitState();
    await backoffRetry();
  }

  cleanupTask();
  const next = trainingMode ? 'TRAINING' : 'IDLE';
  stateMachine.transition(next);
  emitState();
}

// ── Main loop ────────────────────────────────────────────────────────

async function workerMain(): Promise<void> {
  if (!rpc) {
    emitError('RPC client not configured — send { type: "config", coordinatorUrl } first');
    stateMachine.transition('ERROR');
    emitState();
    return;
  }

  // Register
  stateMachine.transition('REGISTERING');
  emitState();
  emitLog('Registering with coordinator...');

  try {
    const reg = await rpc.register({
      type: 'web',
      worker_version: '0.1.0',
      protocol_version: 'rc3-protocol-v1',
      browser: navigator.userAgent,
      operating_system: navigator.platform,
      logical_cpu_count: navigator.hardwareConcurrency || 1,
      wasm_support: typeof WebAssembly !== 'undefined',
      webgpu_support: false,
      max_task_size: 1024 * 1024, // 1MB
      supported_operations: ['simulated_compute'],
    });
    emitLog(`Registered as ${reg.worker_id}`);
    stateMachine.transition('IDLE');
    emitState();
    startHeartbeat(reg.worker_id);
  } catch (err) {
    emitError(`Registration failed: ${err}`);
    stateMachine.transition('ERROR');
    emitState();
    // Don't return — enter the main loop to retry via backoffRetry
  }

  // Main task loop
  while (!cancelled) {
    if (stateMachine.state === 'ERROR') {
      await backoffRetry();
      // Re-read state (backoffRetry may have mutated it)
      if ((stateMachine as any).state === 'DISCONNECTED') {
        break; // All retries exhausted
      }
      continue;
    }

    stateMachine.transition('REQUESTING_TASK');
    emitState();

    try {
      const taskType = trainingMode ? 'training' : 'simulated';
      const result = await rpc.requestTask(taskType);

      if (!result.task) {
        // No tasks available — backoff
        stateMachine.transition('IDLE');
        emitState();
        await sleep((result.retry_after_seconds || 15) * 1000);
        continue;
      }

      stateMachine.transition('RUNNING');
      emitState();
      await runTask(result.task);
    } catch (err) {
      emitError(`Task request failed: ${err}`);
      stateMachine.transition('ERROR');
      emitState();
      await backoffRetry();
    }
  }
}

// ── Backoff ──────────────────────────────────────────────────────────

async function backoffRetry(): Promise<void> {
  let delay = BACKOFF_BASE_MS;
  for (let attempt = 0; attempt < 5; attempt++) {
    emitLog(`Retrying in ${delay}ms...`);
    await sleep(delay);
    try {
      const ping = await rpc!.call('admin.ping', {});
      if (ping.result) {
        emitLog('Reconnected to coordinator');
        stateMachine.transition('IDLE');
        emitState();
        return;
      }
    } catch {
      // Still unreachable
    }
    delay = Math.min(delay * 2, BACKOFF_MAX_MS);
  }
  emitLog('Max retries reached, giving up');
  stateMachine.transition('DISCONNECTED');
  emitState();
}

// ── Message handler ──────────────────────────────────────────────────

self.onmessage = (event: MessageEvent) => {
  const msg = event.data;

  switch (msg.type) {
    case 'config':
      if (msg.coordinatorUrl) {
        rpc = new RpcClient(msg.coordinatorUrl);
        emitLog(`Coordinator URL set to ${msg.coordinatorUrl}`);
      }
      break;

    case 'start':
      cancelled = false;
      trainingMode = false;
      workerMain();
      break;

    case 'cancel':
      cancelled = true;
      emitLog('Cancel requested');
      break;

    case 'training-start':
      if (stateMachine.state === 'IDLE' || stateMachine.state === 'TRAINING') {
        trainingMode = true;
        stateMachine.transition('TRAINING');
        emitState();
        emitLog('Training mode activated');
      } else {
        emitLog('Cannot enter training mode from current state');
      }
      break;

    case 'training-stop':
      if (trainingMode) {
        trainingMode = false;
        if (stateMachine.state === 'TRAINING') {
          stateMachine.transition('IDLE');
          emitState();
        }
        emitLog('Training mode deactivated');
      }
      break;

    case 'pause':
      if (stateMachine.state === 'RUNNING') {
        paused = true;
        stateMachine.transition('PAUSED');
        emitState();
        emitLog('Paused');
      }
      break;

    case 'resume':
      if (stateMachine.state === 'PAUSED') {
        paused = false;
        stateMachine.transition(trainingMode ? 'TRAINING' : 'RUNNING');
        emitState();
        emitLog('Resumed');
      }
      break;
  }
};

// ── Utilities ───────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
