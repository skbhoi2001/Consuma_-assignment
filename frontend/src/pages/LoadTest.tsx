import { useState } from "react";
import { Link } from "react-router-dom";
import { fmt } from "../api";
import { useGetRunsQuery, useStartRunMutation, useStopRunMutation } from "../store/gatewayApi";
import { Badge, ErrorNote, statusTone } from "../components/ui";

const PRESETS = [
  { id: "s1", title: "Scenario 1 — reach capacity", text: "1 model at 50k RPM / 100M TPM, offered 60k RPM for 6 min. ~8–10 min." },
  { id: "s2", title: "Scenario 2 — changing limits", text: "A 30k + B 20k RPM; A cut to 5k at 2:00, restored at 4:30. ~9–11 min." },
  { id: "s3", title: "Scenario 3 — async batch", text: "10,000 requests, 2 models, failures; callback rejects first 2 attempts. ~1 min." },
];

export default function LoadTest() {
  const runs = useGetRunsQuery(undefined, { pollingInterval: 2000 });
  const [custom, setCustom] = useState({ rate: "300", duration: "60", split: "60", batch: "20", tokens: "1000", callback: false });
  const [startRun, start] = useStartRunMutation();
  const [stopRun] = useStopRunMutation();
  const running = runs.data?.find((r) => r.status === "running");

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Load test</h1>
      <p className="text-sm text-slate-600">
        Starts the load generator as a separate process. Watch it live on the Dashboard; the report appears under Reports when it finishes.
        One run at a time.
      </p>
      <ErrorNote error={start.error} />
      <div className="grid gap-4 md:grid-cols-3">
        {PRESETS.map((p) => (
          <div key={p.id} className="card flex flex-col justify-between gap-3">
            <div>
              <h2 className="font-semibold">{p.title}</h2>
              <p className="mt-1 text-sm text-slate-600">{p.text}</p>
            </div>
            <button className="btn self-start" disabled={!!running || start.isLoading} onClick={() => startRun({ scenario: p.id })}>
              Run {p.id}
            </button>
          </div>
        ))}
      </div>

      <div className="card space-y-3">
        <h2 className="font-semibold">Custom run</h2>
        <div className="flex flex-wrap items-end gap-3 text-sm">
          {(
            [
              ["rate", "Requests / s"],
              ["duration", "Duration (s)"],
              ["split", "% model-a"],
              ["batch", "Batch size"],
              ["tokens", "Tokens / request"],
            ] as const
          ).map(([key, label]) => (
            <label key={key} className="space-y-1">
              <div className="text-xs text-slate-500">{label}</div>
              <input className="input w-28" value={custom[key]} onChange={(e) => setCustom({ ...custom, [key]: e.target.value })} />
            </label>
          ))}
          <label className="flex items-center gap-2 pb-1">
            <input type="checkbox" checked={custom.callback} onChange={(e) => setCustom({ ...custom, callback: e.target.checked })} />
            <span className="text-xs text-slate-600">callbacks</span>
          </label>
          <button
            className="btn"
            disabled={!!running || start.isLoading}
            onClick={() => {
              const a = Number(custom.split) / 100;
              startRun({
                scenario: "custom",
                rate: Number(custom.rate),
                duration_seconds: Number(custom.duration),
                models: { "model-a": Math.max(a, 0.001), "model-b": Math.max(1 - a, 0.001) },
                batch_size: Number(custom.batch),
                tokens: Number(custom.tokens),
                callback: custom.callback,
              });
            }}
          >
            Start
          </button>
        </div>
      </div>

      <div className="card overflow-x-auto !p-0">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="bg-slate-50">
            <tr>
              {["Run", "Scenario", "Status", "Result", "Completed", "Started", ""].map((h) => (
                <th key={h} className="th">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {runs.data?.map((r) => (
              <tr key={r.run_id}>
                <td className="td font-mono text-xs">
                  <Link className="text-blue-700 hover:underline" to={`/reports/${r.run_id}`}>
                    {r.run_id}
                  </Link>
                </td>
                <td className="td">{r.scenario}</td>
                <td className="td">
                  <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                </td>
                <td className="td">{r.passed === null ? "—" : <Badge tone={r.passed ? "good" : "bad"}>{r.passed ? "PASS" : "FAIL"}</Badge>}</td>
                <td className="td tabular-nums">{fmt(r.totals?.completed as number | undefined)}</td>
                <td className="td text-xs text-slate-500">{r.started_at ? new Date(r.started_at).toLocaleString() : ""}</td>
                <td className="td">
                  {r.stoppable && (
                    <button className="btn-secondary" onClick={() => stopRun(r.run_id)}>
                      Stop
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
