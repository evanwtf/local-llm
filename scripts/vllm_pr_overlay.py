"""Build a vLLM image from a pinned base plus the Python files of one open PR.

An open vLLM PR that changes only Python files does not need a source build:
copy its files, at the PR's pinned head sha, over the package that a nightly
image already installs. The thinnest image that serves the PR is then the
nightly plus one small layer, and anyone can rebuild it from the spec (#1503:
PR 68387, HY4 Flash on sm_121 and ModelOpt NVFP4 experts).

The spec (`docker/vllm-pr68387/overlay.json`) pins everything that decides the
bytes: the PR's head repo, branch and sha, the base image and its registry
digest, and the sha256 of each file. The script refuses:

- a spec with a malformed PR number, repository, base tag, digest or sha;
- a file path outside `vllm/`, with `..`, or not ending in `.py` (the overlay
  is for Python-only PRs; compiled code needs a real build);
- a fetched file whose sha256 differs from the spec (the branch moved, or the
  fetch is corrupt);
- a base image that is not pulled, or whose registry digest differs;
- a smoke check that prints no readable result, or where a file in the image
  differs from the spec.

Usage::

    uv run python scripts/vllm_pr_overlay.py fetch --out DIR
    uv run python scripts/vllm_pr_overlay.py build
    uv run python scripts/vllm_pr_overlay.py smoke

`build` fetches into a fresh temporary directory, checks the base digest, runs
`docker build` FROM the base pinned by digest, and then runs `smoke` on the
result. `smoke` imports every overlaid module (plus any `--import`), so a file
that does not import fails the build, not the first server start.
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
#: A full git commit; also what `image_tag` shortens inside a base tag.
HEX40 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
#: `repo:tag`, with a tag and no `@digest`: the digest has its own field.
BASE_IMAGE = re.compile(r"[a-z0-9._/-]+:[A-Za-z0-9._-]+")
REF = re.compile(r"[A-Za-z0-9._/-]+")


class OverlayError(ValueError):
    """The spec, a fetched file, or an image disagrees with the pin."""


def check_path(path: str) -> None:
    """A spec path must be a Python file inside the vllm package.

    `startswith("vllm/")` also excludes an absolute path; `..` is checked by
    path part, so a file named `a..b.py` stays legal.
    """
    parts = pathlib.PurePosixPath(path).parts
    if not path.startswith("vllm/") or ".." in parts or not path.endswith(".py"):
        raise OverlayError(f"{path!r} is not a .py file under vllm/")


def load_spec(path: pathlib.Path) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(path.read_text())
    pr = spec.get("pr")
    if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
        raise OverlayError(f"spec pr must be a positive PR number, got {pr!r}")
    for key in ("upstream", "head_repo"):
        if not REPO.fullmatch(str(spec.get(key, ""))):
            raise OverlayError(f"spec {key} must be owner/name, got {spec.get(key)!r}")
    if "head_ref" in spec and not REF.fullmatch(str(spec["head_ref"])):
        raise OverlayError(f"spec head_ref {spec['head_ref']!r} is not a branch name")
    if not HEX40.fullmatch(str(spec.get("sha", ""))):
        raise OverlayError("spec sha must be a full 40-hex commit")
    if not BASE_IMAGE.fullmatch(str(spec.get("base_image", ""))):
        raise OverlayError(
            f"spec base_image must be repo:tag, got {spec.get('base_image')!r}"
        )
    if not DIGEST.fullmatch(str(spec.get("base_digest", ""))):
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
    repo, sep, base_tag = str(spec["base_image"]).rpartition(":")
    if not sep or not repo or "/" in base_tag:
        raise OverlayError(f"base image {spec['base_image']!r} has no tag")
    base_tag = HEX40.sub(lambda m: m.group(0)[:9], base_tag)
    return f"local/vllm-openai:{base_tag}-pr{spec['pr']}-{spec['sha'][:9]}"


def base_ref(spec: dict[str, Any]) -> str:
    """The base pinned by digest, `repo@sha256:...`, for FROM."""
    repo = str(spec["base_image"]).rpartition(":")[0]
    return f"{repo}@{spec['base_digest']}"


def modules_of(spec: dict[str, Any]) -> list[str]:
    """The importable module of each overlaid file, in spec order."""
    out = []
    for name in spec["files"]:
        mod = name.removesuffix(".py").replace("/", ".")
        out.append(mod.removesuffix(".__init__"))
    return out


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


def repo_digests(image: str, run: Any = subprocess.run) -> list[str]:
    proc = run(
        ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise OverlayError(
            f"base {image} is not on this machine; `docker pull {image}` first"
        )
    digests: list[str] = json.loads(proc.stdout) or []
    return digests


def check_base(spec: dict[str, Any], digests: list[str]) -> None:
    want = str(spec["base_digest"])
    if not any(d.endswith("@" + want) for d in digests):
        raise OverlayError(f"base {spec['base_image']} has {digests}, spec {want}")


def build_labels(spec: dict[str, Any], tag: str) -> dict[str, str]:
    """Labels that replace the base's own, so the image names what it is.

    The nightly carries `ai.vllm.image.tag`, `ai.vllm.build.commit`, its CI
    build URL and the OCI version and revision of the nightly; inherited
    unchanged, they would describe an image without the PR.
    """
    pr = f"{spec['upstream']}#{spec['pr']}"
    url = f"https://github.com/{spec['upstream']}/pull/{spec['pr']}"
    return {
        "ai.vllm.build.url": url,
        "org.opencontainers.image.url": url,
        "ai.vllm.image.tag": tag,
        "ai.vllm.build.commit": spec["sha"],
        "ai.vllm.build.pipeline": "local vllm_pr_overlay.py",
        "org.opencontainers.image.version": tag,
        "org.opencontainers.image.revision": spec["sha"],
        "org.opencontainers.image.base.name": spec["base_image"],
        "org.opencontainers.image.base.digest": spec["base_digest"],
        "org.local-llm.vllm-pr": pr,
        "org.local-llm.vllm-pr-sha": spec["sha"],
    }


def build_argv(spec: dict[str, Any], tag: str, ctx: pathlib.Path) -> list[str]:
    argv = ["docker", "build", "--build-arg", f"BASE={base_ref(spec)}"]
    for key, value in build_labels(spec, tag).items():
        argv += ["--label", f"{key}={value}"]
    return [*argv, "-t", tag, str(ctx)]


#: Runs inside the image: the version, the imports, and each file's sha256.
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
    try:
        result = json.loads(line[len("SMOKE ") :])
    except ValueError as exc:
        raise OverlayError(f"the SMOKE line is not JSON: {exc}") from exc
    if not isinstance(result, dict) or not isinstance(result.get("files"), dict):
        raise OverlayError("the SMOKE line has no files map")
    for name, want in spec["files"].items():
        got = result["files"].get(name)
        if got != want:
            raise OverlayError(f"image {name}: sha256 {got} != spec {want}")
    return result


def smoke(spec: dict[str, Any], image: str, extra: list[str]) -> int:
    modules = [*modules_of(spec), *(m for m in extra if m not in modules_of(spec))]
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
    logger.info("vllm %s; imported %s", result.get("version"), result.get("imported"))
    for name, digest in sorted(result["files"].items()):
        logger.info("  %s  %s (matches the spec)", digest, name)
    return 0


def build(spec: dict[str, Any], spec_path: pathlib.Path, extra: list[str]) -> int:
    check_base(spec, repo_digests(spec["base_image"]))
    tag = image_tag(spec)
    with tempfile.TemporaryDirectory(prefix="vllm-pr-overlay-") as tmp:
        ctx = pathlib.Path(tmp)
        fetch(spec, ctx)
        shutil.copy(spec_path.parent / "Dockerfile", ctx / "Dockerfile")
        subprocess.run(build_argv(spec, tag, ctx), check=True)
    logger.info("built %s", tag)
    return smoke(spec, tag, extra)


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
            help="a module to import beyond the overlaid ones (repeatable)",
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
