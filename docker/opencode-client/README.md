# The pinned OpenCode client image (#611)

The harness runs on a different machine from the server (#562). A row's
`env.client_machine` is meant to say "this CPU, this memory". Without an image
it also says "this distro, this Python, this glibc" -- and those are upstream
of the **score**, not just the clock: the trial's venv is built by
`uv sync --frozen` on the client and the target's tests run in it.

Build and verify, on either architecture:

```sh
uv run python scripts/build_client_image.py
```

Verified builds:

| arch | built | opencode | uv | python | git |
|---|---|---|---|---|---|
| `aarch64` | 2026-09-20 | 1.18.31 | 0.12.13 | 3.14.4 | 2.53.0 |
| `x86_64` | 2026-09-24 | 1.18.32 | 0.12.13 | 3.14.4 | 2.53.0 |

## bwrap needs real privileges in here, and that is the whole security story

Each trial is sandboxed with bwrap, which needs user namespaces and mount
operations Docker denies by default. Measured on the arm64 build, 2026-09-20:

| posture | result |
|---|---|
| default | `Creating new namespace failed: Operation not permitted` |
| `--security-opt seccomp=unconfined --security-opt apparmor=unconfined` | same |
| `--cap-add SYS_ADMIN` | `Failed to make / slave: Permission denied` |
| `--cap-add SYS_ADMIN --security-opt apparmor=unconfined` | `pivot_root: Operation not permitted` |
| `--cap-add SYS_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined` | **works** |
| `--privileged` | works |

So the minimum that runs a trial is `SYS_ADMIN` plus unconfined seccomp and
AppArmor.

**Read that honestly: the container is not an added security boundary here.**
Making bwrap work removes most of what makes a container a boundary at all --
`SYS_ADMIN` is close to root and unconfined seccomp drops syscall filtering.
The isolation protecting the host from model-written code is the same bwrap
layer as before, no more and no less. This image buys **reproducibility**, and
claiming otherwise would be the dangerous reading.

If the privilege posture is unacceptable on some future host, the options are
rootless Docker with userns enabled, or running the harness without a
container as it does today -- not "keep the container and drop bwrap", which
would trade the real boundary for the nominal one.
