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
# Builds natively on linux/amd64 and linux/arm64. Only the ripgrep step names
# the arch, because its release assets are named by it: the OpenCode and uv
# installers both resolve it themselves, and every other component comes from
# the base image's own arch.
#
#   docker build -t local-llm-client:1.18.34 docker/opencode-client
#
# Build with scripts/build_client_image.py, not by hand: it checks the pinned
# versions from inside the image and runs every OpenCode tool the trials use
# (#968) before it gives the build its tag.

FROM ubuntu:26.04

# Pins. Every one of these is a variable a row would otherwise carry silently.
ARG OPENCODE_VERSION=1.18.34
ARG UV_VERSION=0.12.23
# The interpreter uv hands the trial's venv. Pinned so the same target commit
# resolves the same Python on every client.
ARG PYTHON_VERSION=3.14.8
# The `rg` OpenCode's grep and glob tools run (#968). See the RUN below.
ARG RIPGREP_VERSION=15.2.0

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
# `lsof` and `procps` are not optional: preflight.py reads listening ports
# from `lsof -nP -iTCP -sTCP:LISTEN` and processes from `ps -eo ...`, and a
# missing one aborts the batch with FileNotFoundError rather than degrading.
# `apt-get upgrade` brings the base image's own packages current too: install
# alone left them at the base image's versions (rust-coreutils, 2026-10-01).
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git bubblewrap \
        lsof procps \
        build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://astral.sh/uv/${UV_VERSION}/install.sh | sh \
    && uv --version

# The installer resolves linux-x64 / linux-arm64 from `uname -m` itself.
RUN curl -fsSL https://opencode.ai/install | bash -s -- --version ${OPENCODE_VERSION} \
    && opencode --version

# ripgrep on PATH, so OpenCode never unpacks its own (#968). OpenCode 1.18.34
# resolves `rg` in this order: `which rg`, then $XDG_DATA_HOME/opencode/bin/rg,
# and only then downloads ripgrep 15.1.0 from GitHub and runs `tar -xzf` on it.
# Inside a trial that tar runs as root under bwrap, and there root cannot give
# a file to another uid: `chown 1001 f` fails with EPERM under bwrap in this
# container and succeeds outside bwrap (checked 2026-10-06). So restoring the
# tarball's owner fails with "Cannot change ownership ... Operation not
# permitted", and every grep and glob call failed that way from 2026-09-22
# until this line. The musl builds are static, so the
# same binary runs on any base; this is the one step that names the arch,
# because ripgrep's release assets are named by it.
RUN arch="$(uname -m)" \
    && case "$arch" in x86_64|aarch64) ;; *) echo "no ripgrep for $arch" >&2; exit 1 ;; esac \
    && name="ripgrep-${RIPGREP_VERSION}-${arch}-unknown-linux-musl" \
    && url="https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VERSION}" \
    && cd /tmp \
    && curl -fsSLO "${url}/${name}.tar.gz" \
    && curl -fsSLO "${url}/${name}.tar.gz.sha256" \
    && sha256sum -c "${name}.tar.gz.sha256" \
    && tar -xzf "${name}.tar.gz" --no-same-owner \
    && install -m 0755 "${name}/rg" /usr/local/bin/rg \
    && rm -rf "/tmp/${name}" "/tmp/${name}.tar.gz" "/tmp/${name}.tar.gz.sha256" \
    && rg --version

# Pre-seed the interpreter so a trial's `uv sync` never downloads one mid-run
# and never falls back to a system Python that differs between clients.
RUN uv python install ${PYTHON_VERSION} \
    && uv python find ${PYTHON_VERSION}

# Recorded so the running container can say what it is without re-deriving it.
ENV LOCAL_LLM_CLIENT_IMAGE=1 \
    LOCAL_LLM_PINNED_OPENCODE=${OPENCODE_VERSION} \
    LOCAL_LLM_PINNED_UV=${UV_VERSION} \
    LOCAL_LLM_PINNED_PYTHON=${PYTHON_VERSION} \
    LOCAL_LLM_PINNED_RIPGREP=${RIPGREP_VERSION}

WORKDIR /work
CMD ["/bin/bash"]
