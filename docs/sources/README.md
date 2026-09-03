# Sweep records

One file per run of the `source-sweep` skill:

```sh
docs/sources/$(date -u +%Y-%m-%d-%H-%M-%S).md      # UTC, always
```

Seconds are in the name deliberately: two sweeps can land in the same minute
while chasing something, and a collision would overwrite the earlier one.

**These record state, not reasoning.** The reasoning lives in the issues a sweep
files; this directory exists so the *next* sweep can diff against the last one.
Without it every sweep re-derives what the previous one already established, and
claims like "the leaderboard improved" or "that branch moved" are unmeasurable
because nothing wrote down where they stood.

Read the newest file before running a sweep. Write one after, even when the
sweep found nothing — "quiet" is itself a data point, and a gap in the sequence
is indistinguishable from a sweep nobody ran.

The raw grok gather for each sweep is kept in `logs/sweeps/`, because `/tmp` is
cleared on reboot and the gather is what the post verification ran against.
