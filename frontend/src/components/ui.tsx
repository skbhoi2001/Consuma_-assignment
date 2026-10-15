import type { ReactNode } from "react";

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: string; tone?: "good" | "bad" }) {
  const color = tone === "good" ? "text-emerald-600" : tone === "bad" ? "text-rose-600" : "text-slate-900";
  return (
    <div className="card">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "good" | "bad" | "warn" | "neutral" }) {
  const styles = {
    good: "bg-emerald-100 text-emerald-800",
    bad: "bg-rose-100 text-rose-800",
    warn: "bg-amber-100 text-amber-800",
    neutral: "bg-slate-100 text-slate-700",
  }[tone];
  return <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${styles}`}>{children}</span>;
}

export function statusTone(status: string): "good" | "bad" | "warn" | "neutral" {
  if (["delivered", "completed", "finished", "succeeded"].includes(status)) return "good";
  if (["dead_lettered", "failed", "aborted", "completed_with_failures", "expired", "rejected"].includes(status)) return "bad";
  if (["processing", "running", "delivering", "pending", "queued", "starting"].includes(status)) return "warn";
  return "neutral";
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="rounded-md bg-rose-50 p-3 text-sm text-rose-700">{String((error as Error).message ?? error)}</div>;
}
