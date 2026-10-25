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
    # OpenCode's target is npm @opencode/cli, not a GitHub release (#1086).
    "registry.npmjs.org/@opencode%2fcli/latest": '{"name":"@opencode/cli","version":"1.18.33"}',
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
    assert "client image opencode: BEHIND (here 1.18.32, latest 1.18.33)" in why
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


NPM_2_0_29 = {
    "registry.npmjs.org/@opencode%2fcli/latest": (
        '{"name":"@opencode/cli","version":"2.0.29"}'
    ),
    # The GitHub releases stop at 1.19.0; consulting them is the bug.
    "--repo sst/opencode --json tagName": "v1.19.0",
    "--repo anomalyco/opencode --json tagName": "v1.19.0",
}


def test_opencode_1_19_is_behind_npm_2_0_29():
    """#1086: 2.0.29 shipped only on npm; 1.19.0 read as current for six days."""
    items = currency.pin_items({"opencode": "1.19.0"}, fake(NPM_2_0_29))
    assert (items[0].installed, items[0].latest, items[0].state) == (
        "1.19.0",
        "2.0.29",
        "behind",
    )
    assert currency.refusal(items) is not None


def test_opencode_2_0_29_is_current_against_npm():
    items = currency.pin_items({"opencode": "2.0.29"}, fake(NPM_2_0_29))
    assert items[0].state == "current"


def test_a_registry_answer_that_is_not_json_is_unknown():
    bad = {"registry.npmjs.org/@opencode%2fcli/latest": "<html>502</html>"}
    items = currency.pin_items({"opencode": "2.0.29"}, fake(bad))
    assert items[0].state == "unknown"
    assert currency.refusal(items) is not None


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


def test_a_recipe_at_its_declared_pin_passes_and_still_names_upstream(tmp_path):
    # Operator, 2026-10-25: an older build that wins stays the winner, so a
    # backend may pin its recipe to the measured commit (#1255).
    (tmp_path / ".git").mkdir()
    item = currency.recipe_item(tmp_path, recipe_runner("2"), pin="943912c")
    assert item.state == "pinned"
    assert item.ok
    assert item.latest == "94ae731 (2 commits ahead of HEAD)"


def test_a_recipe_off_its_declared_pin_refuses(tmp_path):
    (tmp_path / ".git").mkdir()
    item = currency.recipe_item(tmp_path, recipe_runner("2"), pin="1234567")
    assert item.state == "behind"
    assert not item.ok
    assert "checkout --detach 1234567" in item.fix


def test_a_pin_without_a_reason_refuses():
    backend = {"recipe_dir": "/r", "recipe_pin": "943912c"}
    with pytest.raises(ValueError, match="recipe_pin_reason"):
        currency.backend_items(backend, recipe_runner("2"))


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


# #1049: a pinned engine tag passed the digest check while a newer release was
# out -- vllm/vllm-openai:v0.30.0 ran on 2026-10-12, a week after v0.31.0.
VLLM_LATEST = {"--repo vllm-project/vllm --json tagName": "v0.31.0"}


def test_an_engine_image_behind_its_latest_release_refuses():
    item = currency.engine_release_item("vllm/vllm-openai:v0.30.0", fake(VLLM_LATEST))
    assert item is not None
    assert item.state == "behind"
    assert not item.ok
    assert "v0.31.0" in item.fix


def test_an_engine_image_at_its_latest_release_passes():
    item = currency.engine_release_item("vllm/vllm-openai:v0.31.0", fake(VLLM_LATEST))
    assert item is not None and item.state == "current"


def test_an_unreadable_engine_release_is_unknown_and_refuses():
    item = currency.engine_release_item("vllm/vllm-openai:v0.30.0", fake({}))
    assert item is not None
    assert item.state == "unknown"
    assert not item.ok


def test_a_nightly_or_custom_engine_tag_is_not_compared_by_version():
    for ref in ("vllm/vllm-openai:nightly", "vllm/vllm-openai:qwen38-flash-next"):
        assert currency.engine_release_item(ref, fake(VLLM_LATEST)) is None


def test_an_image_from_an_unmapped_repository_is_not_compared():
    assert currency.engine_release_item("ghcr.io/a/b:v1.0.0", fake({})) is None


def test_backend_items_include_the_engine_release_for_an_image():
    backend = {"image": "vllm/vllm-openai:v0.30.0"}
    run = fake({**VLLM_LATEST, "imagetools": None})
    names = [i.name for i in currency.backend_items(backend, run)]
    assert "engine vllm (vllm/vllm-openai:v0.30.0)" in names


