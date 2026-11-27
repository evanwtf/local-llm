"""scripts/preflight_daily.py: the daily currency gate (#726).

The gate is not advisory, so "could not look" must refuse. These pin the one
place it read a failure as an empty answer: the client-image inventory.
"""

from __future__ import annotations

import logging
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import currency
import preflight_daily as pd

DOCKER = "/usr/bin/docker"


def answering(out: str | None) -> currency.Runner:
    """A docker runner that returns `out` for every command."""

    def run(argv: list[str], timeout: int = 30) -> str | None:
        return out

    return run


def test_a_failed_inventory_is_none_not_empty() -> None:
    """Docker present but the daemon down or access denied (review)."""
    got = pd.client_images(answering(None), which=lambda _: DOCKER)
    assert got is None


def test_an_empty_inventory_is_an_empty_set() -> None:
    got = pd.client_images(answering(""), which=lambda _: DOCKER)
    assert got == set()


def test_no_docker_at_all_is_nothing_to_check() -> None:
    """A machine with no docker holds no client image to be stale."""
    got = pd.client_images(answering(None), which=lambda _: None)
    assert got == set()


def test_the_inventory_names_only_client_images() -> None:
    out = "local-llm-client:1.18.33\nlocal-llm-client:1.18.32\nother:1\n"
    got = pd.client_images(answering(out), which=lambda _: DOCKER)
    assert got == {"local-llm-client:1.18.33", "local-llm-client:1.18.32"}


def test_a_client_machine_whose_docker_fails_refuses(
    tmp_path, monkeypatch, caplog
) -> None:
    """No recipes here, docker unreadable: the gate used to exit 0."""
    tasks = tmp_path / "tasks.toml"
    tasks.write_text("")
    monkeypatch.setattr(pd, "client_images", lambda: None)
    monkeypatch.setattr(pd.client_container, "image_pins", lambda *a, **k: None)
    monkeypatch.setattr(pd.logs, "configure", lambda *a, **k: None)
    caplog.set_level(logging.INFO, logger=pd.__name__)
    assert pd.main(["--tasks-file", str(tasks)]) == 1
    assert "cannot list the client images" in caplog.text


