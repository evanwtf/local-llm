"""Gate GLM-5.3-Flash's CuTe DSL decode top-k to datacenter Blackwell (#383).

TensorRT-LLM v1.4.0rc4 (and main as of 2026-11-22) picks the CuTe DSL radix
top-k for the KV-pool decode whenever cutlass-dsl imports
(``_torch/models/modeling_glm5_next.py``, ``self.pool_top_k``). On a DGX
Spark (GB10, sm_121) that kernel fails at warmup with CUDA_LAUNCH_INVALID_CONFIG
on both ranks. Other GPUs now take the Torch top-k, the path rc4 already uses
when cutlass-dsl is absent. The patch refuses to apply unless the source matches
exactly once.
"""

from __future__ import annotations

import pathlib
import sys

TARGET = pathlib.Path(
    "/usr/local/lib/python3.12/dist-packages/tensorrt_llm/_torch/models/modeling_glm5_next.py"
)
OLD = (
    "                TopKImplementation.CUTE_DSL_RADIX\n"
    "                if IS_CUTLASS_DSL_AVAILABLE\n"
)
NEW = (
    "                TopKImplementation.CUTE_DSL_RADIX\n"
    "                if IS_CUTLASS_DSL_AVAILABLE and 100 <= get_sm_version() < 110\n"
)
IMPORT_OLD = "        from ..cute_dsl_utils import IS_CUTLASS_DSL_AVAILABLE\n"
IMPORT_NEW = IMPORT_OLD + "        from tensorrt_llm._utils import get_sm_version\n"


def main() -> int:
    src = TARGET.read_text()
    if src.count(OLD) != 1 or src.count(IMPORT_OLD) != 1:
        print(
            f"refusing: expected one match each, got {src.count(OLD)} and {src.count(IMPORT_OLD)}"
        )
        return 1
    TARGET.write_text(src.replace(IMPORT_OLD, IMPORT_NEW).replace(OLD, NEW))
    print("patched", TARGET)
    return 0


if __name__ == "__main__":
    sys.exit(main())
