"""The results-directory name must be derivable from the machine (#85).

A hand-typed name can disagree with the hardware it claims to describe and
nothing would catch it. These pin the parsing, because the first version
reported a 128 GB Mac as 192 GB -- RAM is labelled in GiB and it divided by
1000**3.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import hardware_id as hw


def test_memory_is_binary_not_decimal():
    """137,438,953,472 bytes is 128 GiB, not 137 GB.

    Dividing by 1000**3 gave 137.4, which rounded up to 192 and named a
    machine that does not exist.
    """
    assert hw.installed_memory_gb(137_438_953_472) == 128


def test_usable_memory_rounds_up_to_installed():
    """A Linux box reports less than its slots hold.

    31,975,852 kB usable is a 32 GB machine: firmware and the iGPU take a
    slice. Rounding down would name a 24 GB machine that does not exist.
    """
    assert hw.installed_memory_gb(31_975_852 * 1024) == 32


def test_memory_never_rounds_down():
    assert hw.installed_memory_gb(17 * 1024**3) == 24
    assert hw.installed_memory_gb(8 * 1024**3) == 8


def test_cpu_names():
    assert hw.normalise_cpu("AMD Ryzen 9 7900X 12-Core Processor") == "Ryzen9-7900X"
    assert hw.normalise_cpu("Apple M5 Max") == "M5-Max"
    assert (
        hw.normalise_cpu("Intel(R) Core(TM) i9-13900K CPU @ 3.00GHz") == "Corei9-13900K"
    )


def test_gpu_names():
    assert hw.normalise_gpu("NVIDIA GeForce RTX 3080 Ti") == "RTX3080Ti"
    assert hw.normalise_gpu("AMD Radeon RX 7900 XTX") == "RX7900XTX"


def test_the_mac_directory_name():
    facts = {
        "model_name": "MacBook Pro",
        "chip": "Apple M5 Max",
        "memory_gb": 128,
        "model_number": "Z1MZ0002NLL/A",
    }
    # The slash cannot survive in a path; the underscore is the documented swap.
    assert (
        hw.directory_name(facts, "darwin") == "MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A"
    )


def test_the_linux_directory_name():
    facts = {
        "cpu": "AMD Ryzen 9 7900X 12-Core Processor",
        "memory_gb": 32,
        "gpu": "NVIDIA GeForce RTX 3080 Ti",
        "vram_gb": 12,
    }
    assert hw.directory_name(facts, "linux") == "Ryzen9-7900X-32GB-RTX3080Ti-12GB"


def test_it_refuses_to_name_a_machine_it_cannot_identify():
    """An empty name would silently collide with every other unknown machine."""
    import pytest

    with pytest.raises(SystemExit):
        hw.directory_name({}, "linux")


def test_the_placeholder_directory_matches_the_derived_name():
    """The committed directory must be the one the script would produce."""
    facts = {
        "cpu": "AMD Ryzen 9 7900X 12-Core Processor",
        "memory_gb": 32,
        "gpu": "NVIDIA GeForce RTX 3080 Ti",
        "vram_gb": 12,
    }
    assert (REPO / "hardware" / hw.directory_name(facts, "linux")).is_dir()
