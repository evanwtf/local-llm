"""Refuse a batch while anything it measures through is out of date.

The operator's rule, 2026-09-28: "Preflight should be run at least once per
day, on any machine we're running. And it should not be 'advisory'. It should
require pulling the latest versions and using them."

It replaces the 2026-09-04 arrangement, in which versions were recorded but
nothing refused (#131). That arrangement ran a cluster day on OpenCode 1.18.32
while 1.18.33 was out, and on a GLM recipe 19 commits behind its upstream. The
version report existed, but only in the standalone preflight command, and it
never looked at the cluster recipes or the pinned client image.

What is checked, and where:

- **Client image pins** (OpenCode, uv, CPython, ripgrep), against their latest
  releases. `client_container.py` runs this before every client batch.
- **Engine recipe checkouts** (a backend's `recipe_dir`), against their
  upstream default branch after a fresh `git fetch`. `server_facts.py` runs
  this on the server before every server batch.
- **Serving images** (a backend's `image`), against the registry's current
  digest for the same tag. Also `server_facts.py`.

A component whose latest version cannot be read is **unknown**, and unknown
refuses like behind. Not knowing is not the same as being up to date.

Every check that passes writes a stamp, and `stamp_age_hours` lets a caller
refuse when no check has passed in the last day.
"""

from __future__ import annotations

import calendar
import dataclasses
import hashlib
import json
import logging
import pathlib
import re
import subprocess
import time
from collections.abc import Callable, Iterable

import staleness

logger = logging.getLogger(__name__)

STAMP = pathlib.Path.home() / ".cache" / "local-llm" / "preflight-pass.json"
#: The operator's cadence: at least once per day on every machine in use.
MAX_STAMP_AGE_HOURS = 24.0
#: How old a server's currency record may be when a client batch starts. The
#: rule is "before every batch"; the facts are written just before launch, so
#: an older file is one being reused from an earlier batch.
MAX_FACTS_AGE_HOURS = 6.0

# name -> GitHub repo whose latest release is the target version.
RELEASE_REPOS = {
    "uv": "astral-sh/uv",
    # #968: the `rg` the image puts on PATH for OpenCode's grep and glob.
    "ripgrep": "BurntSushi/ripgrep",
}
# name -> npm package whose `latest` dist-tag is the target version. OpenCode
# 2.x is published only there (#1086): its GitHub releases stop at 1.19.0.
NPM_RELEASES = {"opencode": staleness.NPM_PACKAGES["opencode"]}
#: Where uv gets its CPython builds. The newest patch release of the pinned
#: minor that this repo ships is the target: a CPython minor upgrade is a
#: deliberate change, a patch release is not.
PYTHON_BUILDS = "astral-sh/python-build-standalone"

Runner = Callable[[list[str], int], "str | None"]


@dataclasses.dataclass(frozen=True)
class Item:
    """One component: what is here, what is newest, and how to fix it."""

    name: str
    installed: str | None
    latest: str | None
    state: str  # current / behind / ahead / unknown / grace (tag builds)
    fix: str = ""

    @property
    def ok(self) -> bool:
        # "ahead" is a build newer than the last release (a main-branch
        # install); it is not stale, so it passes.
        # "grace" is a tag build whose first newer tag is under
        # TAG_GRACE_HOURS old (#1089).
        return self.state in ("current", "ahead", "grace")


def _run(argv: list[str], timeout: int = 30) -> str | None:
    """stdout of `argv`, stripped; None only when the command failed."""
    try:
        got = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if got.returncode != 0:
        return None
    # "" is success with nothing to say (a quiet `git fetch`), not failure.
    return got.stdout.strip()


def latest_release(repo: str, run: Runner = _run) -> str | None:
    """The tag of `repo`'s latest GitHub release, or None."""
    return run(
        [
            "gh",
            "release",
            "view",
            "--repo",
            repo,
            "--json",
            "tagName",
            "-q",
            ".tagName",
        ],
        30,
    )


def latest_python_patch(minor: str, run: Runner = _run) -> str | None:
    """Newest `minor`.N CPython in python-build-standalone's latest release."""
    names = run(
        [
            "gh",
            "release",
            "view",
            "--repo",
            PYTHON_BUILDS,
            "--json",
            "assets",
            "-q",
            ".assets[].name",
        ],
        60,
    )
    if not names:
        return None
    found = {
        tuple(int(x) for x in m.split("."))
        for m in re.findall(rf"cpython-({re.escape(minor)}\.\d+)", names)
    }
    if not found:
        return None
    return ".".join(str(x) for x in max(found))


