"""Generate scripts/README.md from each script's own first docstring line."""

import ast, pathlib, re

root = pathlib.Path("scripts")
rows = []
for p in sorted(root.glob("*.py")) + sorted(root.glob("*.sh")):
    if p.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(p.read_text())) or ""
        except SyntaxError:
            doc = ""
        first = doc.strip().splitlines()[0] if doc.strip() else ""
    else:
        lines = [ln for ln in p.read_text().splitlines()[:6] if ln.startswith("#")]
        body = [ln.lstrip("# ").strip() for ln in lines if not ln.startswith("#!")]
        first = body[0] if body else ""
    issues = sorted(set(re.findall(r"#(\d{1,4})\b", first)), key=int)
    rows.append((p.name, first, issues))

out = [
    """# scripts/

One line per script, taken from its own docstring so this file cannot drift
into describing something the script no longer does. Regenerate with:

    uv run python scripts/make_scripts_readme.py

Each script explains itself in full at the top of its own file -- what it
computes, and why that and not a neighbouring thing. This is an index, not
documentation.

| script | what it does |
|---|---|"""
]
for name, first, _ in rows:
    out.append(f"| `{name}` | {first or '(no description)'} |")
out.append("")
pathlib.Path("scripts/README.md").write_text("\n".join(out) + "\n")
print(f"indexed {len(rows)} scripts")
