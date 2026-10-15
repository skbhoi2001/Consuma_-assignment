import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export type Point = { t: number; observed: number; limit: number };

/** Observed rolling-60s usage (solid) against the configured limit (dashed). */
export function LimitChart({ data, label, height = 220 }: { data: Point[]; label: string; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="t" tickFormatter={(v) => `${Math.round(v)}s`} type="number" domain={["dataMin", "dataMax"]} fontSize={11} />
        <YAxis fontSize={11} tickFormatter={(v) => (v >= 1e6 ? `${v / 1e6}M` : v >= 1e3 ? `${v / 1e3}k` : v)} />
        <Tooltip formatter={(v: number) => v.toLocaleString()} labelFormatter={(v) => `t = ${Math.round(Number(v))} s`} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line type="monotone" dataKey="observed" name={`${label} observed (60 s)`} stroke="#2563eb" dot={false} isAnimationActive={false} strokeWidth={2} />
        <Line type="stepAfter" dataKey="limit" name="limit" stroke="#dc2626" strokeDasharray="6 4" dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
