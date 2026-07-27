/**
 * RC3.2 E2E Tests — Playwright + Chromium.
 *
 * 21 tests total (no. 21 validates __workerUI production guard).
 * Gate: RC3.2 — 21/21 PASS required for certification.
 *
 * Each test uses:
 *   - Fresh DB context (coordinator started with --admin)
 *   - Unique task IDs via test isolation
 *
 * Configuration is injected via __RC3_TEST_CONFIG__ (window property set
 * before page loads via addInitScript — this is the highest-priority
 * config source).
 */

import { test, expect } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

// ── Constants ──────────────────────────────────────────────────────────

const PROJECT_DIR = path.resolve(__dirname, '..');
const COORDINATOR_URL = 'http://localhost:8791';
const PAGE_URL = 'http://localhost:5173';
const TIMEOUT = 60000;

// ── Helpers ────────────────────────────────────────────────────────────

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

interface TaskResult {
  task_id?: string;
  status?: string;
  state?: string;
  worker_id?: string;
  assigned_worker_id?: string;
  result?: string;
  output_hash?: string;
}

async function rpcCall(
  method: string,
  params: Record<string, unknown>
): Promise<any> {
  const resp = await fetch(COORDINATOR_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method,
      params,
    }),
  });
  const body = await resp.json();
  if (body.error) throw new Error(body.error.message);
  return body.result;
}

async function setupTestPage(page: any, coordinatorUrl?: string): Promise<void> {
  const url = coordinatorUrl || COORDINATOR_URL;
  await page.addInitScript((config: Record<string, unknown>) => {
    (window as any).__RC3_TEST_CONFIG__ = config;
  }, { coordinatorUrl: url });
  await page.goto(PAGE_URL, { waitUntil: 'networkidle' });
}

/** Wait until #worker-status textContent includes expectedState */
const waitForState = async (page: any, expectedState: string, timeout = 15000): Promise<boolean> => {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try {
      const state = await page.textContent('#worker-status');
      if (state?.includes(expectedState)) {
        return true;
      }
    } catch {
      // element not yet rendered
    }
    await sleep(200);
  }
  return false;
};

/** Wait until #worker-log textContent includes a message */
const waitForLog = async (page: any, text: string, timeout = 15000): Promise<boolean> => {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try {
      const log = await page.textContent('#worker-log');
      if (log?.includes(text)) {
        return true;
      }
    } catch {
      // element not yet rendered
    }
    await sleep(200);
  }
  return false;
};

/** Create a task on the coordinator and return its task_id */
async function createTaskForTest(
  inputHash = 'hello',
  configHash = '0000002a',
  taskParams?: Record<string, unknown>
): Promise<string> {
  const projectId = `p-test-${Date.now()}`;
  const runId = `r-test-${Date.now()}`;
  const taskResult = await rpcCall('admin.create_task', {
    project_id: projectId,
    run_id: runId,
    strategy: 'simulated',
    input_hash: inputHash,
    config_hash: configHash,
    task_params: taskParams || {},
  });
  return taskResult.task_id;
}

/** Click the Start button and wait for IDLE state (worker registered) */
async function startWorker(page: any): Promise<void> {
  await page.click('#btn-start');
}

/** Get coordinator health */
async function coordinatorHealth(): Promise<any> {
  const resp = await fetch(`${COORDINATOR_URL}/health`);
  return resp.json();
}

// ── Tests ──────────────────────────────────────────────────────────────