def pin_items(pins: dict[str, str], run: Runner = _run) -> list[Item]:
    """The client image's pins against the newest release of each."""
    items = []
    for name, have in sorted(pins.items()):
        if name == "python":
            minor = ".".join(have.split(".")[:2])
            want = latest_python_patch(minor, run)
        elif name in NPM_RELEASES:
            want = staleness.npm_latest(NPM_RELEASES[name], run)
        elif name in RELEASE_REPOS:
            want = latest_release(RELEASE_REPOS[name], run)
        else:
            continue
        state = staleness.compare(have, want)
        fix = (
            f"bump {name} to {want} in docker/opencode-client/Dockerfile and "
            "scripts/build_client_image.py, then "
            "`uv run python scripts/build_client_image.py`"
            if state in ("behind", "unknown")
            else ""
        )
        items.append(Item(f"client image {name}", have, want, state, fix))
    return items


def recipe_item(path: pathlib.Path, run: Runner = _run) -> Item:
    """A recipe checkout against its upstream default branch, freshly fetched.

    Local, uncommitted edits are allowed: several recipes carry a local patch
    (the #664 flags in the NVFP4 start.sh). What must be current is the commit
    they sit on.
    """
    name = f"recipe {path}"
    if not (path / ".git").exists():
        return Item(name, None, None, "unknown", f"{path} is not a git checkout")
    g = ["git", "-C", str(path)]
    # A failed fetch is unknown even if an older FETCH_HEAD exists: stale
    # remote refs would compare "current" against an upstream nobody reached.
    if run([*g, "fetch", "--quiet", "origin"], 120) is None:
        return Item(name, None, None, "unknown", f"`git -C {path} fetch origin` failed")
    head = run([*g, "rev-parse", "--short", "HEAD"], 15)
    default = run([*g, "rev-parse", "--abbrev-ref", "origin/HEAD"], 15) or "origin/main"
    tip = run([*g, "rev-parse", "--short", default], 15)
    behind = run([*g, "rev-list", "--count", f"HEAD..{default}"], 15)
    if head is None or tip is None or behind is None or not behind.isdigit():
        return Item(
            name, head, tip, "unknown", f"could not compare HEAD with {default}"
        )
    if int(behind) == 0:
        return Item(name, head, tip, "current")
    return Item(
        name,
        head,
        f"{tip} ({behind} commits ahead of HEAD)",
        "behind",
        f"`git -C {path} checkout --detach {default}` (re-apply any local patch), "
        "then relaunch the server",
    )


def image_digest_remote(ref: str, run: Runner = _run) -> str | None:
    """sha256 of the registry's top-level manifest for `ref`.

    Hashing the raw manifest gives the same digest docker records in
    RepoDigests for a pulled image, index or single-platform alike.
    """
    raw = run(["docker", "buildx", "imagetools", "inspect", "--raw", ref], 60)
    if not raw:
        return None
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def image_digest_local(ref: str, run: Runner = _run) -> str | None:
    """The digest the local copy of `ref` was pulled at, or None."""
    got = run(
        ["docker", "image", "inspect", ref, "--format", "{{json .RepoDigests}}"], 30
    )
    if not got:
        return None
    try:
        digests = json.loads(got)
    except json.JSONDecodeError:
        return None
    repo = ref.rsplit(":", 1)[0] if ":" in ref.rsplit("/", 1)[-1] else ref
    for d in digests or []:
        if d.startswith(repo + "@"):
            return d.split("@", 1)[1]
    return None


def stale_containers(ref: str, run: Runner = _run) -> list[str] | None:
    """Running containers started from `ref` but not from its current image.

    Pulling a newer image moves the tag, not a container already running; a
    server launched before the pull still serves the old image while the tag
    reads current.

    None when any probe failed. "" from `docker ps -q` is success with no
    containers; None is a probe that could not run. Reading a failure as an
    empty list passed the gate while an old container might still serve.
    """
    tag_id = run(["docker", "image", "inspect", ref, "--format", "{{.Id}}"], 30)
    ids = run(["docker", "ps", "-q"], 30)
    if not tag_id or ids is None:
        return None
    stale = []
    for cid in ids.split():
        got = run(
            [
                "docker",
                "inspect",
                cid,
                "--format",
                "{{.Name}}|{{.Config.Image}}|{{.Image}}",
            ],
            30,
        )
        if not got or got.count("|") != 2:
            return None
        cname, image, running = got.split("|")
        if image == ref and running != tag_id:
            stale.append(cname.lstrip("/"))
    return stale


