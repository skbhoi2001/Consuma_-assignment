import { useParams } from "react-router-dom";
import { fmt, type RunDetail } from "../api";
import { gatewayApi, useGetRunQuery } from "../store/gatewayApi";
import { Badge, ErrorNote, Stat, statusTone } from "../components/ui";
import { LimitChart, type Point } from "../components/LimitChart";

function rollingSeries(rows: NonNullable<RunDetail["timeseries"]>, model: string, field: "requests" | "tokens") {
  const s = rows.filter((r) => r.model === model);
  const t0 = s[0]?.ts ?? 0;
  const out: Point[] = [];
  let acc = 0;
  s.forEach((r, i) => {
    acc += r[field];
    if (i >= 60) acc -= s[i - 60][field];
    out.push({ t: r.ts - t0, observed: acc, limit: field === "requests" ? r.configured_rpm : r.configured_tpm });
  });
  return out;
}

function measured(m: unknown): string {
  if (typeof m === "string") return m;
  if (m && typeof m === "object") return Object.entries(m).map(([k, v]) => `${k}=${typeof v === "number" ? v.toLocaleString() : String(v)}`).join(", ");
  return String(m);
}

export default function ReportDetail() {
  const { runId = "" } = useParams();
  // Poll only while the run is still going: peek at the cached status without subscribing.
  const { data: cached } = gatewayApi.endpoints.getRun.useQueryState(runId);
  const { data, error } = useGetRunQuery(runId, { pollingInterval: cached?.status === "running" ? 3000 : 0 });
  const s = data?.summary;
  const rows = data?.timeseries ?? [];
  const models = [...new Set(rows.map((r) => r.model))].filter((m) => rows.some((r) => r.model === m && r.requests > 0));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Report {runId}</h1>
        {data && <Badge tone={statusTone(data.status)}>{data.status}</Badge>}
        {s && <Badge tone={s.passed ? "good" : "bad"}>{s.passed ? "PASS" : "FAIL"}</Badge>}
      </div>
      <ErrorNote error={error} />
      {!data && !error && <div className="text-sm text-slate-500">Loading…</div>}
      {data?.status === "running" && <div className="text-sm text-slate-600">Still running — follow it on the Dashboard.</div>}
      {s && (
        <>
          <p className="text-sm text-slate-600">{s.description}</p>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-7">
            <Stat label="Submitted" value={fmt(s.totals.submitted as number)} />
            <Stat label="Completed" value={fmt(s.totals.completed as number)} />
            <Stat label="Succeeded / failed" value={`${fmt(s.totals.succeeded as number)} / ${fmt(s.totals.failed as number)}`} />
            <Stat label="Expired / rejected" value={`${fmt(s.totals.expired as number)} / ${fmt(s.totals.rejected as number)}`} />
            <Stat label="Completed / s (after warm-up)" value={fmt(s.totals.completion_rate_per_second_after_warmup as number, 1)} />
            <Stat
              label="Latency p50 / p99 (ms)"
              value={`${fmt(s.latency_ms.end_to_end_after_warmup?.p50)} / ${fmt(s.latency_ms.end_to_end_after_warmup?.p99)}`}
              hint="end-to-end, includes queueing"
            />
            <Stat
              label="Provider call p50 / p99 (ms)"
              value={`${fmt(s.latency_ms.provider_call_after_warmup?.p50)} / ${fmt(s.latency_ms.provider_call_after_warmup?.p99)}`}
              hint="the provider call alone"
            />
          </div>

          <div className="card overflow-x-auto">
            <h2 className="mb-2 font-semibold">Checks</h2>
            <table className="min-w-full text-sm">
              <thead>
                <tr>
                  {["Check", "Result", "Measured", "Expected"].map((h) => (
                    <th key={h} className="th">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {s.checks.map((c) => (
                  <tr key={c.name} className="align-top">
                    <td className="td font-medium">{c.name}</td>
                    <td className="td">
                      <Badge tone={c.passed ? "good" : "bad"}>{c.passed ? "PASS" : "FAIL"}</Badge>
                    </td>
                    <td className="td text-xs text-slate-600">{measured(c.measured)}</td>
                    <td className="td text-xs text-slate-600">{c.expected}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {models.map((m) => (
            <div key={m} className="card grid gap-4 lg:grid-cols-2">
              <div>
                <h3 className="mb-1 text-sm font-semibold">{m}: requests per 60 s vs RPM limit</h3>
                <LimitChart data={rollingSeries(rows, m, "requests")} label="requests" />
              </div>
              <div>
                <h3 className="mb-1 text-sm font-semibold">{m}: tokens per 60 s vs TPM limit</h3>
                <LimitChart data={rollingSeries(rows, m, "tokens")} label="tokens" />
              </div>
            </div>
          ))}
          {data?.report_dir && (
            <div className="text-xs text-slate-500">
              Files: <span className="font-mono">{data.report_dir}/report.md</span>, report.json, throughput.png
            </div>
          )}
        </>
      )}
    </div>
  );
}
