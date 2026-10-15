import { useState } from "react";
import { useParams } from "react-router-dom";
import { fmt } from "../api";
import { useGetBatchCallbacksQuery, useGetBatchQuery, useGetBatchResultsQuery } from "../store/gatewayApi";
import { Badge, ErrorNote, Stat, statusTone } from "../components/ui";

export default function BatchDetail() {
  const { batchId = "" } = useParams();
  const [cursor, setCursor] = useState<string | undefined>();
  const [cursors, setCursors] = useState<string[]>([]);
  const batch = useGetBatchQuery(batchId, { pollingInterval: 1000 });
  const callbacks = useGetBatchCallbacksQuery(batchId, { pollingInterval: 1000 });
  const results = useGetBatchResultsQuery(
    { id: batchId, cursor },
    { pollingInterval: batch.data?.status === "processing" ? 2000 : 0 },
  );
  const b = batch.data;
  const done = b ? b.succeeded + b.failed + b.expired + b.rejected : 0;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">
        Batch <span className="font-mono text-base">{batchId}</span>
      </h1>
      <ErrorNote error={batch.error} />
      {b && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
            <Stat label="Status" value={<Badge tone={statusTone(b.status)}>{b.status}</Badge>} />
            <Stat label="Total" value={fmt(b.total)} />
            <Stat label="Succeeded" value={fmt(b.succeeded)} tone="good" />
            <Stat label="Failed" value={fmt(b.failed)} tone={b.failed ? "bad" : undefined} />
            <Stat label="Expired / rejected" value={`${fmt(b.expired)} / ${fmt(b.rejected)}`} />
            <Stat label="Callback" value={<Badge tone={statusTone(b.callback_status)}>{b.callback_status}</Badge>} />
          </div>
          <div className="card">
            <div className="mb-1 flex justify-between text-xs text-slate-500">
              <span>progress</span>
              <span>
                {fmt(done)} / {fmt(b.total)} final
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded bg-slate-200">
              <div className="h-2 bg-emerald-500 transition-all" style={{ width: `${(100 * done) / Math.max(1, b.total)}%` }} />
            </div>
            {b.completed_at && (
              <div className="mt-2 text-xs text-slate-500">
                completed in {((new Date(b.completed_at).getTime() - new Date(b.created_at).getTime()) / 1000).toFixed(1)} s
              </div>
            )}
          </div>
        </>
      )}

      <div className="card">
        <h2 className="mb-2 font-semibold">Callback delivery attempts</h2>
        {callbacks.data?.length ? (
          <table className="min-w-full text-sm">
            <thead>
              <tr>
                {["#", "Sent", "HTTP", "Error", "Took", "Next retry"].map((h) => (
                  <th key={h} className="th">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {callbacks.data.map((a) => (
                <tr key={a.attempt_no}>
                  <td className="td">{a.attempt_no}</td>
                  <td className="td">{new Date(a.sent_at).toLocaleTimeString()}</td>
                  <td className="td">
                    <Badge tone={a.status_code && a.status_code < 300 ? "good" : "bad"}>{a.status_code ?? "—"}</Badge>
                  </td>
                  <td className="td text-xs">{a.error ?? ""}</td>
                  <td className="td">{fmt(a.duration_ms)} ms</td>
                  <td className="td text-xs">{a.next_retry_at ? new Date(a.next_retry_at).toLocaleTimeString() : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="text-sm text-slate-500">No attempts yet (sent once every request is final).</div>
        )}
      </div>

      <div className="card overflow-x-auto">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-semibold">Results</h2>
          <div className="flex gap-2">
            <button
              className="btn-secondary"
              disabled={!cursors.length}
              onClick={() => {
                const prev = [...cursors];
                prev.pop();
                setCursors(prev);
                setCursor(prev.at(-1));
              }}
            >
              Previous
            </button>
            <button
              className="btn-secondary"
              disabled={!results.data?.next_cursor}
              onClick={() => {
                const next = results.data!.next_cursor!;
                setCursors([...cursors, next]);
                setCursor(next);
              }}
            >
              Next
            </button>
          </div>
        </div>
        {results.isLoading && <div className="text-sm text-slate-500">Loading…</div>}
        <table className="min-w-full text-sm">
          <thead>
            <tr>
              {["Request", "Client id", "Model", "State", "Attempts", "Latency", "Error"].map((h) => (
                <th key={h} className="th">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {results.data?.items.map((r) => (
              <tr key={r.request_id}>
                <td className="td font-mono text-xs">{r.request_id.slice(0, 14)}…</td>
                <td className="td text-xs">{r.client_request_id}</td>
                <td className="td">{r.model}</td>
                <td className="td">
                  <Badge tone={statusTone(r.state)}>{r.state}</Badge>
                </td>
                <td className="td">{r.attempts}</td>
                <td className="td tabular-nums">{fmt(r.latency_ms)} ms</td>
                <td className="td text-xs text-slate-500">{r.error ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
