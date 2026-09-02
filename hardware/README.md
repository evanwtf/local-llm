# One directory per machine

Every measurement in this project is only meaningful next to the hardware that
produced it. This project's premise is one machine at a time, and each
directory here holds the results, logs and notes for exactly one of them.

**Rows from different machines are never pooled.** `results.foreign_hardware()`
refuses to append to a file that already holds another machine's rows, and the
directory a file sits in is the first answer to which machine that is.

## Naming

**Derive the name, never type it.** A hand-written directory can disagree with
the hardware it claims to describe and nothing would catch it:

```sh
uv run python scripts/hardware_id.py          # prints the canonical name
uv run python scripts/hardware_id.py --json   # and the facts behind it
```

It reads `system_profiler` and `sysctl` on macOS, and `/proc/cpuinfo`,
`/proc/meminfo`, `lscpu`, `dmidecode` and `nvidia-smi` on Linux. It refuses to
emit a name for a machine it cannot identify rather than producing an empty one
that would collide with every other unknown machine.

**Apple hardware: `<Model Name>-<Chip>-<Memory>-<Model Number>`.**
Apple ships one model number per configuration, so it removes all ambiguity —
"M5 Max 128 GB" describes several SKUs, `Z1MZ0002NLL/A` describes one. The `/`
becomes `_` because a path cannot hold it.

    MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A

**Everything else: `<CPU>-<Memory>-<GPU>-<VRAM>`.**
A self-built PC has no model number, so the identifier is the three things that
decide what it can run.

    Ryzen9-7900X-32GB-RTX3080Ti-12GB

**Memory is the installed size, not the usable size.** The Ryzen box reports
30.5 GiB to the OS and has 32 GB in its slots; the sticker number is what
someone comparing machines will have.

## What goes in a directory

Results (`results.jsonl`), run logs, a `RESULTS.md` for that machine, and a
`README.md` recording what does not fit in the name: OS, kernel, driver
versions, engine builds, and anything about the machine that could change under
us. Versions belong **in** the files, never in the directory name — a name that
changes on a driver update breaks every link to it.

The harness itself is shared and stays out of here: one apparatus, many
machines. See #85.