def image_item(ref: str, run: Runner = _run) -> Item:
    """A serving image against the registry's current digest for its tag, and
    every running container of that tag against the image the tag names."""
    here = image_digest_local(ref, run)
    there = image_digest_remote(ref, run)
    name = f"image {ref}"
    if there is None:
        return Item(
            name, here, None, "unknown", f"could not read {ref} from its registry"
        )
    if here == there:
        stale = stale_containers(ref, run)
        if stale is None:
            return Item(
                name,
                here[:19],
                there[:19],
                "unknown",
                f"could not list the running containers of {ref}; is docker up?",
            )
        if stale:
            return Item(
                name,
                f"{here[:19]}, but {', '.join(stale)} runs an older image",
                there[:19],
                "behind",
                "the image was pulled but the server was not relaunched; relaunch it",
            )
        return Item(name, here[:19], there[:19], "current")
    return Item(
        name,
        here and here[:19],
        there[:19],
        "behind",
        f"`docker pull {ref}` on every node, then relaunch the server",
    )


#: Engine image repository -> (engine name, GitHub repo whose latest release is
#: the target). #1049: the digest check above only proves a tag is not stale
#: for itself; a pinned `v0.30.0` passed it for a week after `v0.31.0` shipped.
ENGINE_IMAGE_REPOS = {
    "vllm/vllm-openai": ("vllm", "vllm-project/vllm"),
    "lmsysorg/sglang": ("sglang", "sgl-project/sglang"),
}

#: Engines served from a venv (`engine_tree`), and their release repo (#1049).
VENV_ENGINE_REPOS = {"vllm": "vllm-project/vllm"}

#: Engines run from an unpacked release archive (`engine_tree`, with a `VERSION`
#: file at its root), and their release repo (#1066).
ARCHIVE_ENGINE_REPOS = {
    "tensorfold": "ashhart/TensorFold",
    # #1067: a tagged git checkout from `install.sh --ref`, VERSION at its root.
    "tandemllm": "0xBakeer/TandemLLM",
}

#: How to update an archive-style engine other than by a release tarball.
ENGINE_UPDATE_HINTS = {
    "tandemllm": (
        "run TandemLLM's `install.sh --ref v{v} --dir <a new dir> --no-start`, and "
        "point the backend's `engine_tree` at its `src`; a new version starts a new "
        "series"
    ),
}


def _is_version_tag(tag: str) -> bool:
    return staleness.parse(tag) is not None and tag.lstrip("v")[:1].isdigit()


def engine_release_item(ref: str, run: Runner = _run) -> Item | None:
    """An engine image's version tag against the engine's newest release.

    None when the image is not a known engine's, or its tag is not a version
    (a nightly or a custom build): the digest check still covers those.
    """
    repo, _, tag = ref.rpartition(":")
    if repo not in ENGINE_IMAGE_REPOS or not _is_version_tag(tag):
        return None
    engine, release_repo = ENGINE_IMAGE_REPOS[repo]
    want = latest_release(release_repo, run)
    state = staleness.compare(tag, want)
    fix = (
        f"serve {repo}:{want} (pull it on every node, and point the backend's "
        "`image` and the launch at it); a new engine version starts a new series"
        if state in ("behind", "unknown")
        else ""
    )
    return Item(f"engine {engine} ({ref})", tag, want, state, fix)


def venv_engine_item(engine: str, tree: pathlib.Path, run: Runner = _run) -> Item:
    """A venv-served engine's installed version against its newest release."""
    name = f"engine {engine} (venv {tree})"
    have = run(
        [
            str(tree / "bin" / "python"),
            "-c",
            f"import {engine}; print({engine}.__version__)",
        ],
        60,
    )
    want = latest_release(VENV_ENGINE_REPOS[engine], run)
    if not have:
        return Item(
            name, None, want, "unknown", f"could not import {engine} from {tree}"
        )
    state = staleness.compare(have, want)
    fix = (
        f"`{tree}/bin/pip install -U {engine}=={(want or '').lstrip('v')}`"
        if state in ("behind", "unknown")
        else ""
    )
    return Item(name, have, want, state, fix)


