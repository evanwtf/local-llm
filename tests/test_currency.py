"""The currency gate refuses stale and unknown, and passes only current.

The operator's rule, 2026-09-28: preflight is not advisory; a batch does not
start on anything out of date. These tests fake every command, so they never
touch the network, git or docker.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from collections.abc import Mapping

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))
sys.path.insert(0, str(REPO / "scripts"))

import client_container
import currency
import server_facts


def fake(answers: Mapping[str, str | None]):
    """A runner that answers by the first key found in the joined argv."""

    def run(argv: list[str], timeout: int = 30) -> str | None:
        joined = " ".join(argv)
        for key, value in answers.items():
            if key in joined:
                return value
        return None

    return run


RELEASES = {
    "--repo sst/opencode --json tagName": "v1.18.33",
    "--repo astral-sh/uv --json tagName": "0.12.19",
    "--repo BurntSushi/ripgrep --json tagName": "15.2.0",
    "--repo astral-sh/python-build-standalone": (
        "cpython-3.14.7+20260924-x86_64-unknown-linux-gnu.tar.gz\n"
        "cpython-3.14.5+20260924-aarch64-apple-darwin.tar.gz\n"
        "cpython-3.15.0a1+20260924-x86_64-unknown-linux-gnu.tar.gz\n"
    ),
}


def test_current_pins_pass_and_stamp(tmp_path):
    stamp = tmp_path / "stamp.json"
    items = currency.pin_items(
        {"opencode": "1.18.33", "uv": "0.12.19", "python": "3.14.7"}, fake(RELEASES)
    )
    assert [i.state for i in items] == ["current", "current", "current"]
    assert currency.gate("client-image", items, stamp) is None
    age = currency.stamp_age_hours("client-image", stamp)
    assert age is not None and age < 0.01


def test_a_behind_pin_refuses_and_does_not_stamp(tmp_path):
    stamp = tmp_path / "stamp.json"
    items = currency.pin_items({"opencode": "1.18.32"}, fake(RELEASES))
    why = currency.gate("client-image", items, stamp)
    assert why is not None
    assert "client image opencode: BEHIND (here 1.18.32, latest v1.18.33)" in why
    assert "build_client_image.py" in why
    assert not stamp.exists()


def test_unknown_latest_refuses():
    """A lookup that failed is not agreement."""
    items = currency.pin_items({"opencode": "1.18.33"}, fake({}))
    assert items[0].state == "unknown"
    assert currency.refusal(items) is not None


def test_a_build_newer_than_the_release_passes():
    items = currency.pin_items({"opencode": "1.18.34"}, fake(RELEASES))
    assert items[0].state == "ahead"
    assert currency.refusal(items) is None


def test_python_target_is_the_newest_patch_of_the_same_minor():
    """3.15.0a1 is in the release and must not become the target."""
    assert currency.latest_python_patch("3.14", fake(RELEASES)) == "3.14.7"


def recipe_runner(behind: str | None, fetch_ok: bool = True):
    return fake(
        {
            "fetch --quiet origin": "" if fetch_ok else None,
            "rev-parse --verify --quiet FETCH_HEAD": None,
            "rev-parse --short HEAD": "943912c",
            "rev-parse --abbrev-ref origin/HEAD": "origin/main",
            "rev-parse --short origin/main": "94ae731",
            "rev-list --count HEAD..origin/main": behind,
        }
    )


def test_a_recipe_behind_upstream_refuses(tmp_path):
    (tmp_path / ".git").mkdir()
    item = currency.recipe_item(tmp_path, recipe_runner("19"))
    assert item.state == "behind"
    assert item.latest == "94ae731 (19 commits ahead of HEAD)"
    assert "checkout --detach origin/main" in item.fix


def test_a_recipe_at_upstream_passes(tmp_path):
    (tmp_path / ".git").mkdir()
    # `fetch --quiet` prints nothing on success; "" must not read as failure.
    run = fake(
        {
            "fetch --quiet origin": "",
            "rev-parse --short HEAD": "94ae731",
            "rev-parse --abbrev-ref origin/HEAD": "origin/main",
            "rev-parse --short origin/main": "94ae731",
            "rev-list --count HEAD..origin/main": "0",
        }
    )
    assert currency.recipe_item(tmp_path, run).state == "current"


def test_a_recipe_that_cannot_fetch_is_unknown(tmp_path):
    (tmp_path / ".git").mkdir()
    item = currency.recipe_item(tmp_path, recipe_runner("0", fetch_ok=False))
    assert item.state == "unknown"


def test_the_remote_digest_is_the_sha256_of_the_raw_manifest():
    """Anchored to a known value, not just to a relationship: the digest docker
    records for a pulled image is sha256 over the exact manifest bytes."""
    raw = '{"schemaVersion":2}'
    want = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
    assert want.startswith("sha256:bafebd36")
    assert currency.image_digest_remote("x:y", fake({"imagetools": raw})) == want


def image_runner(local: str, raw: str | None):
    return fake(
        {
            "imagetools": raw,
            "image inspect": json.dumps([f"ghcr.io/a/b@{local}"]),
            # Success with no containers. A missing answer is a failed probe.
            "docker ps -q": "",
        }
    )


def test_an_image_at_the_registry_digest_passes():
    raw = '{"schemaVersion":2}'
    digest = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
    item = currency.image_item("ghcr.io/a/b:tag", image_runner(digest, raw))
    assert item.state == "current"


def test_an_image_behind_the_registry_refuses():
    item = currency.image_item(
        "ghcr.io/a/b:tag", image_runner("sha256:" + "0" * 64, '{"new":1}')
    )
    assert item.state == "behind"
    assert "docker pull ghcr.io/a/b:tag" in item.fix


def test_an_unreadable_registry_is_unknown():
    item = currency.image_item(
        "ghcr.io/a/b:tag", image_runner("sha256:" + "0" * 64, None)
    )
    assert item.state == "unknown"


def test_a_remote_backend_that_declares_nothing_refuses():
    why = server_facts.check_current("x", {"topology": "remote"}, fake({}))
    assert why is not None and "declares no recipe_dir or image" in why


def test_a_local_backend_that_declares_nothing_is_not_checked():
    assert server_facts.check_current("x", {}, fake({})) is None


def test_every_cluster_backend_declares_what_it_runs():
    """A two-node stack in the ledger must be gateable; one that declares
    nothing would be refused by server_facts on its next run anyway, and this
    says so before anyone is standing in front of the cluster."""
    import tomllib

    cfg = tomllib.loads((REPO / "benchmarks" / "agent" / "tasks.toml").read_text())
    live = {
        "glm53fexl3dual2xrclatestout64k",
        "glm53fexl3dual2xrclatest",
        "qwen38fnnvfp4dual2xrcflags",
        "qwen38fnnvfp4dual2xrcv030",
        "dsv4flashvisiondspark2xrc",
        "dsv41fexl3dual2xrc",
    }
    for name in live:
        assert cfg["backend"][name].get("recipe_dir"), name


def test_client_image_pins_are_read_from_the_image_itself():
    env = json.dumps(
        [
            "PATH=/usr/bin",
            "LOCAL_LLM_PINNED_OPENCODE=1.18.32",
            "LOCAL_LLM_PINNED_UV=0.12.13",
            "LOCAL_LLM_PINNED_PYTHON=3.14.4",
        ]
    )
    pins = client_container.image_pins("img", fake({"image inspect": env}))
    assert pins == {"opencode": "1.18.32", "uv": "0.12.13", "python": "3.14.4"}


def test_an_old_client_image_refuses():
    env = json.dumps(
        [
            "LOCAL_LLM_PINNED_OPENCODE=1.18.32",
            "LOCAL_LLM_PINNED_UV=0.12.19",
            "LOCAL_LLM_PINNED_PYTHON=3.14.7",
            "LOCAL_LLM_PINNED_RIPGREP=15.2.0",
        ]
    )
    why = client_container.check_image_current(
        "local-llm-client:1.18.32", fake({"image inspect": env, **RELEASES})
    )
    assert why is not None and "BEHIND" in why


def test_an_image_with_every_pin_current_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(currency, "STAMP", tmp_path / "stamp.json")
    env = json.dumps(
        [
            "LOCAL_LLM_PINNED_OPENCODE=1.18.33",
            "LOCAL_LLM_PINNED_UV=0.12.19",
            "LOCAL_LLM_PINNED_PYTHON=3.14.7",
            "LOCAL_LLM_PINNED_RIPGREP=15.2.0",
        ]
    )
    assert (
        client_container.check_image_current(
            "img", fake({"image inspect": env, **RELEASES})
        )
        is None
    )


def test_an_image_without_its_own_ripgrep_refuses():
    """#968: the images before the fix had no `rg`, so OpenCode unpacked its
    own under bwrap and every grep and glob call failed. Their pins were all
    current, so only the missing ripgrep pin can refuse them."""
    env = json.dumps(
        [
            "LOCAL_LLM_PINNED_OPENCODE=1.18.33",
            "LOCAL_LLM_PINNED_UV=0.12.19",
            "LOCAL_LLM_PINNED_PYTHON=3.14.7",
        ]
    )
    why = client_container.check_image_current(
        "local-llm-client:1.18.33", fake({"image inspect": env, **RELEASES})
    )
    assert why is not None and "ripgrep" in why


def test_a_behind_ripgrep_refuses():
    items = currency.pin_items({"ripgrep": "15.1.0"}, fake(RELEASES))
    assert items[0].name == "client image ripgrep"
    assert items[0].state == "behind"


def test_an_image_missing_a_pin_refuses():
    """A current OpenCode pin alone must not pass: the uv and Python pins it
    omits would go unchecked, and the gate would vouch for them anyway."""
    env = json.dumps(["LOCAL_LLM_PINNED_OPENCODE=1.18.33"])
    why = client_container.check_image_current(
        "img", fake({"image inspect": env, **RELEASES})
    )
    assert why is not None
    assert "uv" in why and "python" in why


def test_an_image_whose_pins_cannot_be_read_refuses():
    why = client_container.check_image_current("missing:tag", fake({}))
    assert why is not None and "cannot read the pins" in why


@pytest.mark.parametrize(
    "state,ok",
    [("current", True), ("ahead", True), ("behind", False), ("unknown", False)],
)
def test_only_current_and_ahead_pass(state, ok):
    assert currency.Item("x", "1", "1", state).ok is ok


def test_a_container_still_running_the_old_image_refuses():
    """Pulled, but the server was not relaunched: the tag is current, the
    serving container is not."""
    raw = '{"schemaVersion":2}'
    digest = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
    run = fake(
        {
            "imagetools": raw,
            "{{json .RepoDigests}}": json.dumps([f"ghcr.io/a/b@{digest}"]),
            "{{.Id}}": "sha256:new",
            "docker ps -q": "c1",
            "inspect c1": "/glm-head|ghcr.io/a/b:tag|sha256:old",
        }
    )
    item = currency.image_item("ghcr.io/a/b:tag", run)
    assert item.state == "behind"
    assert item.installed is not None
    assert "glm-head runs an older image" in item.installed


def test_fresh_facts_for_this_backend_pass():
    facts = {"currency": {"backend": "b", "passed_at": 1000.0}}
    assert currency.check_facts(facts, "b", now=1000.0 + 3600) is None


def test_facts_without_a_currency_pass_refuse():
    why = currency.check_facts({}, "b")
    assert why is not None and "no currency pass" in why


def test_facts_for_another_backend_refuse():
    facts = {"currency": {"backend": "a", "passed_at": 1000.0}}
    why = currency.check_facts(facts, "b", now=1000.0)
    assert why is not None and "not 'b'" in why


def test_facts_older_than_the_limit_refuse():
    facts = {"currency": {"backend": "b", "passed_at": 0.0}}
    why = currency.check_facts(facts, "b", now=7 * 3600)
    assert why is not None and "7.0 h old" in why


def test_the_backend_is_read_from_the_run_arguments():
    assert client_container.backend_of(["--backend", "x", "--trials", "3"]) == "x"
    assert client_container.backend_of(["--backend=y"]) == "y"
    assert client_container.backend_of(["--trials", "3"]) is None


def _current_image_with(**probes: str | None):
    raw = '{"schemaVersion":2}'
    digest = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
    answers: dict[str, str | None] = {
        "imagetools": raw,
        "{{json .RepoDigests}}": json.dumps([f"ghcr.io/a/b@{digest}"]),
        "{{.Id}}": "sha256:new",
    }
    answers.update(probes)
    return currency.image_item("ghcr.io/a/b:tag", fake(answers))


def test_a_failed_container_listing_is_unknown_not_current():
    """`docker ps` failing is not "no container runs an old image" (review).

    An older container may still be serving; the gate cannot see it, so it
    must refuse rather than read the tag's digest as the server's.
    """
    item = _current_image_with(**{"docker ps -q": None})
    assert item.state == "unknown"
    assert not item.ok


def test_a_failed_container_inspect_is_unknown_not_current():
    item = _current_image_with(**{"docker ps -q": "c1", "inspect c1": None})
    assert item.state == "unknown"
    assert not item.ok


def test_no_running_containers_is_current():
    assert _current_image_with(**{"docker ps -q": ""}).state == "current"
