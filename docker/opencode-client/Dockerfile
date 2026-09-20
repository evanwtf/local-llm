# The OpenCode client, pinned. #611
#
# The harness runs on a different machine from the server (#562), and a row's
# `env.client_machine` is meant to say "this CPU, this memory". Without an
# image it also says "this distro, this Python, this glibc" -- and those are
# upstream of the score, not just the clock: the trial's venv is built by
# `uv sync --frozen` ON THE CLIENT and the target's tests run in it. A pass
# can move with the client's libraries for reasons that have nothing to do
# with the model.
#
# Builds natively on linux/amd64 and linux/arm64. Nothing here branches on
# architecture: the OpenCode and uv installers both resolve it themselves, and
# every other component comes from the base image's own arch.
#
#   docker build -t local-llm-client:1.18.31 docker/opencode-client
#
# Verify with scripts/build_client_image.py, which builds and then checks the
# pinned versions from inside the image.

FROM ubuntu:26.04

# Pins. Every one of these is a variable a row would otherwise carry silently.
ARG OPENCODE_VERSION=1.18.31
ARG UV_VERSION=0.12.13
# The interpreter uv hands the trial's venv. Pinned so the same target commit
# resolves the same Python on every client.
ARG PYTHON_VERSION=3.14.4

# The OpenCode installer hardcodes INSTALL_DIR=$HOME/.opencode/bin and honours
# no override, so the binary's location is a property of HOME, not a choice.
# The image runs as root, so that is /root/.opencode/bin -- on PATH here. A
# future non-root user would need this re-pointed, not just a USER line.
ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/root/.opencode/bin:/opt/uv/bin:$PATH \
    UV_INSTALL_DIR=/opt/uv/bin \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python

# git and bubblewrap come from the base image, so they are the same build on
# both arches by construction. curl and ca-certificates are for the two
# installers below; the rest is what a trial's `uv sync` needs to build any
# wheel that has no arm64 wheel published.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git bubblewrap \
        build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://astral.sh/uv/${UV_VERSION}/install.sh | sh \
    && uv --version

# The installer resolves linux-x64 / linux-arm64 from `uname -m` itself.
RUN curl -fsSL https://opencode.ai/install | bash -s -- --version ${OPENCODE_VERSION} \
    && opencode --version

# Pre-seed the interpreter so a trial's `uv sync` never downloads one mid-run
# and never falls back to a system Python that differs between clients.
RUN uv python install ${PYTHON_VERSION} \
    && uv python find ${PYTHON_VERSION}

# Recorded so the running container can say what it is without re-deriving it.
ENV LOCAL_LLM_CLIENT_IMAGE=1 \
    LOCAL_LLM_PINNED_OPENCODE=${OPENCODE_VERSION} \
    LOCAL_LLM_PINNED_UV=${UV_VERSION} \
    LOCAL_LLM_PINNED_PYTHON=${PYTHON_VERSION}

WORKDIR /work
CMD ["/bin/bash"]
