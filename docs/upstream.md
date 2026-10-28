# Upstream issues we track

The state of the engines we depend on changes daily, so this page holds no
snapshot. Read the current state instead:

```sh
uv run python scripts/upstream_sweep.py --hours 168                   # every watched repo, last week
uv run python scripts/upstream_sweep.py --hours 168 --platform dgx    # one machine's lane
```

The `source-sweep` skill does the same scan plus X and GitHub notifications.
`WATCHED` in `scripts/upstream_sweep.py` is the list of repositories, and
[`SOURCES.md`](../SOURCES.md) is the list of people. Record an upstream defect
that affects us on our own issue, with a link to the upstream one.

The 2026-08-29 snapshot that used to live here (GLM-5.3 on the ds4 preview
branch, ds4#569, ds4#816, ds4#890) is in git history; every local issue it
cited (#14, #19, #27, #35, #38, #39, #41) is closed.

**Check upstream before you write up a finding.** Every defect we found on our
own was already reported upstream. A search first would have saved hours of
diagnosis.
