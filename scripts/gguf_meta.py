"""Print a GGUF file's metadata without loading the model.

Written for #33: the question of whether AtomicChat's `-M64` build differs
structurally from Unsloth's, or is only re-sharded, is answerable from 700 MiB
of header rather than 88 GiB of weights. It is also the cheapest way to check
that an engine will recognise a model's architecture before committing to a
download or a load.

    uv run python scripts/gguf_meta.py <file.gguf> [--filter ple]
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import struct
import sys

logger = logging.getLogger(__name__)

# GGUF value type ids -> struct format. Type 8 is a string and 9 an array;
# both are length-prefixed and handled separately.
SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
          6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


def read(path: pathlib.Path, with_tensors: bool = False
         ) -> dict[str, object] | tuple[dict[str, object], list[tuple[str, list[int], int]]]:
    """Parse the header. Arrays are summarised, not expanded.

    `with_tensors` also returns the tensor table as (name, dims, type). Tensor
    names are what `--override-tensor` matches on, so placing a specific
    structure -- the n-gram PLE table, say -- on a chosen backend needs them
    (#33).
    """
    with path.open("rb") as fh:
        if fh.read(4) != b"GGUF":
            raise ValueError(f"{path} is not a GGUF file")
        _version, n_tensors, n_kv = struct.unpack("<IQQ", fh.read(20))

        def string() -> str:
            n = struct.unpack("<Q", fh.read(8))[0]
            return fh.read(n).decode("utf-8", "replace")

        out: dict[str, object] = {}
        for _ in range(n_kv):
            key = string()
            kind = struct.unpack("<I", fh.read(4))[0]
            if kind == 8:
                out[key] = string()
            elif kind == 9:
                elem = struct.unpack("<I", fh.read(4))[0]
                length = struct.unpack("<Q", fh.read(8))[0]
                if elem == 8:
                    head = [string() for _ in range(min(length, 4))]
                    for _ in range(max(0, length - 4)):
                        string()
                    out[key] = f"[{length} strings] {head[:3]}"
                elif elem in SCALAR:
                    size = struct.calcsize(SCALAR[elem])
                    raw = fh.read(size * length)
                    vals = struct.unpack(f"<{length}{SCALAR[elem][1]}", raw)
                    out[key] = list(vals) if length <= 20 else f"[{length} values]"
                else:
                    raise ValueError(f"unsupported array element type {elem}")
            elif kind in SCALAR:
                out[key] = struct.unpack(SCALAR[kind],
                                         fh.read(struct.calcsize(SCALAR[kind])))[0]
            else:
                raise ValueError(f"unsupported value type {kind} for {key!r}")

        if not with_tensors:
            return out

        tensors = []
        for _ in range(n_tensors):
            name = string()
            n_dims = struct.unpack("<I", fh.read(4))[0]
            dims = list(struct.unpack(f"<{n_dims}Q", fh.read(8 * n_dims)))
            ttype = struct.unpack("<I", fh.read(4))[0]
            struct.unpack("<Q", fh.read(8))          # offset, unused here
            tensors.append((name, dims, ttype))
    return out, tensors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path")
    p.add_argument("--filter", help="only keys containing this substring")
    p.add_argument("--tensors", action="store_true",
                   help="list tensor names, dims and types instead of metadata")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    if args.tensors:
        _meta, tensors = read(pathlib.Path(args.path), with_tensors=True)
        for name, dims, ttype in tensors:
            if args.filter and args.filter.lower() not in name.lower():
                continue
            logger.info("%-56s dims=%-22s type=%d", name, dims, ttype)
        return 0

    meta = read(pathlib.Path(args.path))
    for key in sorted(meta):
        if args.filter and args.filter.lower() not in key.lower():
            continue
        logger.info("%-46s %s", key, str(meta[key])[:100])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
