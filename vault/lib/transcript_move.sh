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
# stamps a marker file to that instant (BSD/Metal `touch -t CCYYMMDDhhmm.ss`
# sets it correctly there) and passes its PATH; this helper compares with
# `find -newer`, which is POSIX on both the BSD find the Metal run uses and
# the GNU find CI runs. Passing a path, not a date string, keeps the two
# platforms on one code path -- their `touch -t` parsers disagree on the token
# form, and a test must be faithful on either platform.

move_transcripts_since() {
  local src=$1 out=$2 tag=$3 marker=$4 backend=$5
  mkdir -p "$out/$tag"
  find "$src" -maxdepth 1 -type f -name "*${backend}-opencode-1*" \
    -newer "$marker" -exec mv {} "$out/$tag/" \; 2>/dev/null || true
}
