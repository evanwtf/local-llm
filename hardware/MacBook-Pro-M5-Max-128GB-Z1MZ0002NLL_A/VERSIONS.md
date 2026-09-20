# M5 Max — firmware and toolchain inventory

A hand-taken snapshot of the M5 Max MacBook Pro. It records the layer beneath
the OS version — firmware, Metal toolchain, the wired-memory ceiling — none of
which `sw_vers` reports and none of which is reconstructable after an OS
upgrade.

The file now holds two captures, on each side of the macOS 27 boundary (#306):

- **macOS 27.0 (26A428)** — captured **2026-09-20 06:04 EDT**. This is the
  current record, re-taken on 27 for #499.
- **macOS 26.6.2 (25G83)** — captured **2026-09-11 09:15 EDT**, before the
  upgrade. Kept as the pre-27 boundary record.

This mirrors the DGX Spark's inventory (#304). Like that one, it is a snapshot
that nothing regenerates or verifies; #305 proposes a per-machine collector and
an in-ledger header record so a firmware boundary becomes a lookup rather than
a hand-taken file. Until that ships, this is the record.

**Redaction.** This repo is public. The machine serial number, hardware UUID
and provisioning UDID from `system_profiler` are omitted deliberately; they
identify the physical unit and say nothing about performance. Everything that
bears on a benchmark is kept.

## Facts

The model, memory, wired ceiling, `xcrun` version and SIP state did not change
across the boundary. The system firmware and the Metal toolchain did.

| field | macOS 27.0 | macOS 26.6.2 | source |
|---|---|---|---|
| macOS | 27.0 | 26.6.2 | `sw_vers` ProductVersion |
| build | 26A428 | 25G83 | `sw_vers` BuildVersion |
| model name | MacBook Pro | MacBook Pro | `system_profiler SPHardwareDataType` |
| model identifier | Mac17,6 | Mac17,6 | same |
| model number | Z1MZ0002NLL/A | Z1MZ0002NLL/A | same (also the `hardware/<id>/` suffix) |
| chip | Apple M5 Max | Apple M5 Max | same / `sysctl machdep.cpu.brand_string` |
| cores | 18 (6 efficiency "Super", 12 performance) | 18 (6 efficiency "Super", 12 performance) | same |
| memory | 128 GB (137438953472 bytes) | 128 GB (137438953472 bytes) | same / `sysctl hw.memsize` |
| **system firmware** | **20457.1.29** | **18000.161.10** | `system_profiler` System Firmware Version |
| OS loader | 20457.1.29 | 18000.161.10 | `system_profiler` OS Loader Version |
| Metal toolchain | Apple metal 32023.921 (metalfe-32023.921.6) | Apple metal 32023.883 (metalfe-32023.883) | `xcrun metal --version` |
| Metal target | air64-apple-darwin27.0.0 | air64-apple-darwin25.6.0 | same |
| `iogpu.wired_limit_mb` | 114688 | 114688 | `sysctl iogpu.wired_limit_mb` |
| Xcode developer dir | /Applications/Xcode.app/Contents/Developer | /Applications/Xcode.app/Contents/Developer | `xcode-select -p` |
| xcrun | version 72 | version 72 | `xcrun --version` |
| SIP | enabled | enabled | `csrutil status` |

**The offline Metal compiler was absent on the first boot of 27, then
reinstalled.** The upgrade removed the Metal Toolchain, so `xcrun metal`
reported it missing (#499). It was reinstalled with `xcodebuild
-downloadComponent MetalToolchain`, which is why the 27 capture above records a
working `xcrun metal`. Our engines compile shaders at run time and do not
depend on the offline compiler; it is recorded here for the inventory only.

**Not reported on this hardware (both captures):**

- **SMC version** — Apple silicon has no separately versioned SMC; its function
  is folded into the system firmware above. `ioreg -l | grep IOPlatformSMCVersion`
  returns nothing, which is expected, not a probe failure.
- **Command Line Tools package** — none installed standalone
  (`pkgutil --pkg-info=com.apple.pkg.CLTools_Executables` is empty); the
  toolchain is Xcode's, at the developer dir above.

## Raw capture — macOS 27.0, 2026-09-20 06:04 EDT

```text
$ sw_vers
ProductName:     macOS
ProductVersion:  27.0
BuildVersion:    26A428

$ system_profiler SPHardwareDataType        # serial / UUID / UDID redacted
Model Name: MacBook Pro
Model Identifier: Mac17,6
Model Number: Z1MZ0002NLL/A
Chip: Apple M5 Max
Total Number of Cores: 18 (6 Super and 12 Performance)
Memory: 128 GB
System Firmware Version: 20457.1.29
OS Loader Version: 20457.1.29

$ xcrun metal --version
Apple metal version 32023.921 (metalfe-32023.921.6)
Target: air64-apple-darwin27.0.0
Thread model: posix

$ sysctl -n machdep.cpu.brand_string hw.memsize iogpu.wired_limit_mb
Apple M5 Max
137438953472
114688

$ xcode-select -p
/Applications/Xcode.app/Contents/Developer

$ csrutil status
System Integrity Protection status: enabled.
```

## Raw capture — macOS 26.6.2, 2026-09-11 09:15 EDT (pre-upgrade)

```text
$ sw_vers
ProductName:     macOS
ProductVersion:  26.6.2
BuildVersion:    25G83

$ system_profiler SPHardwareDataType        # serial / UUID / UDID redacted
Model Name: MacBook Pro
Model Identifier: Mac17,6
Model Number: Z1MZ0002NLL/A
Chip: Apple M5 Max
Total Number of Cores: 18 (6 Super and 12 Performance)
Memory: 128 GB
System Firmware Version: 18000.161.10
OS Loader Version: 18000.161.10

$ xcrun metal --version
Apple metal version 32023.883 (metalfe-32023.883)
Target: air64-apple-darwin25.6.0
Thread model: posix

$ sysctl -n machdep.cpu.brand_string hw.memsize iogpu.wired_limit_mb
Apple M5 Max
137438953472
114688

$ xcode-select -p
/Applications/Xcode.app/Contents/Developer

$ csrutil status
System Integrity Protection status: enabled.
```
