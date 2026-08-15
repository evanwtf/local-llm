#!/bin/sh
# Interactive model cleanup — reclaim disk from superseded local models.
#
#   sh bench-0731/cleanup_models.sh            # normal (prompts per model)
#   sh bench-0731/cleanup_models.sh --dry-run  # print the plan, delete nothing
#
# Prints the full plan with sizes first, then asks per model before deleting.
# Nothing is removed without an explicit "y". Answer "q" to stop at any point.
#
# Tiers reflect how much evidence backs the recommendation:
#   A  measured in bench-0731 — superseded on the numbers
#   B  redundant by inspection (duplicate quants/tags, unusable runtimes)
#   C  no evidence either way — your call, listed for review only
#
# DELIBERATELY NOT DELETED - gemma4 MLX bf16 variants (~77 GB):
#   gemma4:26b-mlx-bf16, gemma4:e4b-mlx-bf16, gemma4:e2b-mlx-bf16
#   Ollama cannot use these for inference, so they were previously listed as
#   deletion candidates. Retained deliberately as an archive: open weights may
#   not stay freely downloadable, and bf16 is the highest-fidelity copy on hand.
#   Disk is not scarce (2.5 TiB free). Do not re-propose these.
#
# DELIBERATELY NOT DELETED (the two keepers, from REPORT.md):
#   DeepSeek-V4-Flash-Layers37-42Q4KExperts-...-fixed-0731.gguf   90.9 GiB
#       the recommendation: 76/92 eval, best resident model
#   DeepSeek-V4-Flash-MXFP4Experts-...-mxfp4-0731.gguf           145.3 GiB
#       best quality measured: 80/92, ppl 4.5078; keep for hard one-off problems
set -u

GGUF=/Users/evanhoffman/git/ds4/gguf
LINK=/Users/evanhoffman/git/ds4/ds4flash.gguf
KEEP_MIXED="$GGUF/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

# type | id | tier | reason
CANDIDATES="
gguf|DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix.gguf|A|pre-0731 baseline: 68/92 eval, slowest run (3h07m), superseded
gguf|DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf|A|q2 0731: 68/92, dominated by the mixed build at no speed advantage
gguf|DeepSeek-V4-Flash-Q4KExperts-F16HC-F16Compressor-F16Indexer-Q8Attn-Q8Shared-Q8Out-chat-v2-imatrix-0731.gguf|A|Q4 0731: loses to MXFP4 on perplexity (4.5629 vs 4.5078), speed and size
gguf|DeepSeek-V4-Flash-DSpark-support-0731.gguf|A|DSpark: measured 23-44% SLOWER at every setting; re-download ~15 min if a new checkpoint ships
gguf|GLM-5.2-UD-IQ2_XXS_RoutedIQ2XXS_blk78Q2K.gguf|A|GLM 5.2: 3.9 t/s on this machine (~10% of DeepSeek); needs two machines
ollama|hf.co/Jackrong/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-GGUF:Q8_0|B|last Qwen3.5-based model; you removed the rest of the 3.5 family
ollama|deepseek-r1:8b|B|superseded by DeepSeek V4 Flash locally; small, but an older generation
ollama|gemma4:e2b|C|one of NINE gemma4 tags - review the family, keep the sizes you use
ollama|gemma4:e4b|C|one of NINE gemma4 tags - possibly the same blob as gemma4:latest
ollama|gemma4:26b|C|one of NINE gemma4 tags
ollama|gemma4:31b|C|one of NINE gemma4 tags - 31b-mxfp8 also present
ollama|deepseek-r1:70b|C|42 GB, older reasoning generation - do you still use it?
ollama|llama4:scout|C|67 GB, untouched 2 weeks - do you still use it?
ollama|gpt-oss:120b|C|65 GB, untouched 2 weeks - gpt-oss:20b also present
"

human() { awk -v b="$1" 'BEGIN{ if(b>=1073741824) printf "%.1f GiB", b/1073741824; else if(b>=1048576) printf "%.0f MiB", b/1048576; else printf "%d B", b }'; }

size_of() {
    case "$1" in
        gguf)   [ -f "$GGUF/$2" ] && stat -f %z "$GGUF/$2" 2>/dev/null || echo 0 ;;
        ollama) ollama list 2>/dev/null | awk -v m="$2" '$1==m {
                    v=$3; u=$4
                    if (u=="GB") printf "%.0f", v*1000000000
                    else if (u=="MB") printf "%.0f", v*1000000
                    else if (u=="TB") printf "%.0f", v*1000000000000
                    else printf "0"
                }' ;;
    esac
}

