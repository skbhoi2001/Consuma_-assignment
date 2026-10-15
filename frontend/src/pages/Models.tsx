import { useState } from "react";
import { fmt, type ModelInfo } from "../api";
import { useGetLimitHistoryQuery, useGetModelsQuery, useUpdateLimitsMutation } from "../store/gatewayApi";
import { ErrorNote } from "../components/ui";

function LimitEditor({ model }: { model: ModelInfo }) {
  const [rpm, setRpm] = useState(String(model.rpm));
  const [tpm, setTpm] = useState(String(model.tpm));
  const [updateLimits, save] = useUpdateLimitsMutation();
  const presets = [5_000, 20_000, 30_000, 50_000];
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-xs text-slate-500">RPM</label>
        <input className="input w-28" value={rpm} onChange={(e) => setRpm(e.target.value)} inputMode="numeric" />
        <label className="text-xs text-slate-500">TPM</label>
        <input className="input w-36" value={tpm} onChange={(e) => setTpm(e.target.value)} inputMode="numeric" />
        <button className="btn" disabled={save.isLoading} onClick={() => updateLimits({ model: model.name, rpm: Number(rpm), tpm: Number(tpm) })}>
          Apply live
        </button>
      </div>
      <div className="flex flex-wrap gap-1">
        {presets.map((p) => (
          <button key={p} className="btn-secondary !px-2 !py-0.5 text-xs" onClick={() => setRpm(String(p))}>
            {fmt(p)} RPM
          </button>
        ))}
      </div>
      {save.data && (
        <div className="text-xs text-emerald-700">
          Applied: {fmt(save.data.rpm)} RPM / {fmt(save.data.tpm)} TPM · simulator synced: {String(save.data.simulator_synced)}
        </div>
      )}
      <ErrorNote error={save.error} />
    </div>
  );
}

function History({ name }: { name: string }) {
  const { data } = useGetLimitHistoryQuery(name);
  if (!data?.length) return <div className="text-xs text-slate-500">No changes yet.</div>;
  return (
    <ul className="space-y-0.5 text-xs text-slate-600">
      {data.slice(0, 5).map((c, i) => (
        <li key={i}>
          {new Date(c.changed_at).toLocaleTimeString()} · {c.source} · {fmt(c.old_rpm)} → <b>{fmt(c.new_rpm)}</b> RPM
        </li>
      ))}
    </ul>
  );
}

export default function Models() {
  const { data, error } = useGetModelsQuery(undefined, { pollingInterval: 2000 });
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Models and limits</h1>
      <p className="text-sm text-slate-600">
        Changes apply immediately without a restart. After a decrease, nothing new is sent until the last 60 s of traffic is below the new
        limit. Each change is recorded and pushed to the simulator.
      </p>
      <ErrorNote error={error} />
      <div className="grid gap-4 lg:grid-cols-2">
        {data?.map((m) => (
          <div key={m.name} className="card space-y-3">
            <div className="flex items-baseline justify-between">
              <h2 className="text-lg font-semibold">{m.name}</h2>
              <span className="text-sm text-slate-600">
                {fmt(m.rpm)} RPM · {fmt(m.tpm)} TPM <span className="text-slate-400">(target {fmt(m.effective_rpm)} RPM)</span>
              </span>
            </div>
            <div className="grid grid-cols-4 gap-2 text-sm">
              <div>
                <div className="text-xs text-slate-500">last 60 s</div>
                {fmt(m.rolling_60s_requests)}
              </div>
              <div>
                <div className="text-xs text-slate-500">queued</div>
                {fmt(m.queued)}
              </div>
              <div>
                <div className="text-xs text-slate-500">in flight</div>
                {fmt(m.inflight)}
              </div>
              <div>
                <div className="text-xs text-slate-500">max wait</div>
                {m.max_wait_seconds}s
              </div>
            </div>
            <LimitEditor key={`${m.rpm}-${m.tpm}`} model={m} />
            <History name={m.name} />
          </div>
        ))}
      </div>
    </div>
  );
}
