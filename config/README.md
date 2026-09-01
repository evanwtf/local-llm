# Client configuration that lives outside the repo

These are **reference copies**. The tools read them from their real locations;
copying a file here does not configure anything. They are tracked so that a
reader can reproduce a result, and so a change to them is reviewable.

| file | where the tool actually reads it |
|---|---|
| `opencode.json` | `~/.config/opencode/opencode.json` |

## opencode.json

Declares one provider per local server, each with an explicit model list.
**A model that is not listed does not resolve, and `opencode run` exits in
0.6 s with no error text** — the harness then records a model failure. That is
#69, and it is how six client crashes became GLM-5.3's entire published
OpenCode record.

`benchmarks/agent/preflight.py` now checks every `opencode_model` in
`tasks.toml` against this file before a batch, and says which one is missing.

The `apiKey` values are placeholders. Local servers accept any token; nothing
here is a secret. `mtplx` reads `$MTPLX_API_KEY` from the environment.

To install:

```sh
mkdir -p ~/.config/opencode
cp config/opencode.json ~/.config/opencode/opencode.json
```

Adjust `baseURL` ports if your servers differ from the ones in
`benchmarks/agent/tasks.toml`.
