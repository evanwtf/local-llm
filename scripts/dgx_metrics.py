"""DGX Spark vLLM app-level metrics snapshot from Prometheus, via gcx.

The `DGX Spark` Grafana dashboard draws vLLM's own `vllm:*` series (the server
exposes `/metrics` on :8030 and Prometheus scrapes it). This reads the same
series from the command line so a heartbeat or an issue comment can carry the
app-level numbers -- decode throughput, prefix-cache hit rate, queue depth --
without opening the dashboard. GPU util/power/temp come from `nvidia-smi`
separately (the heartbeat header); this covers the *serving* metrics that
`nvidia-smi` cannot see.

Why gcx and not a direct Prometheus client: the reachable, authenticated door to
these series is Grafana, and `gcx metrics query` is the repo's settled client
for it (see `scripts/mac_dash.py`, `scripts/gpu_utilization.py`). The Python here
is the parse-and-format layer -- tested below against captured gcx JSON -- and
gcx is the transport.

Which metrics, and their caveats:

* **decode (gen) tok/s** -- steady and meaningful at any concurrency. The headline.
* **prefix-cache hit %** -- 5-minute rate; stable enough to quote.
* **running / waiting** -- queue depth; how many streams the server admits vs
  queues (the `max_num_seqs` ceiling shows up here).
* **prefill peak tok/s** -- a windowed max, because prefill is bursty: a spot
  sample lands in a trough between re-prefills and reads near zero.
* **TTFT p50** -- ONLY meaningful under real concurrent load. At concurrency 1
  the histogram is sparse and `histogram_quantile` lands on a high `le` bucket
  boundary (a bogus 48 s), so it is omitted unless `--ttft` is passed.

The LAN is never emitted; only the metric values are meant for the PUBLIC repo.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

#: The Prometheus datasource behind Grafana (shared with the M5 Max; see
#: `scripts/mac_dash.py` and `docs/dgx-spark-runbook.md`).
DATASOURCE = "uMatQbvMk"

#: gcx reads its token from the environment; a non-interactive shell never
#: sources the profile that exports it, so it is read explicitly here (the
#: `grafana-prometheus-via-gcx` gotcha).
TOKEN_PATH = pathlib.Path.home() / ".config/gcx/token"


def _gcx_env() -> dict[str, str]:
    env = dict(os.environ)
    try:
        if TOKEN_PATH.exists():
            env["GRAFANA_TOKEN"] = TOKEN_PATH.read_text().strip()
    except OSError:
        pass
    return env


def query(promql: str) -> str:
    """Raw gcx JSON for one instant query, or `""` if the call fails.

    The subprocess boundary -- the only part that is not unit-tested. Parsing
    lives in `scalar`, which the tests exercise against captured output.
    """
    try:
        out = subprocess.run(
            ["gcx", "metrics", "query", "-d", DATASOURCE, promql, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=_gcx_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout


def scalar(gcx_json: str) -> float | None:
    """The single value out of a gcx instant-query result, or None if empty.

    gcx returns instant vectors under `value` and range/matrix results under
    `values`; accept either so the same parser serves both query shapes.
    """
    try:
        result = json.loads(gcx_json).get("data", {}).get("result", [])
    except (ValueError, AttributeError):
        return None
    if not result:
        return None
    row = result[0]
    try:
        if "value" in row:
            return float(row["value"][1])
        return float(row["values"][-1][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _filter(model: str | None) -> str:
    return f'{{model_name="{model}"}}' if model else ""


def snapshot(model: str | None = None, ttft: bool = False) -> dict[str, float | None]:
    """Current vLLM serving metrics. `model` filters by served model name."""
    f = _filter(model)
    snap: dict[str, float | None] = {
        "gen_tps": scalar(query(f"sum(rate(vllm:generation_tokens_total{f}[1m]))")),
        "prefill_peak_tps": scalar(
            query(
                f"max_over_time((sum(rate(vllm:prompt_tokens_total{f}[1m])))[3m:15s])"
            )
        ),
        "prefix_hit_pct": scalar(
            query(
                f"sum(rate(vllm:prefix_cache_hits_total{f}[5m]))"
                f"/sum(rate(vllm:prefix_cache_queries_total{f}[5m]))*100"
            )
        ),
        "running": scalar(query(f"sum(vllm:num_requests_running{f})")),
        "waiting": scalar(query(f"sum(vllm:num_requests_waiting{f})")),
    }
    if ttft:
        snap["ttft_p50_s"] = scalar(
            query(
                f"histogram_quantile(0.5, sum(rate("
                f"vllm:time_to_first_token_seconds_bucket{f}[5m])) by (le))"
            )
        )
    return snap


def _n(v: float | None, fmt: str, dash: str = "n/a") -> str:
    return dash if v is None else fmt.format(v)


def format_line(snap: dict[str, float | None]) -> str:
    """One-line heartbeat summary. Pure: tested against fixed dicts."""
    parts = [
        f"decode {_n(snap.get('gen_tps'), '{:.0f}')} tok/s",
        f"prefix-hit {_n(snap.get('prefix_hit_pct'), '{:.0f}')}%",
        f"running {_n(snap.get('running'), '{:.0f}')}/waiting {_n(snap.get('waiting'), '{:.0f}')}",
        f"prefill peak {_n(snap.get('prefill_peak_tps'), '{:.0f}')} tok/s",
    ]
    if "ttft_p50_s" in snap:
        parts.append(f"TTFT p50 {_n(snap.get('ttft_p50_s'), '{:.2f}')}s")
    return "vLLM: " + ", ".join(parts)


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", help="filter to one served model_name")
    p.add_argument(
        "--ttft",
        action="store_true",
        help="include TTFT p50 (only meaningful under concurrent load)",
    )
    p.add_argument("--json", action="store_true", help="emit the raw snapshot dict")
    args = p.parse_args(argv)
    snap = snapshot(model=args.model, ttft=args.ttft)
    if args.json:
        print(json.dumps(snap))
    else:
        print(format_line(snap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
