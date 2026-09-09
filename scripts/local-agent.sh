#!/bin/zsh
# Start a recommended local stack and drop into a coding agent.
#
#   scripts/local-agent.sh <stack> [client] [-- agent args...]
#
#   stacks   starter | fast | mainline | lineage   (see RECOMMENDATIONS.md)
#   client   opencode (default) | claude
#
# It fetches the weights if they are missing, fetches and builds the engine if
# it is missing, starts the server and whatever shim that stack needs, waits
# until the endpoint actually answers, and then execs the agent.
#
# Two things it deliberately does NOT do:
#
#   - It never downloads tens of gigabytes without asking. Every fetch prints
#     the size and waits for a yes.
#   - It never starts a second engine when one is already listening on the
#     port. One model at a time is the rule the benchmark runs under, and it
#     is the rule here for the same reason: this machine cannot hold two.
set -u
REPO="${0:A:h:h}"
LOG_DIR="${LOCAL_AGENT_LOG_DIR:-$HOME/.local-llm-agent}"
mkdir -p "$LOG_DIR"

die()  { print -u2 -- "error: $*"; exit 1; }
say()  { print -- "  $*"; }
step() { print -- "[$(date '+%H:%M:%S')] $*"; }

confirm() {  # confirm <prompt> ; honours LOCAL_AGENT_YES=1
  [[ "${LOCAL_AGENT_YES:-}" == 1 ]] && { say "auto-yes: $1"; return 0; }
  print -n -- "$1 [y/N] "
  read -r reply
  [[ "$reply" == [yY]* ]]
}

listening() { nc -z 127.0.0.1 "$1" 2>/dev/null; }

wait_ready() {  # wait_ready <port> <label> <seconds>
  local port=$1 label=$2 limit=${3:-600} waited=0
  step "waiting for $label on :$port"
  while (( waited < limit )); do
    listening "$port" && { say "$label is up after ${waited}s"; return 0; }
    sleep 3; (( waited += 3 ))
  done
  die "$label did not come up on :$port within ${limit}s -- see $LOG_DIR"
}

# --- stack table -------------------------------------------------------------
# Everything that differs between stacks lives here and nowhere else.
STACK="${1:-}"
CLIENT="${2:-opencode}"
[[ -n "$STACK" ]] || die "usage: local-agent.sh <starter|fast|mainline|lineage> [opencode|claude] [--check] [-- args]"
# Validate the client HERE, not at the point of use. Checking it after the
# weights and the engine would mean a typo'd client name costs a 105 GB
# download and a model load before it is reported.
case "$CLIENT" in
  opencode|claude) ;;
  *) die "unknown client '$CLIENT' (opencode|claude)" ;;
esac
# --check stops after the report, before anything is fetched or started.
CHECK_ONLY=0
for arg in "$@"; do [[ "$arg" == "--check" ]] && CHECK_ONLY=1; done

