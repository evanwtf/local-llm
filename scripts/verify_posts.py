"""Verify X posts against the source, for the claims that earned an issue.

The sweep order is: gather with grok (unverified), judge relevance to this
machine, file the issue marked unverified, **then** verify — only the posts
that earned an issue. This is step four.

grok has fabricated a post outright, twice, including a status ID of
1900000000000000000. So a claim with no verified URL is unusable, and a claim
that fails verification is itself a finding about the source: record it on the
issue, do not delete it.

    uv run python scripts/verify_posts.py 2094817003806806172
    uv run python scripts/verify_posts.py https://x.com/googlegemma/status/2094817003806806172
    ... | uv run python scripts/verify_posts.py          # scrapes ids from stdin

Exit 0 when every post is confirmed, 1 otherwise, so it can gate a write-up.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import provenance

logger = logging.getLogger(__name__)

ID_RE = re.compile(r"\b\d{15,25}\b")
UA = "curl/8"

# api.fxtwitter.com first: it returns the untruncated text and the QUOTED post,
# which is often where the substance is -- the Gemma 4 "2x faster on a Mac"
# claim reached us as a quote, not as the post we were handed. The syndication
# endpoint is X's own and is the fallback when the mirror is down; the two
# failing together is itself worth reporting rather than retrying forever.
SOURCES = (
    ("fxtwitter", "https://api.fxtwitter.com/status/{}"),
    ("syndication", "https://cdn.syndication.twimg.com/tweet-result?id={}&token=a"),
)


def _get(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as fh:
            return json.loads(fh.read().decode())
    except urllib.error.HTTPError as exc:
        return {"__http__": exc.code}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _from_fxtwitter(payload: dict) -> dict | None:
    tweet = payload.get("tweet")
    if not isinstance(tweet, dict):
        return None
    quote = tweet.get("quote") or {}
    return {
        "author": (tweet.get("author") or {}).get("screen_name"),
        "created_at": tweet.get("created_at"),
        "text": tweet.get("text", ""),
        "is_reply": bool(tweet.get("replying_to")),
        "quoted_author": (quote.get("author") or {}).get("screen_name"),
        "quoted_text": quote.get("text"),
    }


def _from_syndication(payload: dict) -> dict | None:
    if payload.get("__typename") != "Tweet":
        return None
    quote = payload.get("quoted_tweet") or {}
    return {
        "author": (payload.get("user") or {}).get("screen_name"),
        "created_at": payload.get("created_at"),
        "text": payload.get("text", ""),
        "is_reply": bool(payload.get("in_reply_to_screen_name")),
        "quoted_author": (quote.get("user") or {}).get("screen_name"),
        "quoted_text": quote.get("text"),
    }


PARSERS = {"fxtwitter": _from_fxtwitter, "syndication": _from_syndication}


def verify(post_id: str) -> tuple[str, dict | str]:
    """('ok', facts) | ('NOT_FOUND'|'ERROR', reason)."""
    seen_404 = False
    for name, template in SOURCES:
        payload = _get(template.format(post_id))
        if payload is None:
            continue
        if payload.get("__http__") == 404:
            seen_404 = True
            continue
        if payload.get("__http__"):
            continue
        facts = PARSERS[name](payload)
        if facts:
            facts["source"] = name
            return "ok", facts
    if seen_404:
        # A 404 from a mirror that answers other ids is the fabrication signal.
        return "NOT_FOUND", "no such post -- treat the claim as invented"
    return "ERROR", "no source could be reached; verification is inconclusive"


def ids_from(text: str) -> list[str]:
    seen, out = set(), []
    for match in ID_RE.findall(text or ""):
        if match not in seen:
            seen.add(match)
            out.append(match)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("post", nargs="*", help="post URLs or ids; omit to read stdin")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    provenance.configure()
    log_file = provenance.tee("verify-posts", machine_specific=False)
    provenance.banner(logger, engines=False)

    raw = " ".join(args.post) if args.post else sys.stdin.read()
    posts = ids_from(raw)
    if not posts:
        logger.info("no post ids found")
        return 1

    today = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    results, ok = [], True
    for post_id in posts:
        status, got = verify(post_id)
        if status != "ok":
            ok = False
            logger.info("%s  %s -- %s", post_id, status, got)
            results.append({"id": post_id, "status": status, "reason": got})
            continue
        kind = "reply" if got["is_reply"] else "post"
        logger.info(
            "%s  VERIFIED %s  @%s  %s  via %s",
            post_id,
            kind,
            got["author"],
            got["created_at"],
            got["source"],
        )
        logger.info("    %s", got["text"].replace("\n", " ")[:200])
        if got.get("quoted_text"):
            logger.info(
                "    quotes @%s: %s",
                got["quoted_author"],
                got["quoted_text"].replace("\n", " ")[:160],
            )
        results.append({"id": post_id, "status": "ok", **got})

    if args.json:
        logger.info(json.dumps(results, indent=2))
    else:
        logger.info("")
        logger.info(
            "Verified %d: %d confirmed, %d not.",
            len(results),
            sum(1 for r in results if r["status"] == "ok"),
            sum(1 for r in results if r["status"] != "ok"),
        )
        logger.info("Paste onto the issue:")
        for r in results:
            if r["status"] == "ok":
                logger.info(
                    "  **Verified** %s: post exists, authored by @%s, posted %s.",
                    today,
                    r["author"],
                    r["created_at"],
                )
            else:
                logger.info(
                    "  **Could not verify** %s: %s. Treat the claim as unsourced.",
                    today,
                    r["reason"],
                )
    logger.info("log: %s", log_file)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
