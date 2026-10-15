"""Store runs in Postgres (runs, run_metrics, run_results) so the UI can list them."""

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from backend.db.session import dispose_engine, get_sessionmaker
from backend.db.tables import Run, RunMetric, RunResult


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


async def save_started(run_id: str, scenario: str, config: dict, machine: dict) -> None:
    async with get_sessionmaker()() as s, s.begin():
        await s.execute(
            insert(Run)
            .values(
                run_id=run_id,
                scenario=scenario,
                status="running",
                config=config,
                machine_info=machine,
            )
            .on_conflict_do_nothing(index_elements=["run_id"])
        )


async def save_finished(report: dict, report_dir: str) -> None:
    summary = {k: v for k, v in report.items() if k not in ("timeseries", "config", "environment")}
    async with get_sessionmaker()() as s, s.begin():
        run = await s.get(Run, report["run_id"])
        if run is None:
            run = Run(run_id=report["run_id"], scenario=report["scenario"], config=report["config"])
            s.add(run)
        run.status = report["status"]
        run.ended_at = datetime.fromisoformat(report["ended_at"])
        run.machine_info = report["environment"]
        run.summary = summary
        run.report_dir = report_dir
        await s.flush()
        rows = [
            {
                "run_id": report["run_id"],
                "ts_second": _dt(r["ts"]),
                "model": r["model"],
                "requests": r["dispatched"],
                "tokens": r["tokens"],
                "succeeded": r["succeeded"],
                "failed": r["failed"],
                "configured_rpm": r["configured_rpm"],
                "configured_tpm": r["configured_tpm"],
            }
            for r in report["timeseries"]
        ]
        for i in range(0, len(rows), 2000):
            await s.execute(insert(RunMetric).values(rows[i : i + 2000]).on_conflict_do_nothing())
        for c in report["checks"]:
            await s.execute(
                insert(RunResult)
                .values(
                    run_id=report["run_id"],
                    check_name=c["name"][:64],
                    passed=c["passed"],
                    measured=str(c["measured"]),
                    expected=str(c["expected"]),
                )
                .on_conflict_do_nothing()
            )
    await dispose_engine()