case "$STACK" in
  starter)
    LABEL="Qwen3.6-27B-coding on Ollama (slot 1: starting out)"
    ENGINE=ollama; ENGINE_PORT=11434
    OLLAMA_TAG="qwen3.6:27b-coding-mxfp8"; DL_SIZE="31 GB"
    OPENCODE_MODEL="ollama/qwen3.6:27b-coding-mxfp8"
    OPENCODE_BASEURL="http://127.0.0.1:11434/v1"
    CLAUDE_PORT=11500; CLAUDE_UPSTREAM="http://127.0.0.1:11434"
    CLAUDE_MODEL="qwen3.6:27b-coding-mxfp8"; CLAUDE_TOKEN="ollama"; CTX=262144
    ;;
  fast)
    LABEL="Qwen3.8-Flash-Next Q4_K imatrix on ds4 (slot 2: you want it fast)"
    ENGINE=ds4; ENGINE_PORT=8000; SHIM_PORT=8101
    ENGINE_TREE="$HOME/git/ds4-ivan-qwen38fn"
    ENGINE_REPO="https://github.com/ivanfioravanti/ds4.git"; ENGINE_BRANCH="qwen3.8-flash-next"
    MODEL_DIR="$HOME/models/qwen3.8-flash-next-ds4-q4k-imatrix"
    MODEL_FILE="$MODEL_DIR/Qwen3.8-Flash-Next-Q4KImatrixExperts-MXFP4Down-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
    PLE_FILE="$MODEL_DIR/Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
    HF_REPO="ivanfioravanti/Qwen3.8-Flash-Next-DS4-Q4"; DL_SIZE="105 GB (weights + a 30 GB PLE sidecar)"
    OPENCODE_MODEL="ds4qwenshim/qwen3.8-flash-next-q4"
    OPENCODE_BASEURL="http://127.0.0.1:8101/v1"
    CLAUDE_PORT=8101; CLAUDE_MODEL="qwen3.8-flash-next-q4"; CLAUDE_TOKEN="dsv4-local"; CTX=100000
    ;;
  mainline)
    LABEL="Qwen3.8-Flash-Next UD-Q3_K_XL on llama.cpp (the mainline fallback)"
    ENGINE=llamacpp; ENGINE_PORT=8020
    ENGINE_TREE="$HOME/git/llama.cpp"; ENGINE_REPO="https://github.com/ggml-org/llama.cpp"
    MODEL_DIR="$HOME/models/Qwen3.8-Flash-Next-GGUF/UD-Q3_K_XL"
    MODEL_FILE="$MODEL_DIR/Qwen3.8-Flash-Next-UD-Q3_K_XL-00001-of-00003.gguf"
    HF_REPO="unsloth/Qwen3.8-Flash-Next-GGUF"; HF_INCLUDE="UD-Q3_K_XL/*"; DL_SIZE="84 GB"
    OPENCODE_MODEL="llamacpp/qwen3.8-flash-next-q3"
    OPENCODE_BASEURL="http://127.0.0.1:8020/v1"
    CLAUDE_PORT=11500; CLAUDE_UPSTREAM="http://127.0.0.1:8020"
    CLAUDE_MODEL="qwen3.8-flash-next-q3"; CLAUDE_TOKEN="llamacpp-local"; CTX=131072
    ;;
  lineage)
    LABEL="DeepSeek-V4-Flash on ds4 (slot 3: a second lineage)"
    ENGINE=ds4; ENGINE_PORT=8000
    ENGINE_TREE="$HOME/git/ds4-ivan-qwen38fn"
    ENGINE_REPO="https://github.com/ivanfioravanti/ds4.git"; ENGINE_BRANCH="qwen3.8-flash-next"
    MODEL_DIR="$HOME/git/ds4/gguf"
    MODEL_FILE="$MODEL_DIR/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"
    DL_SIZE="91 GB"
    OPENCODE_MODEL="ds4/deepseek-v4-flash"
    OPENCODE_BASEURL="http://127.0.0.1:8000/v1"
    CLAUDE_PORT=8000; CLAUDE_MODEL="deepseek-v4-flash"; CLAUDE_TOKEN="dsv4-local"; CTX=100000
    ;;
  *) die "unknown stack '$STACK' (starter|fast|mainline|lineage)" ;;
esac

print -- "=== $LABEL"
say "client: $CLIENT"
if (( CHECK_ONLY )); then
  say "engine port :$ENGINE_PORT $(listening $ENGINE_PORT && print -- '(listening)' || print -- '(free)')"
  [[ -n "${MODEL_FILE:-}" ]] && say "weights: $([[ -f "$MODEL_FILE" ]] && print -- present || print -- MISSING) $MODEL_FILE"
  [[ -n "${OLLAMA_TAG:-}" ]] && say "ollama tag: $OLLAMA_TAG"
  say "--check: stopping before any download, build or server start"
  exit 0
fi

# --- 1. weights --------------------------------------------------------------
fetch_hf() {  # fetch_hf <repo> <local-dir> [include-glob]
  command -v hf >/dev/null || die "the 'hf' CLI is not installed: pip install -U huggingface_hub"
  if [[ -n "${3:-}" ]]; then
    HF_HUB_ENABLE_HF_TRANSFER=1 hf download "$1" --include "$3" --local-dir "$2"
  else
    HF_HUB_ENABLE_HF_TRANSFER=1 hf download "$1" --local-dir "$2"
  fi
}

if [[ "$ENGINE" == ollama ]]; then
  command -v ollama >/dev/null || die "ollama is not installed: brew install ollama"
  if ! ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$OLLAMA_TAG"; then
    step "model $OLLAMA_TAG is not present ($DL_SIZE)"
    confirm "  download $OLLAMA_TAG ($DL_SIZE)?" || die "declined"
    ollama pull "$OLLAMA_TAG" || die "ollama pull failed"
  else
    say "model present: $OLLAMA_TAG"
  fi