def test_a_venv_vllm_behind_its_latest_release_refuses(tmp_path):
    py = tmp_path / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    backend = {"engine": "vllm", "engine_tree": str(tmp_path)}
    run = fake({**VLLM_LATEST, "import vllm": "0.29.0"})
    items = currency.backend_items(backend, run)
    assert [i.state for i in items] == ["behind"]
    assert "0.29.0" in (items[0].installed or "")


def test_a_venv_vllm_that_cannot_be_read_is_unknown(tmp_path):
    py = tmp_path / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    backend = {"engine": "vllm", "engine_tree": str(tmp_path)}
    items = currency.backend_items(backend, fake(VLLM_LATEST))
    assert [i.state for i in items] == ["unknown"]


TF_LATEST = {"--repo ashhart/TensorFold --json tagName": "v1.0.9"}


def _tf_tree(root: pathlib.Path, version: str | None) -> pathlib.Path:
    """A release-archive tree: `VERSION` beside `bin/` (#1066)."""
    (root / "bin").mkdir(parents=True)
    if version is not None:
        (root / "VERSION").write_text(version + "\n")
    return root


def test_a_release_archive_tensorfold_at_the_latest_release_passes(tmp_path):
    backend = {"engine": "tensorfold", "engine_tree": str(_tf_tree(tmp_path, "1.0.9"))}
    items = currency.backend_items(backend, fake(TF_LATEST))
    assert [(i.state, i.installed, i.latest) for i in items] == [
        ("current", "1.0.9", "v1.0.9")
    ]


def test_a_release_archive_tensorfold_behind_its_latest_release_refuses(tmp_path):
    backend = {"engine": "tensorfold", "engine_tree": str(_tf_tree(tmp_path, "1.0.8"))}
    items = currency.backend_items(backend, fake(TF_LATEST))
    assert [i.state for i in items] == ["behind"]
    assert "tensorfold-1.0.9-linux" in items[0].fix


def test_a_release_archive_without_a_version_file_is_unknown(tmp_path):
    backend = {"engine": "tensorfold", "engine_tree": str(_tf_tree(tmp_path, None))}
    items = currency.backend_items(backend, fake(TF_LATEST))
    assert [i.state for i in items] == ["unknown"]
    assert currency.refusal(items) is not None


# #1067: TandemLLM installs a tagged git checkout (`install.sh --ref vX.Y.Z`)
# whose root carries a `VERSION` file, so it is checked like an archive, and
# the fix names its installer rather than a tarball.
TANDEM_LATEST = {"--repo 0xBakeer/TandemLLM --json tagName": "v0.8.3"}


def test_a_tandemllm_checkout_at_the_latest_release_passes(tmp_path):
    backend = {"engine": "tandemllm", "engine_tree": str(_tf_tree(tmp_path, "0.8.3"))}
    items = currency.backend_items(backend, fake(TANDEM_LATEST))
    assert [(i.state, i.installed, i.latest) for i in items] == [
        ("current", "0.8.3", "v0.8.3")
    ]


def test_a_tandemllm_checkout_behind_names_its_installer(tmp_path):
    backend = {"engine": "tandemllm", "engine_tree": str(_tf_tree(tmp_path, "0.8.2"))}
    items = currency.backend_items(backend, fake(TANDEM_LATEST))
    assert [i.state for i in items] == ["behind"]
    assert "install.sh" in items[0].fix and "--ref v0.8.3" in items[0].fix
    assert "tar.gz" not in items[0].fix


# #1089: llama.cpp built from a release tag (`git clone --branch bNNNNN`). Its
# "Latest" GitHub release flag points at v0.6.0 (2026-10-05) while the build
# tags move several times a day, so the target is the newest tag in the
# release list, not `gh release view`.
NOW = 1792058400.0  # 2026-10-15T10:00:00Z
LIST = "release list --repo ggml-org/llama.cpp"
LCPP = {
    "describe --tags --exact-match": "b11632",
    LIST: "b11632 2026-10-15T03:20:55Z\nb11630 2026-10-15T00:32:18Z",
    "release view --repo ggml-org/llama.cpp": "v0.6.0",
}


