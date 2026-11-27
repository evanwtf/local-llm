"""Build a vLLM image from a pinned base plus the Python files of one open PR.

An open vLLM PR that changes only Python files does not need a source build:
copy its files, at the PR's pinned head sha, over the package that a nightly
image already installs. The thinnest image that serves the PR is then the
nightly plus one small layer, and anyone can rebuild it from the spec (#1503:
PR 68387, HY4 Flash on sm_121 and ModelOpt NVFP4 experts).

The spec (`docker/vllm-pr68387/overlay.json`) pins everything that decides the
bytes: the PR's head repo and sha, the base image and its registry digest, and
the sha256 of each file. The script refuses:

- a file path outside `vllm/`, with `..`, or not ending in `.py` (the overlay
  is for Python-only PRs; compiled code needs a real build);
- a fetched file whose sha256 differs from the spec (the branch moved, or the
  fetch is corrupt);
- a base image whose registry digest differs from the spec;
- a smoke check where a file in the image differs from the spec.

Usage::

    uv run python scripts/vllm_pr_overlay.py fetch --out DIR
    uv run python scripts/vllm_pr_overlay.py build
    uv run python scripts/vllm_pr_overlay.py smoke --import vllm.models.hy_v4_flash

`build` fetches into a fresh temporary directory, checks the base digest, runs
`docker build`, and then runs `smoke` on the result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SPEC = ROOT / "docker" / "vllm-pr68387" / "overlay.json"
RAW = "https://raw.githubusercontent.com/{repo}/{sha}/{path}"
SHA40 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
_LONG_HEX = re.compile(r"[0-9a-f]{40}")


class OverlayError(ValueError):
    """The spec, a fetched file, or an image disagrees with the pin."""


def check_path(path: str) -> None:
    """A spec path must be a Python file inside the vllm package."""
    parts = pathlib.PurePosixPath(path).parts
    if (
        not path.startswith("vllm/")
        or path.startswith("/")
        or ".." in parts
        or not path.endswith(".py")
    ):
        raise OverlayError(f"{path!r} is not a .py file under vllm/")


def load_spec(path: pathlib.Path) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(path.read_text())
    if not SHA40.fullmatch(str(spec.get("sha", ""))):
        raise OverlayError("spec sha must be a full 40-hex commit")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(spec.get("base_digest", ""))):
        raise OverlayError("spec base_digest must be sha256:<64 hex>")
    files = spec.get("files")
    if not isinstance(files, dict) or not files:
        raise OverlayError("spec lists no files")
    for name, digest in files.items():
        check_path(name)
        if not SHA256.fullmatch(str(digest)):
            raise OverlayError(f"{name}: sha256 {digest!r} is not 64 hex")
    return spec


def image_tag(spec: dict[str, Any]) -> str:
    """`local/vllm-openai:<base tag, shas cut to 9>-pr<N>-<sha[:9]>`."""
    base_tag = str(spec["base_image"]).rsplit(":", 1)[1]
    base_tag = _LONG_HEX.sub(lambda m: m.group(0)[:9], base_tag)
    return f"local/vllm-openai:{base_tag}-pr{spec['pr']}-{spec['sha'][:9]}"


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        data: bytes = resp.read()
    return data


def fetch(spec: dict[str, Any], out: pathlib.Path, get: Any = _download) -> None:
    """Write each file under `out`, check its sha256, then write SHA256SUMS."""
    lines = []
    for name, want in sorted(spec["files"].items()):
        url = RAW.format(repo=spec["head_repo"], sha=spec["sha"], path=name)
        data = get(url)
        got = hashlib.sha256(data).hexdigest()
        if got != want:
            raise OverlayError(f"{name}: sha256 {got} != spec {want}")
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        lines.append(f"{want}  {name}\n")
        logger.info("fetched %s (%d bytes, sha256 %s)", name, len(data), got[:12])
    (out / "SHA256SUMS").write_text("".join(lines))


def repo_digests(image: str) -> list[str]:
    out = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    digests: list[str] = json.loads(out) or []
    return digests


def check_base(spec: dict[str, Any], digests: list[str]) -> None:
    want = str(spec["base_digest"])
    if not any(d.endswith("@" + want) for d in digests):
        raise OverlayError(f"base {spec['base_image']} has {digests}, spec {want}")


#: Runs inside the image: the version, an import, and each file's sha256.
SMOKE = """
import hashlib, importlib, importlib.util as u, json, pathlib, sys
pkg = pathlib.Path(u.find_spec("vllm").submodule_search_locations[0]).parent
import vllm
out = {"version": vllm.__version__, "files": {}}
for name in sys.argv[2:]:
    out["files"][name] = hashlib.sha256((pkg / name).read_bytes()).hexdigest()