def archive_engine_item(engine: str, tree: pathlib.Path, run: Runner = _run) -> Item:
    """A release-archive engine's `VERSION` against its newest release."""
    name = f"engine {engine} (archive {tree})"
    repo = ARCHIVE_ENGINE_REPOS[engine]
    want = latest_release(repo, run)
    version_file = tree / "VERSION"
    have = version_file.read_text().strip() if version_file.is_file() else None
    if not have:
        return Item(name, None, want, "unknown", f"no VERSION file in {tree}")
    state = staleness.compare(have, want)
    v = (want or "").lstrip("v")
    hint = ENGINE_UPDATE_HINTS.get(engine)
    fix = (
        (
            hint.format(v=v)
            if hint
            else f"download {engine}-{v}-linux-<arch>.tar.gz and its .sha256 from "
            f"https://github.com/{repo}/releases/tag/v{v}, check it, unpack it, and "
            "point the backend's `engine_tree` at it; a new version starts a new series"
        )
        if state in ("behind", "unknown")
        else ""
    )
    return Item(name, have, want, state, fix)


#: Engines built from a git checkout of a release tag (`engine_tree`), and the
#: repo whose newest release tag is the target (#1089). llama.cpp cuts a
#: `bNNNNN` build tag several times a day, but its "Latest" release flag sat
#: on v0.6.0 (2026-10-05), so `gh release view` would pass a weeks-old build.
#: The target is the newest tag in the release list instead.
TAG_ENGINE_REPOS = {"llama.cpp": "ggml-org/llama.cpp"}

#: How long a tag build stays current after the first newer tag appears
#: (#1089). Tags came 15 to 40 minutes apart on 2026-10-15 (b11632, b11635,
#: b11642, b11643), and a CUDA build takes about 25 minutes, so "the newest
#: tag only" never lets a launch through. Past the window the gate refuses;
#: every row still stamps the exact tag it ran.
TAG_GRACE_HOURS = 12.0


def release_tags(repo: str, run: Runner = _run) -> list[tuple[str, str]]:
    """(tag, publishedAt) for the newest releases by date, newest first.

    Ignores the "Latest" flag, which is not the newest release here.
    """
    out = run(
        [
            "gh",
            "release",
            "list",
            "--repo",
            repo,
            "--limit",
            "50",
            "--json",
            "tagName,publishedAt",
            "-q",
            '.[] | .tagName + " " + .publishedAt',
        ],
        30,
    )
    pairs = []
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return pairs


def _build_number(tag: str | None) -> int | None:
    if tag and tag[:1] == "b" and tag[1:].isdigit():
        return int(tag[1:])
    return None


def _hours_since(stamp: str, now: float) -> float | None:
    try:
        then = calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None
    return (now - then) / 3600.0


def tag_engine_item(
    engine: str,
    tree: pathlib.Path,
    run: Runner = _run,
    now: float | None = None,
) -> Item:
    """A tag-built engine checkout against the newest release tag.

    `grace` (passes) while the first tag newer than ours is under
    TAG_GRACE_HOURS old; `behind` (refuses) after that.
    """
    name = f"engine {engine} (tag build {tree})"
    repo = TAG_ENGINE_REPOS[engine]
    have = run(["git", "-C", str(tree), "describe", "--tags", "--exact-match"], 30)
    # Build tags only: llama.cpp also publishes semver releases (v0.7.0) on
    # a commit that already has a build tag, and those are not newer (#1103).
    tags = [(t, at) for t, at in release_tags(repo, run) if _build_number(t)]
    want = tags[0][0] if tags else None
    h, w = _build_number(have), _build_number(want)
    if h is None or w is None:
        state = "unknown"
    elif h == w:
        state = "current"
    elif h > w:
        state = "ahead"
    else:
        newer = [(t, at) for t, at in tags if (_build_number(t) or 0) > h]
        first_newer_at = newer[-1][1] if newer else None
        age = (
            _hours_since(first_newer_at, time.time() if now is None else now)
            if first_newer_at
            else None
        )
        state = "grace" if age is not None and age < TAG_GRACE_HOURS else "behind"
    fix = (
        f"`git clone --depth 1 --branch {want} https://github.com/{repo}` into a new "
        "dir, build it with the backend's flags, and point the backend's "
        "`engine_tree` at it; a new build starts a new series"
        if state in ("behind", "unknown") and want
        else (
            f"could not read a build tag from {tree} or {repo}"
            if state == "unknown"
            else ""
        )
    )
    return Item(name, have, want, state, fix)