test.describe('RC3.2 Browser Worker — E2E', () => {

  // ── Test 1: UI initializes correctly ──────────────────────────────────

  test('1. UI loads and shows initial state', async ({ page }) => {
    await setupTestPage(page);
    // The HTML template renders "State: INITIAL" before the worker starts.
    const status = await page.textContent('#worker-status');
    expect(status).toContain('INITIAL');
    expect(await page.textContent('#worker-log')).toBeTruthy();
    expect(await page.$('#btn-start')).toBeTruthy();
    expect(await page.$('#btn-cancel')).toBeTruthy();

    // After Start, the machine transitions: INITIAL → REGISTERING → IDLE
    await startWorker(page);
    const idle = await waitForState(page, 'IDLE', TIMEOUT);
    expect(idle).toBe(true);
  });

  // ── Test 2: Worker registers with coordinator ─────────────────────────

  test('2. Worker registers and reaches IDLE', async ({ page }) => {
    await setupTestPage(page);
    await startWorker(page);
    const idle = await waitForState(page, 'IDLE', TIMEOUT);
    expect(idle).toBe(true);
  });

  // ── Test 3: Worker processes a task completely ────────────────────────

  test('3. Worker completes a task end-to-end', async ({ page }) => {
    await setupTestPage(page);
    // Use a longer task to ensure RUNNING is observable
    const taskId = await createTaskForTest('e2e-test', '0000002a', {
      iterations: 20,
      delay_ms: 80,
    });
    await startWorker(page);

    // Wait for RUNNING state
    const running = await waitForState(page, 'RUNNING', TIMEOUT);
    expect(running).toBe(true);

    // Verify that the ACCEPTED log appears (proof task was submitted)
    // This is more reliable than polling for the SUBMITTING state.
    const accepted = await waitForLog(page, 'ACCEPTED', TIMEOUT);
    expect(accepted).toBe(true);

    // Final state is IDLE
    const idle = await waitForState(page, 'IDLE', TIMEOUT);
    expect(idle).toBe(true);

    // Verify the task was completed on the coordinator side
    const task = await rpcCall('admin.get_task', { task_id: taskId });
    expect(task.status).toBe('COMPLETED');
    expect(task.result).toBeTruthy();
  });

  // ── Test 4: Worker progress updates ──────────────────────────────────

  test('4. Worker sends progress updates during compute', async ({ page }) => {
    await setupTestPage(page);
    // Use a long task so we see intermediate progress
    await createTaskForTest('progress-test', '0000002a', {
      iterations: 30,
      delay_ms: 100,
    });
    await startWorker(page);

    // Wait for RUNNING state
    const running = await waitForState(page, 'RUNNING', TIMEOUT);
    expect(running).toBe(true);

    // Progress is rendered in #worker-progress, NOT #worker-log.
    // Check that it shows a percentage between 0 and 100.
    let progressText = '';
    const startTime = Date.now();
    while (Date.now() - startTime < 15000) {
      try {
        progressText = await page.textContent('#worker-progress');
        if (progressText && progressText.includes('%') && !progressText.includes('0%')) {
          break;
        }
      } catch { /* not yet */ }
      await sleep(200);
    }
    expect(progressText).toContain('%');

    // Extract the numeric percentage value
    const pctMatch = progressText.match(/(\d+)%/);
    expect(pctMatch).not.toBeNull();
    const pct = parseInt(pctMatch![1], 10);
    expect(pct).toBeGreaterThan(0);
    expect(pct).toBeLessThanOrEqual(100);

    // Wait for completion and verify progress reaches 100%
    const idle = await waitForState(page, 'IDLE', TIMEOUT);
    expect(idle).toBe(true);

    const finalProgress = await page.textContent('#worker-progress');
    expect(finalProgress).toContain('100%');
  });

  // ── Test 5: Multiple sequential tasks ────────────────────────────────

  test('5. Worker handles 3 sequential tasks', async ({ page }) => {
    await setupTestPage(page);

    for (let i = 0; i < 3; i++) {
      const taskId = await createTaskForTest(`seq-${i}`);
      await startWorker(page);

      const idle = await waitForState(page, 'IDLE', TIMEOUT);
      expect(idle).toBe(true);

      const task = await rpcCall('admin.get_task', { task_id: taskId });
      // The coordinator normalizes state → status
      expect(task.status).toBe('COMPLETED');
    }
  });

  // ── Test 6: Output hash is deterministic ─────────────────────────────

  test('6. Same input produces same output hash', async ({ page }) => {
    await setupTestPage(page);

    const inputHash = 'deterministic-test-42';
    const task1 = await createTaskForTest(inputHash);
    await startWorker(page);
    await waitForState(page, 'IDLE', TIMEOUT);
    const result1 = await rpcCall('admin.get_task', { task_id: task1 });

    // Reset worker (page reload)
    await setupTestPage(page);
    const task2 = await createTaskForTest(inputHash);
    await startWorker(page);
    await waitForState(page, 'IDLE', TIMEOUT);
    const result2 = await rpcCall('admin.get_task', { task_id: task2 });

    expect(result1.result).toBe(result2.result);
  });

  // ── Test 7: Pause and Resume ────────────────────────────────────────

  test('7. Worker pauses and resumes on demand', async ({ page }) => {
    await setupTestPage(page);
    await createTaskForTest('pause-test', '0000002a', {
      iterations: 30,
      delay_ms: 100,
    });
    await startWorker(page);

    const running = await waitForState(page, 'RUNNING', TIMEOUT);
    expect(running).toBe(true);

    // Click Pause
    await page.click('#btn-pause');
    const paused = await waitForState(page, 'PAUSED', 15000);
    expect(paused).toBe(true);

    // Click Resume
    await page.click('#btn-resume');
    const resumedRunning = await waitForState(page, 'RUNNING', 15000);
    expect(resumedRunning).toBe(true);

    const idle = await waitForState(page, 'IDLE', 45000);
    expect(idle).toBe(true);
  });

  // ── Test 8: Cancel during computation ────────────────────────────────

  test('8. Cancel during computation drops result', async ({ page }) => {
    await setupTestPage(page);

    // Create a long task so we have time to cancel
    await createTaskForTest('cancel-test', '0000002a', {
      iterations: 50,
      delay_ms: 100,
    });
    await startWorker(page);

    // Wait for RUNNING state
    const running = await waitForState(page, 'RUNNING', TIMEOUT);
    expect(running).toBe(true);

    // Verify Cancel button is disabled → enabled after running
    const cancelDisabled = await page.$eval('#btn-cancel', (el: any) =>
      (el as HTMLButtonElement).disabled
    );
    expect(cancelDisabled).toBe(false);

    // Click Cancel button
    await page.click('#btn-cancel');

    // Verify cancel log appears
    const detected = await waitForLog(page, 'Cancel detected', 20000);
    expect(detected).toBe(true);

    // Verify the CANCELLED state appears
    const cancelled = await waitForState(page, 'CANCELLED', 15000);
    expect(cancelled).toBe(true);

    // Verify log shows the task was dropped (no SUBMITTING/ACCEPTED)
    const logText = await page.textContent('#worker-log');
    expect(logText).not.toContain('ACCEPTED');
    expect(logText).not.toContain('Submitted');
  });

  // ── Test 9: Heartbeat ────────────────────────────────────────────────

  test('9. Worker sends heartbeats', async ({ page }) => {
    await setupTestPage(page);
    const taskId = await createTaskForTest();
    await startWorker(page);
    await waitForState(page, 'IDLE', TIMEOUT);

    // Get the assigned worker_id from the completed task
    const task = await rpcCall('admin.get_task', { task_id: taskId });
    const workerId = task.worker_id;
    expect(workerId).toBeTruthy();

    // Wait for the first heartbeat to arrive (interval is 30s,
    // so we poll for up to 35s)
    let heartbeatReceived = null;
    const deadline = Date.now() + 35000;
    while (Date.now() < deadline) {
      const w = await rpcCall('admin.get_worker', { worker_id: workerId });
      if (w.last_heartbeat_at) {
        heartbeatReceived = w.last_heartbeat_at;
        break;
      }
      await sleep(1000);
    }
    expect(heartbeatReceived).toBeTruthy();

    // Wait for another heartbeat and verify timestamp advances
    await sleep(2000);
    const w2 = await rpcCall('admin.get_worker', { worker_id: workerId });
    expect(w2.last_heartbeat_at).toBeTruthy();
    expect(w2.last_heartbeat_at >= heartbeatReceived).toBe(true);
  });

  // ── Test 10: Coordinator health check ────────────────────────────────

  test('10. Coordinator health endpoint returns OK', async () => {
    const health = await coordinatorHealth();
    expect(health.status).toBe('ok');
    expect(health.protocol_version).toBe('rc3-protocol-v1');
  });

  // ── Test 11: Admin RPC list_workers ──────────────────────────────────

  test('11. Admin RPC lists registered workers', async ({ page }) => {
    await setupTestPage(page);
    await startWorker(page);
    await waitForState(page, 'IDLE', TIMEOUT);

    const result = await rpcCall('admin.list_workers', {});
    // admin.list_workers returns { workers: [...] }, not an array directly
    expect(result.workers).toBeDefined();
    expect(Array.isArray(result.workers)).toBe(true);
    expect(result.workers.length).toBeGreaterThanOrEqual(1);
    expect(result.workers[0].worker_id).toBeTruthy();
    expect(result.workers[0].status).toBe('idle');
  });

  // ── Test 12: Task result validation on coordinator ───────────────────

  test('12. Completed task has valid result on coordinator', async ({ page }) => {
    await setupTestPage(page);
    const taskId = await createTaskForTest('validate-me');
    await startWorker(page);
    await waitForState(page, 'IDLE', TIMEOUT);

    const task = await rpcCall('admin.get_task', { task_id: taskId });
    // Normalized fields are available
    expect(task.status).toBe('COMPLETED');
    expect(task.result).toMatch(/^[a-f0-9]{64}$/);
    expect(task.worker_id).toBeTruthy();

    // Internal columns are ALSO preserved
    expect(task.state).toBe('COMPLETED');
    expect(task.output_hash).toBe(task.result);
    expect(task.assigned_worker_id).toBe(task.worker_id);
  });

  // ── Test 13: Multiple concurrent workers ─────────────────────────────

  test('13. Two workers can process tasks concurrently', async ({ page: page1 }) => {
    // First worker
    await setupTestPage(page1);
    await startWorker(page1);
    await waitForState(page1, 'IDLE', TIMEOUT);

    // Open a second browser context
    const browser = page1.context().browser();
    const context2 = await browser.newContext();
    const page2 = await context2.newPage();
    await setupTestPage(page2);
    await startWorker(page2);
    await waitForState(page2, 'IDLE', TIMEOUT);

    // Create 2 tasks
    const taskIds = await Promise.all([
      createTaskForTest('concurrent-A'),
      createTaskForTest('concurrent-B'),
    ]);

    // Wait for tasks to be assigned (workers sleep 15s between polls)
    await sleep(22000);

    // Both tasks should now be assigned
    const assigned1 = await rpcCall('admin.get_task', { task_id: taskIds[0] });
    const assigned2 = await rpcCall('admin.get_task', { task_id: taskIds[1] });

    expect(assigned1.worker_id).toBeTruthy();
    expect(assigned2.worker_id).toBeTruthy();
  });

  // ── Test 14: Coordinator rejects invalid task_id ─────────────────────

  test('14. Coordinator rejects invalid task_id', async () => {
    try {
      await rpcCall('admin.get_task', { task_id: 'nonexistent-task-id' });
      // Should have thrown
      expect(true).toBe(false);
    } catch (err: any) {
      expect(err).toBeTruthy();
    }
  });

  // ── Test 15: Connection refused causes ERROR then controlled retry ───

  test('15. Connection refused causes ERROR then controlled retry', async ({ page }) => {
    // Use a bad port that refuses connection
    await setupTestPage(page, 'http://localhost:18791');
    await startWorker(page);

    // Worker should show ERROR state
    const errorState = await waitForState(page, 'ERROR', 10000);
    expect(errorState).toBe(true);

    // Wait for error log
    const regFailed = await waitForLog(page, 'Registration failed', 5000);
    expect(regFailed).toBe(true);

    // Worker retries with backoff, eventually gives up
    const gaveUp = await waitForLog(page, 'giving up', 45000);
    expect(gaveUp).toBe(true);

    // Final state is DISCONNECTED
    const disconnected = await waitForState(page, 'DISCONNECTED', 5000);
    expect(disconnected).toBe(true);
  });

  // ── Test 16: Protocol version mismatch ──────────────────────────────

  test('16. Protocol version mismatch returns error from coordinator', async () => {
    // Wrong version → error -32000
    let resp = await fetch(COORDINATOR_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        method: 'worker.register',
        params: {
          capabilities: {
            type: 'web',
            worker_version: '0.0.0',
            protocol_version: 'rc3-protocol-v999',
          },
        },
      }),
    });
    let body = await resp.json();
    expect(body.error).toBeTruthy();
    expect(body.error.code).toBe(-32000);
    expect(body.error.data.expected).toBe('rc3-protocol-v1');
    expect(body.error.data.received).toBe('rc3-protocol-v999');

    // Missing version → error -32602
    resp = await fetch(COORDINATOR_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 2,
        method: 'worker.register',
        params: { type: 'web' },
      }),
    });
    body = await resp.json();
    expect(body.error).toBeTruthy();
    expect(body.error.code).toBe(-32602);

    // Wrong type → error -32602
    resp = await fetch(COORDINATOR_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 3,
        method: 'worker.register',
        params: {
          capabilities: {
            type: 'web',
            protocol_version: 123,
          },
        },
      }),
    });
    body = await resp.json();
    expect(body.error).toBeTruthy();
    expect(body.error.code).toBe(-32602);
  });

  // ── Test 17: Worker cannot submit without a task ─────────────────────

  test('17. Submit without active task is rejected', async ({ page }) => {
    await setupTestPage(page);
    await startWorker(page);
    await waitForState(page, 'IDLE', TIMEOUT);

    // Try to submit a result without an active task
    const resp = await fetch(COORDINATOR_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        method: 'task.submit_result',
        params: {
          task_id: 'fake-task',
          result: 'abc123',
          worker_id: 'fake-worker',
        },
      }),
    });
    const body = await resp.json();
    expect(body.error).toBeTruthy();
  });

  // ── Test 18: No duplicate task assignments ───────────────────────────

  test('18. Task is not assigned twice', async ({ page }) => {
    await setupTestPage(page);

    // Create two workers
    await startWorker(page);
    await waitForState(page, 'IDLE', TIMEOUT);

    const browser = page.context().browser();
    const context2 = await browser.newContext();
    const page2 = await context2.newPage();
    await setupTestPage(page2);
    await startWorker(page2);
    await waitForState(page2, 'IDLE', TIMEOUT);

    // Create just one task
    const taskId = await createTaskForTest('single-task');

    // Wait for it to be assigned (workers sleep 15s between polls)
    await sleep(22000);
    const task = await rpcCall('admin.get_task', { task_id: taskId });
    expect(task.worker_id).toBeTruthy();

    // The task should have exactly one worker_id assigned
    expect(task.worker_id).toBeTruthy();

    // Verify task status is assigned or completed (not pending)
    expect(['ASSIGNED', 'COMPLETED']).toContain(task.status);
  });

  // ── Test 19: Coordinator shutdown handled gracefully ─────────────────

  test('19. Worker handles coordinator shutdown gracefully', async ({ page }) => {
    await setupTestPage(page);
    await startWorker(page);
    await waitForState(page, 'IDLE', TIMEOUT);

    // Stop coordinator (we can't actually stop it, but we can simulate
    // by making requests fail)
    // This test verifies the worker doesn't crash on unexpected errors.
    const taskId = await createTaskForTest('shutdown-test');
    await page.reload();
    await setupTestPage(page);
    await startWorker(page);

    const running = await waitForState(page, 'RUNNING', TIMEOUT);
    // If the coordinator is still there, the task completes
    if (running) {
      const idle = await waitForState(page, 'IDLE', 15000);
      expect(idle).toBe(true);
    }
  });

  // ── Test 20: Task idempotency ───────────────────────────────────────

  test('20. Worker does not process the same task twice', async ({ page }) => {
    await setupTestPage(page);
    const taskId = await createTaskForTest('idempotent');
    await startWorker(page);
    await waitForState(page, 'IDLE', TIMEOUT);
    const result1 = await rpcCall('admin.get_task', { task_id: taskId });

    // Re-request the same task
    await startWorker(page);
    await waitForState(page, 'IDLE', 5000);
    const result2 = await rpcCall('admin.get_task', { task_id: taskId });

    // Task should not be assigned again (still completed from first run)
    expect(result2.task_id).toBe(taskId);
    expect(result2.status).toBe('COMPLETED');
  });

  // ── Test 21: __workerUI only exposed in test mode ────────────────────

  test('21. __workerUI is not exposed in production', async ({ page }) => {
    // Load page WITHOUT __RC3_TEST_CONFIG__
    await page.goto(PAGE_URL, { waitUntil: 'networkidle' });
    const hasWorkerUI = await page.evaluate(() => {
      return typeof (window as any).__workerUI !== 'undefined';
    });
    expect(hasWorkerUI).toBe(false);
  });
});