for mod in sys.argv[1].split(",") if sys.argv[1] else []:
    importlib.import_module(mod)
out["imported"] = sys.argv[1]
print("SMOKE " + json.dumps(out))
"""


def smoke_argv(image: str, modules: list[str], files: list[str]) -> list[str]:
    return [
        "docker", "run", "--rm", "--gpus", "all", "--entrypoint", "python3", image,
        "-c", SMOKE, ",".join(modules), *files,
    ]  # fmt: skip


def parse_smoke(spec: dict[str, Any], stdout: str) -> dict[str, Any]:
    """Read the SMOKE line, and refuse a file that differs from the spec."""
    line = next((x for x in stdout.splitlines() if x.startswith("SMOKE ")), None)
    if line is None:
        raise OverlayError("the smoke run printed no SMOKE line")
    result: dict[str, Any] = json.loads(line[len("SMOKE ") :])
    for name, want in spec["files"].items():
        got = result["files"].get(name)
        if got != want:
            raise OverlayError(f"image {name}: sha256 {got} != spec {want}")
    return result


def smoke(spec: dict[str, Any], image: str, modules: list[str]) -> int:
    proc = subprocess.run(
        smoke_argv(image, modules, sorted(spec["files"])),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        logger.error("smoke run failed (%d): %s", proc.returncode, proc.stderr[-4000:])
        return 1
    result = parse_smoke(spec, proc.stdout)
    logger.info("vllm %s; imported %s", result["version"], result["imported"])
    for name, digest in sorted(result["files"].items()):
        logger.info("  %s  %s (matches the spec)", digest, name)
    return 0


def build(spec: dict[str, Any], spec_path: pathlib.Path, modules: list[str]) -> int:
    check_base(spec, repo_digests(spec["base_image"]))
    tag = image_tag(spec)
    with tempfile.TemporaryDirectory(prefix="vllm-pr-overlay-") as tmp:
        ctx = pathlib.Path(tmp)
        fetch(spec, ctx)
        shutil.copy(spec_path.parent / "Dockerfile", ctx / "Dockerfile")
        argv = [
            "docker", "build",
            "--build-arg", f"BASE={spec['base_image']}",
            "--label", f"org.local-llm.vllm-pr={spec['upstream']}#{spec['pr']}",
            "--label", f"org.local-llm.vllm-pr-sha={spec['sha']}",
            "--label", f"org.local-llm.base-digest={spec['base_digest']}",
            "-t", tag, str(ctx),
        ]  # fmt: skip
        subprocess.run(argv, check=True)
    logger.info("built %s", tag)
    return smoke(spec, tag, modules)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=pathlib.Path, default=DEFAULT_SPEC)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_fetch = sub.add_parser("fetch", help="fetch and check the files")
    p_fetch.add_argument("--out", type=pathlib.Path, required=True)
    for name in ("build", "smoke"):
        p = sub.add_parser(name)
        p.add_argument(
            "--import",
            dest="modules",
            action="append",
            default=[],
            help="a module to import in the image (repeatable)",
        )
        if name == "smoke":
            p.add_argument("--image", help="default: the spec's tag")
    args = parser.parse_args(argv)
    spec = load_spec(args.spec)
    if args.cmd == "fetch":
        fetch(spec, args.out)
        return 0
    if args.cmd == "build":
        return build(spec, args.spec, args.modules)
    return smoke(spec, args.image or image_tag(spec), args.modules)


if __name__ == "__main__":
    logs.configure()
    try:
        sys.exit(main())
    except OverlayError as exc:
        logger.error("refused: %s", exc)
        sys.exit(1)
