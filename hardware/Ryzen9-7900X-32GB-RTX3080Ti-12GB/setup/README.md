# bwrap confinement on the Ryzen / RTX 3080 Ti desktop

The benchmark harness confines every Linux trial with `bwrap` (#476, #477):
each trial gets its own `/tmp`, a PID namespace, and tmpfs covers over the
paths that hold the answers. `bwrap` builds that isolation from a **user
namespace**, and creating one needs a uid/gid map.

## The problem this fixes (#516)

This box runs Ubuntu 24.04, which restricts unprivileged user namespaces
through AppArmor:

```
kernel.apparmor_restrict_unprivileged_userns = 1
```

Under that setting a program may create a user namespace only if it has an
AppArmor profile that grants `userns`. The packaged `bwrap` has no profile and
is not setuid, so it is denied and exits **before** it starts the trial:

```
bwrap: setting up uid map: Permission denied
```

The harness then wrapped every trial in a command that could not start; the
client wrote nothing; the oracle read the untouched target repo and scored a
model failure. A whole batch of zeros that measured the sandbox, not the
model. `run.py` now detects a non-functional `bwrap` and falls back to
unconfined (`confinement: none`) rather than voiding the batch (#517) -- this
profile is what restores **confined** runs.

## Install

[`apparmor-bwrap`](apparmor-bwrap) is the profile. It leaves `bwrap` otherwise
unconfined and grants only `userns`, mirroring the shipped
`/etc/apparmor.d/linux-sandbox`. Install it, then keep the global restriction
**on** so only `bwrap` is exempted:

```sh
sudo install -m 0644 \
  hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/setup/apparmor-bwrap \
  /etc/apparmor.d/bwrap
sudo apparmor_parser -r /etc/apparmor.d/bwrap
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=1
```

The profile file persists across reboots, and `=1` is the Ubuntu 24.04
default, so nothing else needs persisting.

## Verify

```sh
bwrap --dev-bind / / --unshare-pid --tmpfs /tmp -- echo BWRAP_OK
```

`BWRAP_OK` means `bwrap` can create the namespace with the restriction on. The
harness reaches the same conclusion through `run.py`'s `bwrap_works()` probe;
when it passes, trials record `confinement: bwrap`, and when it fails they fall
back to `confinement: none` with a warning (#517).

## The blunter alternative

Setting `kernel.apparmor_restrict_unprivileged_userns=0` also makes `bwrap`
work, but it relaxes the restriction for **every** binary on the box, not just
`bwrap`. The profile keeps the restriction on and exempts one binary, so prefer
it.