def backend_items(backend: dict, run: Runner = _run) -> list[Item]:
    """The recipe, image and engine a backend declares. None declared, none
    checked."""
    items = []
    if backend.get("recipe_dir"):
        items.append(recipe_item(pathlib.Path(backend["recipe_dir"]).expanduser(), run))
    if backend.get("image"):
        items.append(image_item(backend["image"], run))
        engine_item = engine_release_item(backend["image"], run)
        if engine_item is not None:
            items.append(engine_item)
    else:
        engine = str(backend.get("engine") or "").lower()
        if engine in VENV_ENGINE_REPOS and backend.get("engine_tree"):
            tree = pathlib.Path(backend["engine_tree"]).expanduser()
            items.append(venv_engine_item(engine, tree, run))
        elif engine in ARCHIVE_ENGINE_REPOS and backend.get("engine_tree"):
            tree = pathlib.Path(backend["engine_tree"]).expanduser()
            items.append(archive_engine_item(engine, tree, run))
        elif engine in TAG_ENGINE_REPOS and backend.get("engine_tree"):
            tree = pathlib.Path(backend["engine_tree"]).expanduser()
            items.append(tag_engine_item(engine, tree, run))
    return items


def refusal(items: Iterable[Item]) -> str | None:
    """None when every item passes, else the message to refuse with."""
    bad = [i for i in items if not i.ok]
    if not bad:
        return None
    lines = ["preflight: out of date -- pull the latest and use it, then rerun:"]
    for i in bad:
        lines.append(
            f"  {i.name}: {i.state.upper()} (here {i.installed or '?'}, "
            f"latest {i.latest or 'unknown'})"
        )
        if i.fix:
            lines.append(f"    fix: {i.fix}")
    return "\n".join(lines)


def log_items(items: Iterable[Item]) -> None:
    for i in items:
        logger.info(
            "preflight: %s: %s (here %s, latest %s)",
            i.name,
            i.state,
            i.installed or "?",
            i.latest or "unknown",
        )


def write_stamp(
    scope: str, items: Iterable[Item], path: pathlib.Path | None = None
) -> None:
    """Record that `scope`'s checks passed now."""
    path = path or STAMP
    try:
        data = json.loads(path.read_text()) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[scope] = {
        "passed_at": time.time(),
        "items": {i.name: i.installed for i in items},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def stamp_age_hours(scope: str, path: pathlib.Path | None = None) -> float | None:
    """Hours since `scope` last passed, or None if it never has."""
    path = path or STAMP
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    passed = (data.get(scope) or {}).get("passed_at")
    if not isinstance(passed, (int, float)):
        return None
    return (time.time() - passed) / 3600


def gate(
    scope: str, items: list[Item], stamp: pathlib.Path | None = None
) -> str | None:
    """Log every item; stamp and return None if all pass, else the refusal."""
    log_items(items)
    why = refusal(items)
    if why is None:
        write_stamp(scope, items, stamp)
    return why


def facts_record(backend: str, items: Iterable[Item]) -> dict:
    """What `server_facts.py` stores in the facts file for the client to check."""
    return {
        "backend": backend,
        "passed_at": time.time(),
        "items": {i.name: i.installed for i in items},
    }


def check_facts(
    facts: dict, backend: str | None, now: float | None = None
) -> str | None:
    """None if `facts` carries a fresh pass for `backend`, else the refusal."""
    rec = facts.get("currency")
    if not isinstance(rec, dict) or not isinstance(rec.get("passed_at"), (int, float)):
        return (
            "preflight: the server facts carry no currency pass; regenerate them "
            "with scripts/server_facts.py on the server"
        )
    if backend and rec.get("backend") != backend:
        return (
            f"preflight: the server facts passed currency for {rec.get('backend')!r}, "
            f"not {backend!r}; regenerate them"
        )
    age = ((now or time.time()) - rec["passed_at"]) / 3600
    if age > MAX_FACTS_AGE_HOURS:
        return (
            f"preflight: the server's currency pass is {age:.1f} h old (limit "
            f"{MAX_FACTS_AGE_HOURS:.0f} h); regenerate the facts before this batch"
        )
    return None
