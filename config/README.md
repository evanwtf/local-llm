# Client configuration that lives outside the repo

These are **reference copies**. The tools read them from their real locations;
a file here configures nothing. They are tracked so a reader can reproduce a
result and a change is reviewable.

| file | where the tool reads it |
|---|---|
| `opencode.json` | `~/.config/opencode/opencode.json` |

## opencode.json

One provider per local server, each with an explicit model list. **A model that
is not listed does not resolve, and `opencode run` exits in 0.6 s with no error
text**, so the harness records a model failure (#69). `preflight.py` checks
every `opencode_model` in `tasks.toml` against the installed copy
(`$OPENCODE_CONFIG`, default `~/.config/opencode/opencode.json`) and names any
that is missing.

The `apiKey` values are placeholders, including mtplx's `$MTPLX_API_KEY`.
Local servers accept any token; nothing here is a secret.

Install:

```sh
mkdir -p ~/.config/opencode
cp config/opencode.json ~/.config/opencode/opencode.json
```

If your server ports differ, adjust each `baseURL` to match
`benchmarks/agent/tasks.toml`.