else
  if [[ ! -f "$MODEL_FILE" ]]; then
    step "weights are missing: $MODEL_FILE"
    [[ -n "${HF_REPO:-}" ]] || die "no download source recorded for '$STACK' -- fetch it by hand"
    confirm "  download $HF_REPO ($DL_SIZE)?" || die "declined"
    mkdir -p "$MODEL_DIR"
    fetch_hf "$HF_REPO" "$MODEL_DIR" "${HF_INCLUDE:-}" || die "download failed"
  else
    say "weights present: ${MODEL_FILE:t}"
  fi
  # The kimat stack needs a PLE sidecar as well, and missing it is the single
  # easiest way to get a confusing failure rather than a clear one.
  if [[ -n "${PLE_FILE:-}" && ! -e "$PLE_FILE" ]]; then
    step "PLE sidecar missing: $PLE_FILE"
    confirm "  download the PLE sidecar (30 GB)?" || die "declined"
    fetch_hf "$HF_REPO" "$MODEL_DIR" "*PLE*" || die "PLE download failed"
  fi
fi

# --- 2. engine ---------------------------------------------------------------
case "$ENGINE" in
  ds4)
    if [[ ! -x "$ENGINE_TREE/ds4-server" ]]; then
      step "ds4 engine not built at $ENGINE_TREE"
      confirm "  clone and build ds4 (a few minutes)?" || die "declined"
      [[ -d "$ENGINE_TREE/.git" ]] || git clone "$ENGINE_REPO" "$ENGINE_TREE" || die "clone failed"
      ( cd "$ENGINE_TREE" && git checkout "$ENGINE_BRANCH" && make ) || die "build failed"
    else
      say "engine: $ENGINE_TREE @ $(git -C "$ENGINE_TREE" rev-parse --short HEAD 2>/dev/null)"
    fi
    ;;
  llamacpp)
    if [[ ! -x "$ENGINE_TREE/build/bin/llama-server" ]]; then
      step "llama.cpp not built at $ENGINE_TREE"
      confirm "  clone and build llama.cpp (a few minutes)?" || die "declined"
      [[ -d "$ENGINE_TREE/.git" ]] || git clone "$ENGINE_REPO" "$ENGINE_TREE" || die "clone failed"
      cmake -B "$ENGINE_TREE/build" -S "$ENGINE_TREE" -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release || die "cmake failed"
      cmake --build "$ENGINE_TREE/build" --config Release -j || die "build failed"
    else
      say "engine: llama.cpp @ $(git -C "$ENGINE_TREE" rev-parse --short HEAD 2>/dev/null)"
    fi
    ;;
  ollama) say "engine: ollama $(ollama --version 2>/dev/null | tail -1)" ;;
esac

# --- 3. server ---------------------------------------------------------------
if listening "$ENGINE_PORT"; then
  say "something is already listening on :$ENGINE_PORT -- reusing it, not starting a second engine"
else
  step "starting $ENGINE on :$ENGINE_PORT"
  case "$ENGINE" in
    ds4)
      PLE_ARG=(); [[ -n "${PLE_FILE:-}" ]] && PLE_ARG=(--ple "$PLE_FILE")
      ( cd "$ENGINE_TREE" && nohup ./ds4-server --metal -m "$MODEL_FILE" "${PLE_ARG[@]}" \
          --ctx "$CTX" --warm-weights --host 127.0.0.1 --port "$ENGINE_PORT" \
          > "$LOG_DIR/ds4-server.log" 2>&1 & )
      ;;
    llamacpp)
      ( nohup "$ENGINE_TREE/build/bin/llama-server" -m "$MODEL_FILE" \
          -a "$CLAUDE_MODEL" --host 127.0.0.1 --port "$ENGINE_PORT" \
          -c "$CTX" -np 1 --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0 \
          > "$LOG_DIR/llama-server.log" 2>&1 & )
      ;;
    ollama) ( nohup ollama serve > "$LOG_DIR/ollama.log" 2>&1 & ) ;;
  esac
  wait_ready "$ENGINE_PORT" "$ENGINE" 900
