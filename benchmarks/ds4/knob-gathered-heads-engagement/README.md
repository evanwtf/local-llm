# gathered-heads: the knob engages exactly 2 layers, at every frontier

Engagement evidence for the `gathered-heads` knob A/B, taken **before** the
timed run and kept separate from it. The trace prints one line per packed-FA
dispatch, so it must never be enabled inside a timed arm — the extra stderr I/O
would land on one arm and not the other.

    tree   ds4-pr952 @ 77a054e
    gguf   DeepSeek-V4-Flash-Layers37-42Q4KExperts-...-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf
    trace  DS4_METAL_TRACE_M5_FLASH_ATTN_PACKED32_REDUCE=1   (ds4_metal.m:36845)
    on     env -u DS4_METAL_DISABLE_DECODE_RAW_GATHERED_ATTN
    off    DS4_METAL_DISABLE_DECODE_RAW_GATHERED_ATTN=1
    128 generated tokens at a single frontier, one rep

| ctx | arm | `packed FA` lines | `use=1` | lines / 128 |
|---:|---|---:|---:|---:|
| 2048 | on | 5504 | 5504 | 43 layers |
| 2048 | off | 5248 | 5248 | 41 layers |
| 16384 | on | 2816 | 2816 | 22 layers |
| 16384 | off | 2560 | 2560 | 20 layers |

**The knob's effect is exactly 256 dispatches at both frontiers**, which is
128 tokens x 2 layers. Those are the two `n_comp == 0` layers, 0 and 1
(`ds4.c:1180`: `il < 2` returns ratio 0 for the FLASH variant). `n_keys` is
pinned at 128 on them regardless of context, so the knob engages the same two
layers at every frontier.

**The totals independently confirm the rest of the layout.** At ctx 2048 the off
arm runs packed FA on 41 layers -- the 21 ratio-4 plus the 20 ratio-128 layers.
At ctx 16384 only 20 remain: `n_keys = 128 + pos/ratio`, so a ratio-4 layer
gives 128 + 16384/4 = 4224, past the `n_keys <= 1024` gate at
`ds4_metal.m:36769`, while a ratio-128 layer gives 128 + 128 = 256 and stays in.
21 layers drop out; 20 remain; the 2 uncompressed layers are unaffected.

Every number here was predicted from source before it was measured: 2 affected
layers, 21 ratio-4, 20 ratio-128, 43 total. `use=1` on every line means the
pipeline was selected, not merely requested.

**This is what makes the timed A/B interpretable.** A flat result now means "2
layers of dispatch overhead is below the noise floor", which is a finding. It
cannot mean "the knob did nothing", because the knob demonstrably moved 256
dispatches per 128 tokens at both ends of the sweep.