echo "================================================================"
echo " Model cleanup plan"
[ "$DRY" -eq 1 ] && echo " DRY RUN - nothing will be deleted"
echo "================================================================"
echo
echo " KEEPING (not listed below, never touched by this script):"
echo "   mixed q2/q4 0731   90.9 GiB   the recommendation, 76/92 eval"
echo "   MXFP4 0731        145.3 GiB   best quality, 80/92, ppl 4.5078"
echo

total=0; found=0
printf " %-6s %-5s %10s  %s\n" TYPE TIER SIZE MODEL
printf " %-6s %-5s %10s  %s\n" ------ ----- ---------- ---------------------------
echo "$CANDIDATES" | while IFS='|' read -r type id tier reason; do
    [ -z "${type:-}" ] && continue
    sz=$(size_of "$type" "$id"); sz=${sz:-0}
    [ "$sz" -eq 0 ] && continue
    printf " %-6s %-5s %10s  %s\n" "$type" "$tier" "$(human "$sz")" "$id"
done

# recompute totals in this shell (the while above runs in a subshell)
total=0
for tier in A B C; do
    t=0
    echo "$CANDIDATES" | { while IFS='|' read -r type id tr reason; do
        [ -z "${type:-}" ] && continue
        [ "$tr" != "$tier" ] && continue
        sz=$(size_of "$type" "$id"); sz=${sz:-0}
        t=$((t + sz))
        echo "$t" > /tmp/.cleanup_tier_$tier
    done; }
    v=$(cat /tmp/.cleanup_tier_$tier 2>/dev/null || echo 0)
    rm -f /tmp/.cleanup_tier_$tier
    echo
    printf " tier %s total: %s\n" "$tier" "$(human "$v")"
    total=$((total + v))
done
echo
printf " MAXIMUM RECLAIM if you accept everything: %s\n" "$(human "$total")"
echo
echo " Tier A = measured here, superseded on the numbers"
echo " Tier B = redundant by inspection"
echo " Tier C = no evidence either way, review these yourself"
echo

# --- safety: is ds4flash.gguf pointing at something on the list? --------
if [ -L "$LINK" ]; then
    target=$(readlink "$LINK")
    tname=$(basename "$target")
    if echo "$CANDIDATES" | grep -q "|$tname|"; then
        echo "----------------------------------------------------------------"
        echo " WARNING: ds4flash.gguf currently points at a deletion candidate:"
        echo "   $tname"
        echo
        echo " Deleting it leaves a dangling symlink and 'ds4' will fail with"
        echo " its default model path."
        echo
        printf " Repoint ds4flash.gguf at the recommended mixed q2/q4 build first? [y/N] "
        read -r ans < /dev/tty
        if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
            if [ "$DRY" -eq 1 ]; then
                echo " [dry-run] would: ln -sfn <mixed q2/q4> $LINK"
            else
                ln -sfn "$KEEP_MIXED" "$LINK"
                echo " repointed -> $(basename "$(readlink "$LINK")")"
            fi
        else
            echo " left as-is; be careful deleting that model."
        fi
        echo "----------------------------------------------------------------"
        echo
    fi
fi

if [ "$DRY" -eq 1 ]; then
    echo "Dry run complete. Re-run without --dry-run to delete interactively."
    exit 0
fi

printf "Proceed to per-model confirmation? [y/N] "
read -r go < /dev/tty
case "$go" in y|Y) ;; *) echo "Aborted. Nothing deleted."; exit 0 ;; esac
echo

reclaimed=0
echo "$CANDIDATES" | while IFS='|' read -r type id tier reason; do
    [ -z "${type:-}" ] && continue
    sz=$(size_of "$type" "$id"); sz=${sz:-0}
    if [ "$sz" -eq 0 ]; then
        echo "-- skip (not present): $id"
        continue
    fi

    echo
    echo "----------------------------------------------------------------"
    echo " $id"
    echo "   type:   $type"
    echo "   size:   $(human "$sz")"
    echo "   tier:   $tier"
    echo "   reason: $reason"
    printf " Delete? [y/N/q] "
    read -r ans < /dev/tty

    case "$ans" in
        q|Q) echo " Stopping."; break ;;
        y|Y)
            case "$type" in
                gguf)
                    rm -f -- "$GGUF/$id" && echo " deleted ($(human "$sz") reclaimed)" \
                        || echo " FAILED to delete"
                    ;;
                ollama)
                    ollama rm "$id" >/dev/null 2>&1 && echo " deleted ($(human "$sz") reclaimed)" \
                        || echo " FAILED to delete"
                    ;;
            esac
            ;;
        *) echo " kept." ;;
    esac
done

echo
echo "================================================================"
echo " Done. Current free space:"
df -h /Users/evanhoffman/git | tail -1
echo
echo " Note: ollama may defer unlinking blobs; 'du -sh ~/.ollama/models'"
echo " is more accurate than df immediately after deletion."
echo "================================================================"
