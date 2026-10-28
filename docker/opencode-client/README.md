# The pinned OpenCode client image (#611)

The harness runs on a different machine from the server (#562). A row's
`env.client_machine` is meant to say "this CPU, this memory". Without an image
it also says "this distro, this Python, this glibc", and those affect the
**score**, not only the clock: the trial's venv is built by `uv sync --frozen`
on the client, and the target's tests run in it.

The pins (OpenCode, uv, CPython, ripgrep) are the `ARG` lines in
[`Dockerfile`](Dockerfile). Build and verify, on either architecture:

```sh
uv run python scripts/build_client_image.py
```

The build checks the pins and the arch from inside the image. Then it runs the
**tool self-test** (`scripts/client_tool_smoke.py`, #968): a scripted stub
server drives the real `opencode run` through bash, read, write, edit, grep,
glob, todowrite, skill and webfetch, inside the image, under the trial's
sandbox, privileges and mounts. A tool that errors, or completes with no effect
on disk, fails the build. The build runs under a `-candidate` tag, and only a
build that passes every check gets the real tag.

`scripts/client_container.py` runs the same self-test before **every batch**
and refuses the batch on a failure. It takes about 8 seconds and needs no
server or GPU. To run it by hand:

```sh
uv run python scripts/client_tool_smoke.py --image local-llm-client:<tag>
```

Why: before #968, every OpenCode `grep` and `glob` call in this image failed.
OpenCode found no `rg` on PATH and unpacked its own ripgrep with `tar`, which
could not restore the file owner under bwrap. Nothing refused, because nothing
ran a tool. The image now installs a pinned ripgrep at `/usr/local/bin/rg`.

## bwrap needs real privileges in here, and that is the whole security story

Each trial is sandboxed with bwrap, which needs user namespaces and mount
operations that Docker denies by default. Measured on the arm64 build,
2026-09-20:

| posture | result |
|---|---|
| default | `Creating new namespace failed: Operation not permitted` |
| `--security-opt seccomp=unconfined --security-opt apparmor=unconfined` | same |
| `--cap-add SYS_ADMIN` | `Failed to make / slave: Permission denied` |
| `--cap-add SYS_ADMIN --security-opt apparmor=unconfined` | `pivot_root: Operation not permitted` |
| `--cap-add SYS_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined` | **works** |
| `--privileged` | works |

The minimum that runs a trial is `SYS_ADMIN` plus unconfined seccomp and
AppArmor.

**The container is not an added security boundary.** `SYS_ADMIN` is close to
root, and unconfined seccomp drops syscall filtering. The isolation between the
host and model-written code is the same bwrap layer as without the container.
This image buys **reproducibility** only.

If a host cannot accept this posture, use rootless Docker with userns enabled,
or run the harness without a container. Do not keep the container and drop
bwrap: that trades the real boundary for a nominal one.
