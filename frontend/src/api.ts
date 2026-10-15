// Typed wrappers around the gateway API (proxied to :8000 by Vite in dev).

export type Accounting = {
  submitted: number;
  succeeded: number;
  failed: number;
  expired: number;
  rejected: number;
  completed: number;
  waiting: number;
  balanced: boolean;
};

export type LiveModel = {
  configured_rpm: number;
  configured_tpm: number;
  rolling_60s_requests: number;
  rolling_60s_tokens: number;
  completed_last_second: number;
  dispatched_last_second: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  latency_p99_ms: number | null;
  provider_call_p50_ms: number | null;
  provider_call_p99_ms: number | null;
  queued: number;
  inflight: number;
  retry_wait: number;
};

export type LiveSnapshot = { ts: number; accounting: Accounting; models: Record<string, LiveModel> };

export type ModelInfo = {
  name: string;
  rpm: number;
  tpm: number;
  effective_rpm: number;
  effective_tpm: number;
  max_queue: number;
  max_wait_seconds: number;
  queued: number;
  inflight: number;
  retry_wait: number;
  rolling_60s_requests: number;
  rolling_60s_tokens: number;
};

export type LimitChange = {
  changed_at: string;
  source: string;
  old_rpm: number | null;
  new_rpm: number;
  old_tpm: number | null;
  new_tpm: number;
};

export type BatchSummary = {
  batch_id: string;
  status: string;
  total: number;
  succeeded: number;
  failed: number;
  expired: number;
  rejected: number;
  pending?: number;
  created_at: string;
  completed_at: string | null;
  callback_status: string;
  results_url?: string;
};

export type CallbackAttempt = {
  attempt_no: number;
  status_code: number | null;
  error: string | null;
  duration_ms: number | null;
  sent_at: string;
  next_retry_at: string | null;
};

export type RequestStatus = {
  request_id: string;
  client_request_id: string | null;
  model: string;
  state: string;
  attempts: number;
  error: string | null;
  latency_ms: number | null;
  finished_at: string | null;
};

export type ResultsPage = { batch_id: string; items: RequestStatus[]; next_cursor: string | null };

export type Check = { name: string; passed: boolean; measured: unknown; expected: string };

export type RunListItem = {
  run_id: string;
  scenario: string;
  status: string;
  stoppable?: boolean;
  started_at: string | null;
  ended_at: string | null;
  passed: boolean | null;
  totals?: Record<string, number | boolean>;
};

export type RunDetail = {
  run_id: string;
  scenario: string;
  status: string;
  started_at?: string;
  ended_at?: string | null;
  report_dir?: string | null;
  summary?: {
    passed: boolean;
    description: string;
    totals: Record<string, number | boolean>;
    latency_ms: Record<string, { p50: number | null; p95: number | null; p99: number | null; count?: number }>;
    models: Record<string, Record<string, unknown>>;
    checks: Check[];
    limit_changes: { ts: number; model: string; rpm: number; tpm: number }[];
  } | null;
  timeseries?: {
    ts: number;
    model: string;
    requests: number;
    tokens: number;
    succeeded: number;
    failed: number;
    configured_rpm: number;
    configured_tpm: number;
  }[];
};

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* not JSON */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  models: () => request<ModelInfo[]>("GET", "/v1/models"),
  updateLimits: (model: string, body: { rpm?: number; tpm?: number }) =>
    request<ModelInfo & { simulator_synced: boolean | null }>("PATCH", `/v1/models/${model}/limits`, body),
  limitHistory: (model: string) => request<LimitChange[]>("GET", `/v1/models/${model}/limits/history`),
  batches: () => request<BatchSummary[]>("GET", "/v1/batches"),
  batch: (id: string) => request<BatchSummary>("GET", `/v1/batches/${id}`),
  batchCallbacks: (id: string) => request<CallbackAttempt[]>("GET", `/v1/batches/${id}/callbacks`),
  batchResults: (id: string, cursor?: string) =>
    request<ResultsPage>("GET", `/v1/batches/${id}/results?limit=100${cursor ? `&cursor=${cursor}` : ""}`),
  submitBatch: (body: unknown) => request<{ batch_id: string }>("POST", "/v1/batches", body),
  runs: () => request<RunListItem[]>("GET", "/v1/runs"),
  run: (id: string) => request<RunDetail>("GET", `/v1/runs/${id}`),
  startRun: (body: unknown) => request<{ run_id: string }>("POST", "/v1/runs", body),
  stopRun: (id: string) => request<unknown>("POST", `/v1/runs/${id}/stop`),
};

export const fmt = (n: number | null | undefined, digits = 0) =>
  n === null || n === undefined ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: digits });
