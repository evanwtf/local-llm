"""benchmarks/videogen/fast-arms-v1.json: the few-step arms for #1412.

No GPU, no network. These pin the settings each arm takes from its upstream
source, and the guard that keeps a distill LoRA off FP8 weights.
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import generate, serve

HOME = pathlib.Path("/home/u")
FASTWAN = "FastVideo/FastWan2.2-TI2V-5B-FullAttn-Diffusers"
WAN_4STEP = "Wan-AI/Wan2.2-T2V-A14B-Diffusers+lightx2v-4step"
HIGH = "wan2.2_t2v_A14b_high_noise_lora_rank64_lightx2v_4step_1217.safetensors"
LOW = "wan2.2_t2v_A14b_low_noise_lora_rank64_lightx2v_4step_1217.safetensors"


def arms() -> dict[str, Any]:
    return generate.load_json(generate.FAST_ARMS_FILE)


def test_the_file_is_registered_and_valid() -> None:
    data = arms()
    generate.check_arms(data)
    assert generate.FAST_ARMS_FILE in generate.ARMS_FILES
    assert generate.find_arms_file("fastwan-t2v") == generate.FAST_ARMS_FILE
    assert generate.find_arms_file("wan-t2v-4step") == generate.FAST_ARMS_FILE
    assert sorted(data["arms"]) == ["fastwan-t2v", "wan-t2v-4step"]
    assert data["clips"] == ["dog-a", "dog-b", "dog-c"]
    assert data["seeds"] == [1, 2, 3]


def test_the_locked_values() -> None:
    """Locked on 2026-11-28 after the #1412 timing probes. A change to these
    is a new version file, not an edit."""
    data = arms()
    assert data["status"] == "locked"
    probes = data["locked"]["probes"]
    assert [(p["arm"], p["size"], p["steps"]) for p in probes] == [
        ("fastwan-t2v", "1280x704", 3),
        ("wan-t2v-4step", "832x480", 4),
    ]
    assert [p["wall_s"] for p in probes] == [105.4, 115.8]
    assert "800 lora keys loaded" in probes[1]["lora_check"]
    for arm in data["arms"].values():
        size = f"{arm['form']['width']}x{arm['form']['height']}"
        assert size in {p["size"] for p in probes}


def test_fastwan_follows_its_model_card() -> None:
    arm = arms()["arms"]["fastwan-t2v"]
    assert arm["revision"] == "3e187042"
    assert arm["form"] == {
        "width": 1280,
        "height": 704,
        "num_frames": 121,
        "fps": 24,
        "num_inference_steps": 3,
        "guidance_scale": 1.0,
    }
    assert arm["expect"] == {"frames": 121, "audio": False}
    # TI2V-5B: VAE stride 16 x patch 2 = 32 px; 4n+1 frames.
    for size in (arm["form"], arm["fallback"]):
        assert size["width"] % 32 == 0 and size["height"] % 32 == 0
    assert (arm["form"]["num_frames"] - 1) % 4 == 0


def test_wan_4step_follows_the_lightx2v_config() -> None:
    arm = arms()["arms"]["wan-t2v-4step"]
    form = arm["form"]
    assert arm["revision"] == "5be7df96"
    assert arm["lora"]["revision"] == "57adea58"
    assert arm["lora"]["files"] == [HIGH, LOW]
    assert (form["width"], form["height"], form["num_frames"], form["fps"]) == (
        832,
        480,
        81,
        16,
    )
    assert form["num_inference_steps"] == 4
    assert form["guidance_scale"] == form["guidance_scale_2"] == 1.0
    assert form["flow_shift"] == 5.0
    assert form["boundary_ratio"] == 0.875
    assert arm["extra_params"] == {"sample_solver": "euler"}


def test_the_euler_timesteps_split_two_and_two_at_the_boundary() -> None:
    """Shift 5.0 over 4 Euler steps gives 1000, 937.5, 833.3, 625 (the
    LightX2V list 1000, 750, 500, 250 before the shift). At 0.875 the
    first two go to the high-noise expert: LightX2V's boundary_step_index 2."""
    shift = arms()["arms"]["wan-t2v-4step"]["form"]["flow_shift"]
    sigmas = [1 - i / 4 for i in range(4)]
    steps = [1000 * shift * s / (1 + (shift - 1) * s) for s in sigmas]
    assert steps == pytest.approx([1000.0, 937.5, 833.333, 625.0], abs=0.01)
    boundary = 1000 * arms()["arms"]["wan-t2v-4step"]["form"]["boundary_ratio"]
    assert sum(t >= boundary for t in steps) == 2


