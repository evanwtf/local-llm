"""Draft acceptance for vLLM's speculative decoding, read from `/metrics`.

#319. The #148/#151 machinery proves an arm actually drafted before its rows
may be believed, and until now "drafted" meant a *draft model* whose engine
writes counters to a file -- ds4's `--mtp-timing` stderr, MTPLX's decode-trace
JSONL. vLLM's n-gram speculation has no draft model at all: the draft comes
from n-gram matches in the prompt and the tokens generated so far.

Draftless is not proofless. A draft that is never accepted is exactly the
failure #151 exists to catch, and vLLM counts acceptance natively:

    vllm:spec_decode_num_drafts_total          proposals made
    vllm:spec_decode_num_draft_tokens_total    tokens proposed
    vllm:spec_decode_num_accepted_tokens_total tokens the target kept

So the assertion is unchanged -- `used` is still `accepted > 0` -- and only the
transport differs: an HTTP endpoint rather than a growing file.

**The counters need no switch.** ds4 and MTPLX both require an env var or an
argv flag, which is why `counters_on()` inspects the server's command line.
vLLM exports these whenever a speculative config is loaded and omits them
entirely when it is not, so their *presence* is the proof that the server is
speculating, and `counters_on("vllm")` is unconditionally true.

Two consequences of a Prometheus counter that a file reader does not have:

- The values are **cumulative since server start**, not a tail. A delta needs a
  baseline, so this module keeps one per endpoint and `read_since` returns the
  change since the previous call. The first call establishes the baseline and
  reports nothing, which is the same contract as opening a log at its end --
  the smoke gate's own generation must not be credited to trial 1.
- A **restart resets them to zero**. A counter that went backwards means a new
  server, not a shrunken file, so the baseline is re-established rather than
  the difference reported as negative.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import urllib.error
import urllib.request

logger = logging.getLogger("agent-bench")

# The three counters this reader needs, in the Prometheus text exposition
# format vLLM serves. Matched by name with any label set, because the labels
# carry engine index and model name and neither is ours to predict.
_METRICS = {
    "drafts": "vllm:spec_decode_num_drafts_total",
    "drafted": "vllm:spec_decode_num_draft_tokens_total",
    "accepted": "vllm:spec_decode_num_accepted_tokens_total",
}


@dataclasses.dataclass(frozen=True)
class Counters:
    """The same shape mtplx_trace.Counters carries, so callers do not branch."""

    records: int
    requests: int
    accepted: int
    drafted: int
    verify_calls: int
    generated: int

    @property
    def accept_rate(self) -> float | None:
        """Accepted over drafted. None when nothing was ever drafted."""
        return self.accepted / self.drafted if self.drafted else None

    @property
    def used(self) -> bool:
        """The #148 assertion: was any drafted token actually kept?"""
        return self.accepted > 0


EMPTY = Counters(
    records=0, requests=0, accepted=0, drafted=0, verify_calls=0, generated=0
)


@dataclasses.dataclass(frozen=True)
class Reading:
    counters: Counters
    offset: int


# base_url -> the cumulative values seen at the previous read.
_BASELINE: dict[str, dict[str, int]] = {}


def parse(text: str) -> dict[str, int]:
    """The three cumulative counters out of Prometheus exposition text.

    A metric that is absent maps to 0 rather than raising: vLLM omits the
    `spec_decode` family entirely when no speculative config is loaded, and the
    caller distinguishes that case by `present()`, not by an exception.
    """
    got = {}
    for key, metric in _METRICS.items():
        # `^name{labels} value` or `^name value`. float() because Prometheus
        # renders counters as floats ("713.0") even when they count whole
        # things; int() on that string raises.
        #
        # Sum every matching sample, not just the first. A data-parallel or
        # multi-engine vLLM exports one line per engine index --
        # `...accepted_tokens_total{engine="0"} 40` and `{engine="1"} 37` --
        # and `re.search` would read only engine 0 and silently undercount the
        # rest. The total across engines is the figure the #148 assertion wants
        # (#319). A single-engine server has one sample, so this is identical to
        # the old behavior there.
        total = 0
        for m in re.finditer(
            rf"^{re.escape(metric)}(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)\s*$",
            text,
            re.MULTILINE,
        ):
            total += int(float(m.group(1)))
        got[key] = total
    return got


def present(text: str) -> bool:
    """Does this server export the speculative family at all?

    The proof that a server is speculating, and the reason `counters_on` needs
    no switch for vLLM. `HELP` lines are emitted for a declared metric even
    while its value is zero, so this is true from the first scrape rather than
    only after the first accepted token.
    """
    return _METRICS["accepted"] in text


def scrape(base_url: str, timeout: float = 10.0) -> str:
    """`/metrics` as text, or "" when the server cannot be reached.

    Provenance must never take a run down: a probe that raises would fail a
    trial that the model itself completed.
    """
    url = base_url.rstrip("/") + "/metrics"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("vllm spec probe: %s unreachable (%s)", url, exc)
        return ""


def read_since(base_url, offset: int = 0) -> Reading:
    """Counters accumulated since the previous call for this endpoint.

    `base_url` stands where the file readers take a path, and `offset` carries
    the cumulative drafted-token count purely so the caller's debug line can
    report how much the probe consumed. The real baseline is `_BASELINE`,
    because three counters do not fit in one integer.
    """
    url = str(base_url)
    text = scrape(url)
    if not text or not present(text):
        return Reading(counters=EMPTY, offset=offset)
    now = parse(text)
    before = _BASELINE.get(url)
    _BASELINE[url] = now
    if before is None:
        # First read: establish the baseline and report nothing. Anything the
        # server did before this point -- startup, the smoke gate -- belongs to
        # no trial.
        return Reading(counters=EMPTY, offset=now["drafted"])
    if any(now[k] < before[k] for k in now):
        logger.warning(
            "vllm spec counters went backwards (%s -> %s) -- the server "
            "restarted; re-baselining rather than reporting a negative delta",
            before,
            now,
        )
        return Reading(counters=EMPTY, offset=now["drafted"])
    delta = {k: now[k] - before[k] for k in now}
    return Reading(
        counters=Counters(
            records=1,
            requests=delta["drafts"],
            accepted=delta["accepted"],
            drafted=delta["drafted"],
            # vLLM reports no verify-call count and no generated-token count on
            # this family. Zero here is "not reported", and the row records
            # which mechanism produced the numbers so a reader can tell.
            verify_calls=0,
            generated=0,
        ),
        offset=now["drafted"],
    )


def reset(base_url: str | None = None) -> None:
    """Forget the baseline, for a test or a deliberate re-arm."""
    if base_url is None:
        _BASELINE.clear()
    else:
        _BASELINE.pop(str(base_url), None)
