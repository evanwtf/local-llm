# M5 Max — firmware and toolchain inventory

A hand-taken snapshot of the M5 Max MacBook Pro, captured **2026-09-11 09:15
EDT** on **macOS 26.6.2 (25G83)**, before the imminent macOS 27 upgrade
(#306). It is the pre-27 record of the layer beneath the OS version —
firmware, Metal toolchain, the wired-memory ceiling — none of which `sw_vers`
reports and none of which is reconstructable after the upgrade.

This mirrors the DGX Spark's inventory (#304). Like that one, it is a snapshot
that nothing regenerates or verifies; #305 proposes a per-machine collector and
an in-ledger header record so a firmware boundary becomes a lookup rather than
a hand-taken file. Until that ships, this is the record.

**Redaction.** This repo is public. The machine serial number, hardware UUID
and provisioning UDID from `system_profiler` are omitted deliberately; they
identify the physical unit and say nothing about performance. Everything that
bears on a benchmark is kept.

## Facts

| field | value | source |
|---|---|---|
| macOS | 26.6.2 | `sw_vers` ProductVersion |
| build | 25G83 | `sw_vers` BuildVersion |
| model name | MacBook Pro | `system_profiler SPHardwareDataType` |
| model identifier | Mac17,6 | same |
| model number | Z1MZ0002NLL/A | same (also the `hardware/<id>/` suffix) |
| chip | Apple M5 Max | same / `sysctl machdep.cpu.brand_string` |
| cores | 18 (6 efficiency "Super", 12 performance) | same |
| memory | 128 GB (137438953472 bytes) | same / `sysctl hw.memsize` |
| **system firmware** | **18000.161.10** | `system_profiler` System Firmware Version |
| OS loader | 18000.161.10 | `system_profiler` OS Loader Version |
| Metal toolchain | Apple metal 32023.883 (metalfe-32023.883) | `xcrun metal --version` |
| Metal target | air64-apple-darwin25.6.0 | same |
| `iogpu.wired_limit_mb` | 114688 | `sysctl iogpu.wired_limit_mb` |
| Xcode developer dir | /Applications/Xcode.app/Contents/Developer | `xcode-select -p` |
| xcrun | version 72 | `xcrun --version` |
| SIP | enabled | `csrutil status` |

**Not reported on this hardware:**

- **SMC version** — Apple silicon has no separately versioned SMC; its function
  is folded into the system firmware above. `ioreg -l | grep IOPlatformSMCVersion`
  returns nothing, which is expected, not a probe failure.
- **Command Line Tools package** — none installed standalone
  (`pkgutil --pkg-info=com.apple.pkg.CLTools_Executables` is empty); the
  toolchain is Xcode's, at the developer dir above.

## Raw capture

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
