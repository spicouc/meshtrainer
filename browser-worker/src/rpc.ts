/**
 * rpc.ts — JSON-RPC v1 client for RC3 coordinator.
 *
 * Protocol: JSON-RPC 2.0 over HTTP POST
 * Headers: X-Protocol-Version, X-Worker-Id, X-Worker-Token
 *
 * Compatible with coordinator rc3_coordinator.py (RC3.1).
 */

export interface RpcRequest {
  jsonrpc: '2.0';
  method: string;
  params: Record<string, unknown>;
  id: string;
}

export interface RpcError {
  code: number;
  message: string;
}

export interface RpcResponse<T = unknown> {
  jsonrpc: '2.0';
  result?: T;
  error?: RpcError;
  id: string;
}

export interface RpcHeaders {
  'X-Protocol-Version': string;
  'X-Worker-Id'?: string;
  'X-Worker-Token'?: string;
  'Content-Type': 'application/json';
}

export class RpcClient {
  private _protocolVersion: string;
  private _workerId: string | undefined;
  private _workerToken: string | undefined;
  private _requestId = 0;

  constructor(
    private _coordinatorUrl: string,
    opts?: { workerId?: string; workerToken?: string; protocolVersion?: string },
  ) {
    this._protocolVersion = opts?.protocolVersion ?? 'rc3-protocol-v1';
    this._workerId = opts?.workerId;
    this._workerToken = opts?.workerToken;
  }

  setWorkerId(id: string): void {
    this._workerId = id;
  }

  setWorkerToken(token: string): void {
    this._workerToken = token;
  }

  get workerId(): string | undefined {
    return this._workerId;
  }

  async call<T = unknown>(method: string, params: Record<string, unknown>): Promise<RpcResponse<T>> {
    this._requestId++;
    const body: RpcRequest = {
      jsonrpc: '2.0',
      method,
      params,
      id: `r${this._requestId}`,
    };

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Protocol-Version': this._protocolVersion,
    };
    if (this._workerId) headers['X-Worker-Id'] = this._workerId;
    if (this._workerToken) headers['X-Worker-Token'] = this._workerToken;

    const response = await fetch(this._coordinatorUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const text = await response.text().catch(() => '');
      throw new Error(`RPC HTTP ${response.status}: ${text.slice(0, 200)}`);
    }

    const data: RpcResponse<T> = await response.json();
    if (data.error) {
      throw new Error(`RPC error ${data.error.code}: ${data.error.message}`);
    }
    return data;
  }

  /** worker.register */
  async register(capabilities: Record<string, unknown>): Promise<{
    worker_id: string;
    auth_token: string;
    protocol_version: string;
    lease_default_seconds: number;
    heartbeat_interval_seconds: number;
  }> {
    const res = await this.call<{
      worker_id: string;
      auth_token: string;
      protocol_version: string;
      lease_default_seconds: number;
      heartbeat_interval_seconds: number;
    }>('worker.register', { capabilities });
    this._workerId = res.result!.worker_id;
    this._workerToken = res.result!.auth_token;
    return res.result!;
  }

  /** worker.heartbeat */
  async heartbeat(workerId: string, status: string, progressPct?: number): Promise<void> {
    await this.call('worker.heartbeat', {
      worker_id: workerId,
      status,
      ...(progressPct !== undefined ? { progress_pct: progressPct } : {}),
    });
  }

  /** task.request */
  async requestTask(preferredStrategy?: string): Promise<{
    task: TaskAssignment | null;
    retry_after_seconds?: number;
  }> {
    const res = await this.call<{
      task: TaskAssignment | null;
      retry_after_seconds?: number;
    }>('task.request', {
      worker_id: this._workerId!,
      ...(preferredStrategy ? { preferred_strategy: preferredStrategy } : {}),
    });
    return res.result!;
  }

  /** task.submit */
  async submitResult(
    taskId: string,
    workerId: string,
    status: string,
    outputHash?: string,
    executionMetadata?: Record<string, unknown>,
  ): Promise<{
    status: string;
    validated_ok: boolean;
    validation_details: Record<string, unknown>;
    next_action: string;
  }> {
    const res = await this.call<{
      status: string;
      validated_ok: boolean;
      validation_details: Record<string, unknown>;
      next_action: string;
    }>('task.submit', {
      task_id: taskId,
      worker_id: workerId,
      status,
      ...(outputHash ? { output_hash: outputHash } : {}),
      ...(executionMetadata ? { execution_metadata: executionMetadata } : {}),
    });
    return res.result!;
  }
}

export interface TaskAssignment {
  task_id: string;
  run_id: string;
  protocol_version: string;
  strategy: string;
  config_hash?: string;
  input_hash?: string;
  lease_expires_at: string;
  expected_output: string[];
}
