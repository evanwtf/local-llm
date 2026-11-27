# A vLLM nightly plus the Python files of one open vLLM PR (#1503: PR 68387,
# HY4 Flash on sm_121 and ModelOpt NVFP4 experts). Build it with
# `uv run python scripts/vllm_pr_overlay.py build`, which fetches the files at
# the pinned sha, checks each sha256 against overlay.json, and passes BASE as
# repo@digest. The PR changes no compiled code, so copying its files over the
# installed package gives the same engine as a source build of the branch on
# that base.
# The default is the pinned base, by digest, so a bare `docker build` cannot
# pick up a retagged nightly.
ARG BASE=vllm/vllm-openai@sha256:d24e5fc73f0b859bd76c9d2f6f6fa4da3001afd7637f2610876420f3027ea6c7
FROM ${BASE}
COPY vllm/ /opt/pr-overlay/vllm/
COPY SHA256SUMS /opt/pr-overlay/SHA256SUMS
# find_spec locates the installed package without importing it (an import
# would initialize CUDA). sha256sum -c proves the copy landed where vLLM loads.
RUN set -eu; \
    pkg="$(python3 -c 'import importlib.util as u; print(u.find_spec("vllm").submodule_search_locations[0])')"; \
    cp -r /opt/pr-overlay/vllm/. "$pkg/"; \
    cd "$pkg/.." && sha256sum -c /opt/pr-overlay/SHA256SUMS