def test_a_retired_backend_is_not_a_daily_target(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    cfg = {
        "backend": {
            "old": {
                "recipe_dir": str(recipe),
                "image": "vllm/vllm-openai:v0.30.0",
                "retired": "superseded",
            },
            "new": {"recipe_dir": str(recipe), "image": "vllm/vllm-openai:v0.31.0"},
        }
    }
    recipes, images = pd.targets(cfg)
    assert recipes == {recipe}
    assert images == {"vllm/vllm-openai:v0.31.0"}


def _recipe_at(behind: str) -> currency.Runner:
    """A recipe checkout at 943912c, `behind` commits short of upstream."""
    answers = {
        "fetch --quiet origin": "",
        "rev-parse --short HEAD": "943912c",
        "rev-parse --abbrev-ref origin/HEAD": "origin/main",
        "rev-parse --short origin/main": "94ae731",
        "rev-list --count HEAD..origin/main": behind,
    }

    def run(argv: list[str], timeout: int = 30) -> str | None:
        return answers.get(" ".join(argv[3:]))

    return run


def test_recipe_pins_name_each_live_backends_pin(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    cfg = {
        "backend": {
            "pick": {
                "recipe_dir": str(recipe),
                "recipe_pin": "943912c",
                "recipe_pin_reason": "the pick (#1255)",
            },
            "latest": {"recipe_dir": str(recipe)},
            "gone": {
                "recipe_dir": str(recipe),
                "recipe_pin": "0000000",
                "retired": "x",
            },
        }
    }
    assert pd.recipe_pins(cfg) == {recipe: {"943912c"}}


def test_a_recipe_at_a_backends_pin_passes_the_daily_gate(tmp_path):
    # #1257: the pick pins ~/dsv41_recipe at cc59c36 while v2.0 is upstream.
    (tmp_path / ".git").mkdir()
    assert pd.recipe_refusal(tmp_path, {"943912c"}, _recipe_at("2")) is None


def test_a_recipe_behind_and_off_every_pin_refuses(tmp_path):
    (tmp_path / ".git").mkdir()
    why = pd.recipe_refusal(tmp_path, {"1234567"}, _recipe_at("2"))
    assert why is not None
    assert "behind" in why.lower()


def test_an_unpinned_recipe_behind_upstream_still_refuses(tmp_path):
    (tmp_path / ".git").mkdir()
    assert pd.recipe_refusal(tmp_path, set(), _recipe_at("2")) is not None
    assert pd.recipe_refusal(tmp_path, set(), _recipe_at("0")) is None


def test_the_daily_gate_warns_when_a_pin_lacks_an_upstream_release(tmp_path, caplog):
    # #1257: the pin passes, but the daily log names v2.0 so it gets measured.
    (tmp_path / ".git").mkdir()
    base = _recipe_at("2")

    def run(argv: list[str], timeout: int = 30) -> str | None:
        if argv[3:6] == ["tag", "--merged", "origin/main"]:
            return "v2.0\n"
        return base(argv, timeout)

    caplog.set_level(logging.INFO)
    assert pd.recipe_refusal(tmp_path, {"943912c"}, run) is None
    assert "pinned below upstream release(s) v2.0" in caplog.text


# #1515: a locally built image has no registry. The daily gate used to ask one
# anyway and refused every `image_source = "local"` backend, every day.

import node_image

_PR_SHA = "6ca1feee4ca275b3c6ddcb3b9fc323b5ffeda1f1"
_LOCAL = "local/vllm-openai:nightly-5aea30579-pr68387-6ca1feee4"


def _local_cfg(recipe: pathlib.Path, **extra: object) -> dict:
    spec = {
        "tier": "gb10-spark-x2",
        "recipe_dir": str(recipe),
        "image": _LOCAL,
        "image_source": "local",
        "recipe_pin": "6ca1feee4",
        "recipe_pin_reason": "the PR head",
        **extra,
    }
    return {"backend": {"hy4": spec}}


def _facts(
    layers: tuple[str, ...] = ("sha256:l1", "sha256:l2"),
    sha: str | None = _PR_SHA,
    image_id: str = "sha256:aaaa",
) -> node_image.ImageFacts:
    labels = {} if sha is None else {node_image.PIN_LABEL: sha}
    return node_image.ImageFacts(image_id=image_id, layers=layers, labels=labels)


def _nodes(head: node_image.ImageFacts | None, worker: node_image.ImageFacts | None):
    asked: list[tuple[int, str]] = []

    def inspect(rank: int, ref: str) -> node_image.ImageFacts | None:
        asked.append((rank, ref))
        return head if rank == 0 else worker

    return inspect, asked


def test_a_local_image_is_not_a_registry_target(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    cfg = _local_cfg(recipe)
    cfg["backend"]["reg"] = {
        "recipe_dir": str(recipe),
        "image": "vllm/vllm-openai:v0.34.0",
    }
    _, images = pd.targets(cfg)
    # The registry backend keeps its registry check; the local one leaves it.
    assert images == {"vllm/vllm-openai:v0.34.0"}
    local = pd.local_images(cfg)
    assert set(local) == {_LOCAL}
    assert local[_LOCAL].two_node and local[_LOCAL].pins == {"6ca1feee4"}


def test_a_local_image_whose_checkout_is_elsewhere_is_not_checked(tmp_path):
    assert pd.local_images(_local_cfg(tmp_path / "absent")) == {}


def test_a_local_image_on_both_nodes_at_its_pin_passes(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    # The two image stores report different IDs for one image; layers decide.
    inspect, asked = _nodes(_facts(), _facts(image_id="sha256:bbbb"))
    assert pd.local_image_refusals(pd.local_images(_local_cfg(recipe)), inspect) == []
    assert asked == [(0, _LOCAL), (1, _LOCAL)]


def test_a_local_image_missing_on_the_worker_refuses(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    inspect, _ = _nodes(_facts(), None)
    (why,) = pd.local_image_refusals(pd.local_images(_local_cfg(recipe)), inspect)
    assert "not on the worker" in why and _LOCAL in why


def test_a_local_image_missing_on_the_head_refuses(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    inspect, _ = _nodes(None, _facts())
    (why,) = pd.local_image_refusals(pd.local_images(_local_cfg(recipe)), inspect)
    assert "not on the head" in why


def test_a_local_image_with_other_layers_on_the_worker_refuses(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    inspect, _ = _nodes(_facts(), _facts(layers=("sha256:l1", "sha256:other")))
    (why,) = pd.local_image_refusals(pd.local_images(_local_cfg(recipe)), inspect)
    assert "different layers" in why and "RootFS.Layers" in why


def test_a_local_image_whose_label_is_off_the_pin_refuses(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    stale = "5aea30579ad672c3c35be43112b7a1f7b293a09e"
    inspect, _ = _nodes(_facts(sha=stale), _facts(sha=stale))
    (why,) = pd.local_image_refusals(pd.local_images(_local_cfg(recipe)), inspect)
    # Names the node, the label field and the pin.
    assert "the head" in why and "the worker" in why
    assert node_image.PIN_LABEL in why and "6ca1feee4" in why


def test_a_local_image_without_the_pr_label_warns_that_its_pin_is_unchecked(
    tmp_path, caplog
):
    """An image built some other way (the inkling2 image) carries no PR-sha
    label. There is no claim to refuse, but the pin was not checked, and the
    log says so at WARNING rather than as a pass (#1515 review L4)."""
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    inspect, _ = _nodes(_facts(sha=None), _facts(sha=None))
    caplog.set_level(logging.INFO, logger=pd.__name__)
    assert pd.local_image_refusals(pd.local_images(_local_cfg(recipe)), inspect) == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and all("unlabeled" in r.getMessage() for r in warnings)
    assert "pin 6ca1feee4 not checked" in warnings[0].getMessage()


def test_an_unreachable_worker_is_not_told_to_build_the_image(tmp_path):
    """ssh exit 255 or a timeout is not "absent" (#1515 review L1)."""
    recipe = tmp_path / "recipe"
    recipe.mkdir()

    def inspect(rank: int, ref: str) -> node_image.ImageFacts | None:
        if rank == 1:
            raise node_image.NodeUnreachable(1, "ssh exit 255")
        return _facts()

    (why,) = pd.local_image_refusals(pd.local_images(_local_cfg(recipe)), inspect)
    assert "unreachable" in why and "build or load" not in why


def test_a_one_node_local_image_is_not_looked_for_on_the_worker(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    inspect, asked = _nodes(_facts(), None)
    cfg = _local_cfg(recipe, tier="gb10-spark")
    assert pd.local_image_refusals(pd.local_images(cfg), inspect) == []
    assert asked == [(0, _LOCAL)]


def test_the_daily_gate_never_asks_a_registry_about_a_local_image(
    tmp_path, monkeypatch, caplog
):
    """End to end through main: the registry check is never called for the
    local image, and the local check refuses for its own reason."""
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(
        "[backend.hy4]\n"
        'tier = "gb10-spark-x2"\n'
        f'recipe_dir = "{recipe}"\n'
        f'image = "{_LOCAL}"\n'
        'image_source = "local"\n'
        "[backend.reg]\n"
        f'recipe_dir = "{recipe}"\n'
        'image = "vllm/vllm-openai:v0.34.0"\n'
    )
    registry: list[str] = []

    def image_item(ref: str, *a: object) -> currency.Item:
        registry.append(ref)
        return currency.Item(ref, "d", "d", "current", "")

    monkeypatch.setattr(pd.currency, "image_item", image_item)
    monkeypatch.setattr(pd.currency, "engine_release_item", lambda *a: None)
    monkeypatch.setattr(pd, "recipe_refusal", lambda *a, **k: None)
    monkeypatch.setattr(pd, "client_images", lambda: set())
    monkeypatch.setattr(pd.client_container, "image_pins", lambda *a, **k: None)
    monkeypatch.setattr(pd.logs, "configure", lambda *a, **k: None)
    monkeypatch.setattr(
        pd.node_image, "inspect_image", lambda rank, ref: None if rank else _facts()
    )
    caplog.set_level(logging.INFO, logger=pd.__name__)
    assert pd.main(["--tasks-file", str(tasks)]) == 1
    # The registry backend is still asked; the local image never is.
    assert registry == ["vllm/vllm-openai:v0.34.0"]
    assert "not on the worker" in caplog.text
    assert "from its registry" not in caplog.text