def test_the_wan_4step_server_is_bf16_with_the_lora_pair_in_order() -> None:
    block = arms()["servers"]["wan-t2v-4step"]
    argv = block["argv"]
    assert "--quantization" not in argv
    at = argv.index("--lora-path")
    assert argv[argv.index("--lora-backend") + 1] == "distill"
    assert [pathlib.PurePath(p).name for p in argv[at + 1 : at + 3]] == [HIGH, LOW]
    assert block["served_model_name"] == WAN_4STEP
    assert block["node"] == "worker" and block["port"] == 8041


def test_the_fastwan_server_is_on_the_head() -> None:
    block = arms()["servers"]["fastwan"]
    assert block["served_model_name"] == FASTWAN
    assert block["node"] == "head" and block["port"] == 8040
    assert block["argv"][2] == "~/models/FastWan2.2-TI2V-5B-FullAttn-Diffusers"


@pytest.mark.parametrize(
    "extra",
    [
        ["--quantization", "fp8"],
        ["--quantization=fp8"],
        ["-q", "fp8"],
        ["--diffusion-quantization-config", "cfg.json"],
    ],
)
def test_check_server_refuses_a_distill_lora_on_quantized_weights(
    extra: list[str],
) -> None:
    block = arms()["servers"]["wan-t2v-4step"]
    block["argv"] = [*block["argv"], *extra]
    with pytest.raises(ValueError, match="distill"):
        generate.check_server("wan-t2v-4step", block)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--lora-backend", "distill", "--quantization", "fp8"], True),
        (["--lora-backend=distill", "--quantization=fp8"], True),
        (["--lora-backend", "peft", "--quantization", "fp8"], False),
        (["--lora-backend", "distill"], False),
        (["--quantization", "fp8"], False),
        (["--lora-backend"], False),
        # vLLM maps `_` to `-` in flag names (FlexibleArgumentParser).
        (["--lora_backend", "distill", "--quantization", "fp8"], True),
        (["--lora_backend=distill", "--quantization=fp8"], True),
        # -q is an alias of --quantization; argparse also takes -qfp8.
        (["--lora-backend", "distill", "-q", "fp8"], True),
        (["--lora-backend", "distill", "-q=fp8"], True),
        (["--lora-backend", "distill", "-qfp8"], True),
        # vLLM-Omni's own quantization flag.
        (["--lora-backend", "distill", "--diffusion-quantization-config", "x"], True),
        (["--lora-backend", "distill", "--diffusion_quantization_config=x"], True),
        (["--lora-backend", "distill", "--quiet-x"], False),
        (["--lora-backend", "distill", "--lora-path", "a", "b"], False),
    ],
)
def test_distill_with_quantization(argv: list[str], expected: bool) -> None:
    assert generate.distill_with_quantization(argv) is expected


def test_sample_solver_is_an_allowed_extra_param() -> None:
    fields = generate.build_fields(
        arms()["arms"]["wan-t2v-4step"], "a dog", 1, WAN_4STEP
    )
    assert fields["extra_params"] == '{"sample_solver":"euler"}'
    assert fields["model"] == WAN_4STEP
    assert "negative_prompt" not in fields


def test_serve_expands_the_lora_paths() -> None:
    block, _ = generate.all_servers(generate.ARMS_FILES)["wan-t2v-4step"]
    argv, env = serve.command(block, HOME)
    paths = [a for a in argv if a.endswith(".safetensors")]
    assert paths == [
        f"{HOME}/models/Wan2.2-Distill-Loras/{HIGH}",
        f"{HOME}/models/Wan2.2-Distill-Loras/{LOW}",
    ]
    assert env["VLLM_OMNI_VIDEO_SYNC_TIMEOUT"] == "7200"


def test_each_arm_names_its_precision_and_lora() -> None:
    data = arms()["arms"]
    assert data["fastwan-t2v"]["precision"] == "bf16"
    assert data["fastwan-t2v"]["lora"] is None
    assert data["wan-t2v-4step"]["precision"] == "bf16"
    assert data["wan-t2v-4step"]["lora"]["repo"] == "lightx2v/Wan2.2-Distill-Loras"


def test_judge_reads_the_fast_arms_and_their_seeds() -> None:
    from videogen import judge

    arms_file, _, media = judge.resolve(["fastwan-t2v", "wan-t2v-4step"], None, None)
    assert arms_file == generate.FAST_ARMS_FILE and media == "video"
    cfg, seeds = judge.load_arms(arms_file)
    assert seeds == [1, 2, 3]
    names = judge.model_names(cfg)
    for path in ("/x/fastwan-blind", "/x/wan-blind", "/x/lightx2v-run", "/x/FastVideo"):
        assert judge.names_in_path(pathlib.Path(path), names), path
    assert judge.names_in_path(pathlib.Path("/x/blind-20261128"), names) == []
