import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { fmt } from "../api";
import { useGetBatchesQuery, useSubmitBatchMutation } from "../store/gatewayApi";
import { Badge, ErrorNote, statusTone } from "../components/ui";

function SubmitBatch() {
  const navigate = useNavigate();
  const [size, setSize] = useState("1000");
  const [split, setSplit] = useState("50");
  const [callback, setCallback] = useState("http://localhost:9100/callbacks/receive");
  const [submitBatch, submit] = useSubmitBatchMutation();
  const onSubmit = () => {
    const n = Number(size);
    const aShare = Number(split) / 100;
    const requests = Array.from({ length: n }, (_, i) => ({
      model: i < n * aShare ? "model-a" : "model-b",
      estimated_tokens: 1000,
      request_id: `ui-${i}`,
    }));
    submitBatch({ requests, ...(callback ? { callback_url: callback } : {}) })
      .unwrap()
      .then((data) => navigate(`/batches/${data.batch_id}`))
      .catch(() => undefined); // shown by <ErrorNote error={submit.error} />
  };
  return (
    <div className="card space-y-3">
      <h2 className="font-semibold">Submit a batch</h2>
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="space-y-1">
          <div className="text-xs text-slate-500">Requests</div>
          <input className="input w-28" value={size} onChange={(e) => setSize(e.target.value)} />
        </label>
        <label className="space-y-1">
          <div className="text-xs text-slate-500">% to model-a</div>
          <input className="input w-20" value={split} onChange={(e) => setSplit(e.target.value)} />
        </label>
        <label className="flex-1 space-y-1">
          <div className="text-xs text-slate-500">Callback URL (optional)</div>
          <input className="input w-full" value={callback} onChange={(e) => setCallback(e.target.value)} />
        </label>
        <button className="btn" disabled={submit.isLoading} onClick={onSubmit}>
          Submit
        </button>
      </div>
      <ErrorNote error={submit.error} />
    </div>
  );
}

export default function Batches() {
  const { data, error } = useGetBatchesQuery(undefined, { pollingInterval: 2000 });
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Batches</h1>
      <SubmitBatch />
      <ErrorNote error={error} />
      <div className="card overflow-x-auto !p-0">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="bg-slate-50">
            <tr>
              {["Batch", "Status", "Progress", "Succeeded", "Failed", "Expired / rejected", "Callback", "Created"].map((h) => (
                <th key={h} className="th">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data?.map((b) => {
              const done = b.succeeded + b.failed + b.expired + b.rejected;
              return (
                <tr key={b.batch_id} className="hover:bg-slate-50">
                  <td className="td font-mono text-xs">
                    <Link className="text-blue-700 hover:underline" to={`/batches/${b.batch_id}`}>
                      {b.batch_id.slice(0, 16)}…
                    </Link>
                  </td>
                  <td className="td">
                    <Badge tone={statusTone(b.status)}>{b.status}</Badge>
                  </td>
                  <td className="td tabular-nums">
                    {fmt(done)} / {fmt(b.total)}
                  </td>
                  <td className="td tabular-nums">{fmt(b.succeeded)}</td>
                  <td className="td tabular-nums">{fmt(b.failed)}</td>
                  <td className="td tabular-nums">
                    {fmt(b.expired)} / {fmt(b.rejected)}
                  </td>
                  <td className="td">
                    <Badge tone={statusTone(b.callback_status)}>{b.callback_status}</Badge>
                  </td>
                  <td className="td text-xs text-slate-500">{new Date(b.created_at).toLocaleTimeString()}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {data && data.length === 0 && <div className="p-4 text-sm text-slate-500">No batches yet.</div>}
      </div>
    </div>
  );
}