fi

# --- 4. shims ----------------------------------------------------------------
# The ds4 Qwen stack always needs its tool-format shim: #112 measured the
# scaffolding strip as worth 23 points of pass rate, so this is not optional
# plumbing, it is part of the stack.
if [[ -n "${SHIM_PORT:-}" ]] && ! listening "$SHIM_PORT"; then
  step "starting the ds4 Qwen tool shim on :$SHIM_PORT"
  ( cd "$REPO" && nohup uv run python ds4_qwen_tool_shim.py \
      --upstream "http://127.0.0.1:$ENGINE_PORT" --port "$SHIM_PORT" \
      > "$LOG_DIR/qwen-tool-shim.log" 2>&1 & )
  wait_ready "$SHIM_PORT" "tool shim" 120
fi

# Claude Code speaks the Anthropic wire. Ollama and llama.cpp do not, so those
# two stacks need the translating shim in front before `claude` will talk to
# them at all.
if [[ "$CLIENT" == claude && -n "${CLAUDE_UPSTREAM:-}" ]] && ! listening "$CLAUDE_PORT"; then
  step "starting the Anthropic-wire shim on :$CLAUDE_PORT"
  ( cd "$REPO" && nohup uv run python ollama_claude_shim.py \
      --port "$CLAUDE_PORT" --upstream "$CLAUDE_UPSTREAM" \
      > "$LOG_DIR/claude-shim.log" 2>&1 & )
  wait_ready "$CLAUDE_PORT" "Anthropic shim" 120
fi

# --- 5. client ---------------------------------------------------------------
# Drop the positional stack/client, then anything up to and including "--",
# so the rest passes through to the agent untouched.
(( $# )) && shift $(( $# < 2 ? $# : 2 ))
AGENT_ARGS=()
seen_sep=0
for arg in "$@"; do
  [[ "$arg" == "--check" ]] && continue
  if (( seen_sep )); then AGENT_ARGS+=("$arg"); continue; fi
  [[ "$arg" == "--" ]] && { seen_sep=1; continue; }
  AGENT_ARGS+=("$arg")
done

case "$CLIENT" in
  opencode)
    # OpenCode resolves a model only if its provider is declared in the config
    # file, which lives outside this repo. #69: an undeclared model made
    # `opencode run` exit in 0.6s and six client crashes were recorded as six
    # model failures. Declare it rather than let that happen again.
    CONFIG="${OPENCODE_CONFIG:-$HOME/.config/opencode/opencode.json}"
    uv run --directory "$REPO" python - "$CONFIG" "$OPENCODE_MODEL" "$OPENCODE_BASEURL" <<'PY' || die "could not update the OpenCode config"
import json, pathlib, sys
cfg, model, base = pathlib.Path(sys.argv[1]).expanduser(), sys.argv[2], sys.argv[3]
provider, name = model.split("/", 1)
data = json.loads(cfg.read_text()) if cfg.exists() else {}
prov = data.setdefault("provider", {}).setdefault(provider, {})
prov.setdefault("npm", "@ai-sdk/openai-compatible")
prov.setdefault("options", {})["baseURL"] = base
if name not in (prov.setdefault("models", {})):
    prov["models"][name] = {}
    print(f"  declared {model} in {cfg}")
cfg.parent.mkdir(parents=True, exist_ok=True)
cfg.write_text(json.dumps(data, indent=2) + "\n")
PY
    step "starting opencode on $OPENCODE_MODEL"
    exec opencode --model "$OPENCODE_MODEL" "${AGENT_ARGS[@]}"
    ;;
  claude)
    step "starting claude on $CLAUDE_MODEL"
    unset ANTHROPIC_API_KEY
    export ANTHROPIC_BASE_URL="http://127.0.0.1:$CLAUDE_PORT"
    export ANTHROPIC_AUTH_TOKEN="$CLAUDE_TOKEN"
    export ANTHROPIC_MODEL="$CLAUDE_MODEL"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="$CLAUDE_MODEL"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="$CLAUDE_MODEL"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="$CLAUDE_MODEL"
    export CLAUDE_CODE_MAX_CONTEXT_TOKENS="$CTX"
    exec claude "${AGENT_ARGS[@]}"
    ;;
  *) die "unknown client '$CLIENT' (opencode|claude)" ;;
esac
