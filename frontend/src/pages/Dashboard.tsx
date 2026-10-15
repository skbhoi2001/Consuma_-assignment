import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmt } from "../api";
import { Badge, Stat } from "../components/ui";
import { LimitChart } from "../components/LimitChart";
import { useLiveStream } from "../useLiveStream";

export default function Dashboard() {
  const { history, latest, connected } = useLiveStream();
  const acc = latest?.accounting;
  const t0 = history[0]?.ts ?? 0;
  const models = latest ? Object.keys(latest.models) : [];
  const active = models.filter((m) => history.some((h) => (h.models[m]?.rolling_60s_requests ?? 0) > 0 || (h.models[m]?.queued ?? 0) > 0));
  const shown = active.length ? active : models;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Live dashboard</h1>
        <Badge tone={connected ? "good" : "bad"}>{connected ? "live" : "disconnected"}</Badge>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
        <Stat label="Submitted" value={fmt(acc?.submitted)} />
        <Stat label="Completed" value={fmt(acc?.completed)} />
        <Stat label="Succeeded" value={fmt(acc?.succeeded)} tone="good" />
        <Stat label="Failed" value={fmt(acc?.failed)} tone={acc?.failed ? "bad" : undefined} />
        <Stat label="Waiting" value={fmt(acc?.waiting)} />
        <Stat label="Expired / rejected" value={`${fmt(acc?.expired)} / ${fmt(acc?.rejected)}`} />
        <Stat
          label="Accounting"
          value={acc ? (acc.balanced ? "balanced" : "mismatch") : "—"}
          tone={acc ? (acc.balanced ? "good" : "bad") : undefined}
          hint="submitted = completed + waiting + expired + rejected"
        />
      </div>

      {shown.map((name) => {
        const m = latest!.models[name];
        const rpmSeries = history.map((h) => ({ t: h.ts - t0, observed: h.models[name]?.rolling_60s_requests ?? 0, limit: h.models[name]?.configured_rpm ?? 0 }));
        const tpmSeries = history.map((h) => ({ t: h.ts - t0, observed: h.models[name]?.rolling_60s_tokens ?? 0, limit: h.models[name]?.configured_tpm ?? 0 }));
        const latSeries = history.map((h) => ({
          t: h.ts - t0,
          p50: h.models[name]?.latency_p50_ms,
          p95: h.models[name]?.latency_p95_ms,
          p99: h.models[name]?.latency_p99_ms,
        }));
        return (
          <section key={name} className="card space-y-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-lg font-semibold">{name}</h2>
              <div className="flex flex-wrap gap-4 text-sm text-slate-600">
                <span>limit <b>{fmt(m.configured_rpm)}</b> RPM / <b>{fmt(m.configured_tpm)}</b> TPM</span>
                <span>last 60 s <b>{fmt(m.rolling_60s_requests)}</b> req ({fmt((100 * m.rolling_60s_requests) / Math.max(1, m.configured_rpm), 1)}%)</span>
                <span>completed/s <b>{fmt(m.completed_last_second)}</b></span>
                <span>provider call p50/p99 <b>{fmt(m.provider_call_p50_ms)}</b> / <b>{fmt(m.provider_call_p99_ms)}</b> ms</span>
                <span>queued <b>{fmt(m.queued)}</b> · in flight <b>{fmt(m.inflight)}</b> · retry <b>{fmt(m.retry_wait)}</b></span>
              </div>
            </div>
            {m.rolling_60s_requests > m.configured_rpm && (
              <div className="rounded-md bg-amber-50 p-2 text-xs text-amber-800">
                The last 60 s still contain traffic sent before this limit was lowered. Nothing new is sent until that traffic leaves the
                window and usage is below the new limit.
              </div>
            )}
            <div className="grid gap-4 lg:grid-cols-3">
              <div>
                <div className="mb-1 text-xs font-medium text-slate-500">Requests per 60 s vs RPM limit</div>
                <LimitChart data={rpmSeries} label="requests" />
              </div>
              <div>
                <div className="mb-1 text-xs font-medium text-slate-500">Tokens per 60 s vs TPM limit</div>
                <LimitChart data={tpmSeries} label="tokens" />
              </div>
              <div>
                <div className="mb-1 text-xs font-medium text-slate-500">End-to-end latency incl. queueing, last 60 s (ms)</div>
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={latSeries} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(v) => `${Math.round(v)}s`} fontSize={11} />
                    <YAxis fontSize={11} />
                    <Tooltip formatter={(v: number) => `${Math.round(v)} ms`} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Line dataKey="p50" stroke="#16a34a" dot={false} isAnimationActive={false} />
                    <Line dataKey="p95" stroke="#ca8a04" dot={false} isAnimationActive={false} />
                    <Line dataKey="p99" stroke="#dc2626" dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </section>
        );
      })}
      {!latest && <div className="text-sm text-slate-500">Waiting for the gateway stream…</div>}
    </div>
  );
}