def _lcpp(answers):
    backend = {"engine": "llama.cpp", "engine_tree": "/engines/llama.cpp-b11632"}
    tree = currency.pathlib.Path(backend["engine_tree"])
    return [currency.tag_engine_item("llama.cpp", tree, fake(answers), now=NOW)]


def test_a_llamacpp_tag_build_reaches_the_check_through_backend_items():
    backend = {"engine": "llama.cpp", "engine_tree": "/engines/llama.cpp-b11632"}
    items = currency.backend_items(backend, fake(LCPP))
    assert [i.installed for i in items] == ["b11632"]


def test_a_llamacpp_tag_build_at_the_newest_build_tag_passes():
    items = _lcpp(LCPP)
    assert [(i.installed, i.latest, i.state) for i in items] == [
        ("b11632", "b11632", "current")
    ]


def test_a_newer_tag_under_the_grace_window_still_passes():
    # b11633 appeared 2 h before NOW: inside the 12 h window.
    listing = "b11640 2026-10-15T09:00:00Z\nb11633 2026-10-15T08:00:00Z\n" + LCPP[LIST]
    items = _lcpp({**LCPP, LIST: listing})
    assert [(i.latest, i.state) for i in items] == [("b11640", "grace")]
    assert currency.refusal(items) is None


def test_a_newer_tag_past_the_grace_window_refuses():
    # The first tag after ours is 13 h old at NOW, though the newest is fresh.
    listing = "b11640 2026-10-15T09:00:00Z\nb11633 2026-10-14T21:00:00Z\n" + LCPP[LIST]
    items = _lcpp({**LCPP, LIST: listing})
    assert [(i.installed, i.latest, i.state) for i in items] == [
        ("b11632", "b11640", "behind")
    ]
    assert "--branch b11640" in items[0].fix
    assert currency.refusal(items) is not None


def test_a_llamacpp_tree_not_on_a_tag_is_unknown_not_current():
    items = _lcpp({**LCPP, "describe --tags --exact-match": None})
    assert items[0].state == "unknown"
    assert currency.refusal(items) is not None


def test_the_latest_flag_v0_6_0_is_never_the_llamacpp_target():
    # Only `gh release view` answers v0.6.0; the check must not use it.
    items = _lcpp({**LCPP, LIST: None})
    assert items[0].latest is None
    assert items[0].state == "unknown"


def test_a_semver_release_is_not_the_llamacpp_build_target():
    # #1103: v0.7.0 was published at 17:13Z on the same commit as b11669
    # (16:10Z). The target stays the newest build tag, so b11669 is current.
    listing = "v0.7.0 2026-10-15T09:30:00Z\n" + LCPP[LIST]
    items = _lcpp({**LCPP, LIST: listing})
    assert [(i.installed, i.latest, i.state) for i in items] == [
        ("b11632", "b11632", "current")
    ]
    assert currency.refusal(items) is None


# #1088: the worker Spark has no `gh`. A venv engine served there read its
# latest release as None, so the server gate refused every batch as "unknown".
# The repos are public: read the REST API directly when `gh` is absent.


def test_latest_release_reads_the_api_when_gh_is_missing():
    urls = []

    def fetch(url):
        urls.append(url)
        return {"tag_name": "v0.32.0"}

    got = currency.latest_release(
        "vllm-project/vllm", fake({}), have_gh=False, fetch=fetch
    )
    assert got == "v0.32.0"
    assert urls == ["https://api.github.com/repos/vllm-project/vllm/releases/latest"]


def test_latest_release_reads_the_api_when_the_real_gh_gives_no_answer(monkeypatch):
    # An installed but unauthenticated gh fails; the public API still answers.
    monkeypatch.setattr(currency, "_run", lambda argv, timeout=30: None)
    got = currency.latest_release(
        "a/b",
        currency._run,
        have_gh=True,
        fetch=lambda url: {"tag_name": "v1.2.3"},
    )
    assert got == "v1.2.3"


def test_an_injected_runner_that_fails_is_not_bypassed():
    def fetch(url):
        raise AssertionError("a test fake answers for gh; no network")

    assert currency.latest_release("a/b", fake({}), have_gh=True, fetch=fetch) is None


def test_latest_release_api_failure_is_none():
    def fetch(url):
        return None

    assert currency.latest_release("a/b", fake({}), have_gh=False, fetch=fetch) is None


def test_an_injected_runner_answers_for_gh_even_without_gh_installed(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    got = currency.latest_release("vllm-project/vllm", fake({"release view": "v9"}))
    assert got == "v9"
