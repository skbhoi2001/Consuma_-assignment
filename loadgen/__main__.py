"""Load generator CLI.

python -m loadgen run --rate 1000 --duration 60 --models model-a=0.6,model-b=0.4
python -m loadgen scenario scenarios/s1.yaml
python -m loadgen bench --shards 10 --rate 400000 --duration 20
"""

import asyncio
import json
import signal
from datetime import UTC, datetime

import typer

from loadgen.config import LoadConfig, load_scenario
from loadgen.environment import machine_info

app = typer.Typer(add_completion=False, help="Load generator and validation scenarios")


def _parse_models(spec: str) -> dict[str, float]:
    out = {}
    for part in spec.split(","):
        name, _, weight = part.partition("=")
        out[name.strip()] = float(weight or 1)
    return out


async def _execute(cfg: LoadConfig, run_id: str, persist: bool) -> dict:
    from loadgen.report import build_report, write_report
    from loadgen.runner import LoadRunner

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    if persist:
        from loadgen import persist as P

        try:
            await P.save_started(run_id, cfg.scenario, cfg.model_dump(mode="json"), machine_info())
        except Exception as exc:  # the run itself doesn't need the database
            typer.echo(f"warning: could not record run start in Postgres: {exc}", err=True)
            persist = False

    data = await LoadRunner(cfg, stop).run()
    report = build_report(data, run_id)
    out_dir = write_report(report, cfg.results_dir)
    if persist:
        try:
            await P.save_finished(report, str(out_dir))
        except Exception as exc:
            typer.echo(f"warning: could not save run in Postgres: {exc}", err=True)
    report["report_dir"] = str(out_dir)
    return report


def _print(report: dict) -> None:
    t = report["totals"]
    typer.echo(
        f"\n{report['scenario']} {report['run_id']}: {'PASS' if report['passed'] else 'FAIL'}"
    )
    typer.echo(
        f"sent {t['client_sent']:,} | completed {t['completed']:,} (ok {t['succeeded']:,}, "
        f"failed {t['failed']:,}) | expired {t['expired']:,} | rejected {t['rejected']:,} | "
        f"waiting {t['waiting']:,}"
    )
    typer.echo(f"completion rate after warm-up: {t['completion_rate_per_second_after_warmup']:,}/s")
    for label, key in (
        ("end-to-end", "end_to_end_after_warmup"),
        ("provider call", "provider_call_after_warmup"),
    ):
        lat = report["latency_ms"].get(key) or {}
        values = " / ".join(
            f"{lat[k]:,.0f}" if lat.get(k) is not None else "-" for k in ("p50", "p95", "p99")
        )
        typer.echo(f"{label} latency ms p50/p95/p99: {values}")
    for c in report["checks"]:
        verdict = "PASS" if c["passed"] else "FAIL"
        typer.echo(f"  {verdict}  {c['name']}  {json.dumps(c['measured'], default=str)}")
    typer.echo(f"report: {report['report_dir']}/report.md")


def _run_id(scenario: str) -> str:
    return f"{scenario}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"


@app.command()
def run(
    rate: float = typer.Option(..., help="requests per second"),
    duration: float = typer.Option(..., help="seconds of load"),
    models: str = typer.Option("model-a=1", help="model=weight,model=weight"),
    batch_size: int = typer.Option(1),
    tokens: int = typer.Option(1000),
    token_jitter: float = typer.Option(0.0),
    warmup: float = typer.Option(10.0),
    callback: bool = typer.Option(False, help="use an embedded callback receiver"),
    callback_fail_first_n: int = typer.Option(0),
    gateway: str = typer.Option("http://localhost:8000"),
    simulator: str = typer.Option("http://localhost:9000"),
    run_id: str | None = typer.Option(None),
    persist: bool = typer.Option(True, help="record the run in Postgres"),
) -> None:
    """Custom load test."""
    cfg = LoadConfig(
        rate=rate,
        duration_seconds=duration,
        models=_parse_models(models),
        batch_size=batch_size,
        tokens=tokens,
        token_jitter=token_jitter,
        warmup_seconds=warmup,
        callback=callback,
        callback_fail_first_n=callback_fail_first_n,
        gateway_url=gateway,
        simulator_url=simulator,
        checks=["accounting", "limits_respected"] + (["batches"] if callback else []),
    )
    _print(asyncio.run(_execute(cfg, run_id or _run_id("custom"), persist)))


@app.command()
def scenario(
    path: str = typer.Argument(..., help="scenarios/s1.yaml etc."),
    gateway: str | None = typer.Option(None),
    simulator: str | None = typer.Option(None),
    duration: float | None = typer.Option(None, help="override duration (quick try-outs)"),
    run_id: str | None = typer.Option(None),
    persist: bool = typer.Option(True),
) -> None:
    """Run a validation scenario from a YAML file."""
    cfg = load_scenario(
        path, gateway_url=gateway, simulator_url=simulator, duration_seconds=duration
    )
    report = asyncio.run(_execute(cfg, run_id or _run_id(cfg.scenario), persist))
    _print(report)
    raise typer.Exit(0 if report["passed"] else 1)


@app.command()
def bench(
    shards: int = typer.Option(8, help="worker processes"),
    rate: float = typer.Option(400_000, help="total requests per second offered"),
    duration: float = typer.Option(20, help="seconds"),
    rpm: int = typer.Option(30_000_000, help="total RPM limit across shards"),
    tpm: int = typer.Option(30_000_000_000, help="total TPM limit across shards"),
    tokens: int = typer.Option(1000),
    latency_ms: float = typer.Option(200, help="simulated median provider latency"),
    transient: float = typer.Option(0.0),
    permanent: float = typer.Option(0.0),
    results_dir: str = typer.Option("results"),
) -> None:
    """High-throughput benchmark: in-process engine across processes (see backend/bench)."""
    from backend.bench.runner import BenchConfig, run_bench

    cfg = BenchConfig(
        shards=shards,
        rate=rate,
        duration_seconds=duration,
        rpm=rpm,
        tpm=tpm,
        tokens=tokens,
        latency_median_ms=latency_ms,
        transient_error_rate=transient,
        permanent_error_rate=permanent,
    )
    result = run_bench(cfg, results_dir)
    s = result["summary"]
    lat = s["latency_ms"]
    typer.echo(
        f"sustained completed/s: {s['sustained_completed_per_second']:,} (offered {cfg.rate:,.0f})"
    )
    typer.echo(
        f"300k/s met: {s['required_300k_per_second']} | 1M/s met: {s['stretch_1m_per_second']}"
    )
    typer.echo(
        f"completed {s['completed']:,} | rejected {s['rejected']:,} | accounted {s['accounted']}"
    )
    typer.echo(f"latency ms p50/p95/p99: {lat['p50']:.0f} / {lat['p95']:.0f} / {lat['p99']:.0f}")
    typer.echo(f"referee violations: {s['referee_violations']}")
    typer.echo(f"report: {result['report_dir']}/report.md")


if __name__ == "__main__":
    app()
