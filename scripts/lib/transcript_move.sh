# Moving a sweep's transcripts into its directory, filtered to files written
# after the sweep started -- sourced, not executed.
#
# #112's pre-remedy evidence was destroyed because later sweeps rewrote the
# same transcript filenames. `save_transcript()` no longer overwrites, but a
# per-sweep directory is still what makes rows attributable at all, so the
# move out of the shared `~/.local-llm-bench` log dir matters.
#
# The plain `mv "$src"/*<backend>-opencode-1* "$tag/"` had a hole: a run killed
# before ITS move ran leaves transcripts behind, and the next run of the same
# arm swept them into its own directory. `old-sweep1` once held 22 transcripts
# for a 15-task sweep -- 7 leftovers from a run killed at 08:17.
#
# Filter by mtime: move only files newer than this sweep's recorded start.
# `stack_agent_ab.sh` passes the ISO stamp it writes to the sweep-order line.
# BSD find accepts `-newermt`, and this script only ever runs on the macOS
# Metal runner, so the GNU/BSD divergence never bites here.

move_transcripts_since() {
  local src=$1 out=$2 tag=$3 since=$4 backend=$5
  mkdir -p "$out/$tag"
  find "$src" -maxdepth 1 -type f -name "*${backend}-opencode-1*" \
    -newermt "$since" -exec mv {} "$out/$tag/" \; 2>/dev/null || true
}
