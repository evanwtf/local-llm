"""The engine's own time-to-first-token for one trial, read from `/metrics`. #444

On OpenCode 1.18.31 the client's `step_start` event is emitted after the
request has already spent most of its time in the engine, so the in-band
`step_ttft_ms_*` fields understate TTFT (#444). `first_content_ms` fixed the
consumer side: invocation to the first visible event, an upper bound that
includes process startup. This is the other side of the pair: what the
**engine** measured.

There is no per-request id visible on both sides, so a request cannot be
matched to its histogram observation. A trial can be. vLLM and SGLang both
export TTFT as a Prometheus histogram with a cumulative `_sum` and `_count`,
and the harness runs one trial at a time against a server nothing else is
using. The change in `_sum` over the change in `_count`, scraped just before
and just after a trial, is therefore the exact mean engine TTFT over that
trial's requests. It is not a sample or an estimate.

That holds only while the trial is the server's only client, which is how this
harness runs. A second client on the same server, such as another session, a
load sweep or a LAN user, would be averaged in. The request count recorded
beside the mean is the check: it should be close to the trial's own number of
model steps.

Engines without a TTFT histogram (llama.cpp, ds4, Ollama) record nothing, which
means "not measured", never zero.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import urllib.error
import urllib.request

logger = logging.getLogger("agent-bench")

# Histogram base names. The exposition adds `_sum`, `_count` and `_bucket`.
HISTOGRAMS = {
    "vllm": "vllm:time_to_first_token_seconds",
    "sglang": "sglang:time_to_first_token_seconds",
}


@dataclasses.dataclass(frozen=True)
class Snapshot:
    source: str
    total_s: float
    count: int


def _sum_samples(text: str, name: str) -> float | None:
    """Every sample of `name`, across label sets, summed. None if absent."""
    values = [
        float(m.group(1))
        for m in re.finditer(
            rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)\s*$",
            text,
            re.MULTILINE,
        )
    ]
    return sum(values) if values else None


def parse(text: str) -> Snapshot | None:
    """The cumulative TTFT histogram totals, or None when none is exported."""
    for source, base in HISTOGRAMS.items():
        total = _sum_samples(text, f"{base}_sum")
        count = _sum_samples(text, f"{base}_count")
        if total is not None and count is not None:
            return Snapshot(source, total, int(count))
    return None


def scrape(base_url: str | None, timeout: float = 5.0) -> Snapshot | None:
    """GET `<base_url>/metrics` and parse it. None on any failure."""
    if not base_url:
        return None
    url = base_url.rstrip("/").removesuffix("/v1") + "/metrics"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return parse(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("engine TTFT scrape of %s failed: %s", url, exc)
        return None


def fields(before: Snapshot | None, after: Snapshot | None) -> dict:
    """Row fields for one trial, from the snapshots either side of it.

    Empty when either side is missing, or when the counters went backwards: a
    restart resets them, and a difference across a restart is not a measurement.
    """
    if before is None or after is None or before.source != after.source:
        return {}
    requests = after.count - before.count
    total_s = after.total_s - before.total_s
    if requests < 0 or total_s < 0:
        logger.warning(
            "engine TTFT counters went backwards (%s): the server restarted "
            "during the trial, so no engine TTFT is recorded",
            after.source,
        )
        return {}
    out = {"engine_ttft_source": after.source, "engine_ttft_requests": requests}
    if requests:
        out["engine_ttft_ms_mean"] = round(total_s / requests * 1000, 1)
    return out
