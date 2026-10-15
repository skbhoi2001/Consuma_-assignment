import { Link } from "react-router-dom";
import { fmt } from "../api";
import { useGetRunsQuery } from "../store/gatewayApi";
import { Badge, ErrorNote, statusTone } from "../components/ui";

export default function Reports() {
  const { data, error } = useGetRunsQuery(undefined, { pollingInterval: 5000 });
  const finished = data?.filter((r) => r.status !== "running");
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Reports</h1>
      <ErrorNote error={error} />
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {finished?.map((r) => (
          <Link key={r.run_id} to={`/reports/${r.run_id}`} className="card block hover:border-slate-400">
            <div className="flex items-center justify-between">
              <span className="font-semibold">{r.scenario}</span>
              {r.passed === null ? <Badge tone={statusTone(r.status)}>{r.status}</Badge> : <Badge tone={r.passed ? "good" : "bad"}>{r.passed ? "PASS" : "FAIL"}</Badge>}
            </div>
            <div className="mt-1 font-mono text-xs text-slate-500">{r.run_id}</div>
            <div className="mt-2 text-sm text-slate-600">
              completed {fmt(r.totals?.completed as number | undefined)} · {fmt(r.totals?.completion_rate_per_second_after_warmup as number | undefined, 1)}/s after
              warm-up
            </div>
          </Link>
        ))}
      </div>
      {finished && finished.length === 0 && <div className="text-sm text-slate-500">No finished runs yet.</div>}
    </div>
  );
}
