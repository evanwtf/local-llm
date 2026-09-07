# Moving a sweep's transcripts into its directory, filtered to files written
# after the sweep started -- sourced, not executed.
#
# #112's pre-remedy evidence was destroyed because later sweeps rewrote the
# same transcript filenames. `save_transcript()` no longer overwrites, but a
# per-sweep directory is still what makes rows attributable at all, so the
# move out of the shared log dir matters.
#
# The plain `mv "$src"/*<backend>-opencode-1* "$tag/"` had a hole: a run killed
# before ITS move ran leaves transcripts behind, and the next run of the same
# arm swept them into its own directory. `old-sweep1` once held 22 transcripts
# for a 15-task sweep -- 7 leftovers from a run killed at 08:17.
#
# Filter by mtime: move only files newer than this sweep's start. The caller
# passes that instant as `touch -t` (CCYYMMDDhhmm.ss), and this helper stamps a
# throwaway marker with it, then finds with `-newer` -- POSIX on both the BSD
# find the Metal run uses and the GNU find CI runs, unlike `-newermt`, whose
# date string BSD and GNU parse differently.

move_transcripts_since() {
  local src=$1 out=$2 tag=$3 mtime_tok=$4 backend=$5
  local marker
  mkdir -p "$out/$tag"
  marker="$(mktemp "${TMPDIR:-/tmp}/transcript-move.XXXXXX")"
  : > "$marker"
  touch -t "$mtime_tok" "$marker"
  find "$src" -maxdepth 1 -type f -name "*${backend}-opencode-1*" \
    -newer "$marker" -exec mv {} "$out/$tag/" \; 2>/dev/null || true
  rm -f "$marker"
}
