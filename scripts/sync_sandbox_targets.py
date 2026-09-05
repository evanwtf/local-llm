"""Clone the harness's own copies of the task repositories into `sandbox/`.

The agent's export is built from these, never from the operator's working
copies in ~/git. See .gitignore for why: a run used to rename ~/git/monitor
aside and stand an excised export in its place, which made the operator's only
usable checkout a load-bearing part of the harness without telling him.

Every path this writes is under `sandbox/`. It reads ~/git only to learn a
remote URL, and refuses to write there.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import subprocess
import sys
import tomllib

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "sandbox"
AGENT = ROOT / "benchmarks" / "agent"
TASKS = AGENT / "tasks.toml"

# Import the harness's own resolver rather than re-deriving where a task
# points. Tasks inherit file-level `repo` and `base_commit` defaults, and a
# second implementation of that rule is how run.py and provenance ended up
# disagreeing about what "dirty" means.
sys.path.insert(0, str(AGENT))
import run as harness


def git(args: list[str], cwd: pathlib.Path) -> str:
    got = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if got.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {cwd}: {got.stderr.strip()}")
    return got.stdout.strip()


def targets(tasks_file: pathlib.Path) -> dict[str, str]:
    """{repo path as configured: base_commit}, one entry per distinct repo.

    A repo named by several tasks must be pinned to one commit; two commits for
    one checkout cannot both be satisfied, and silently taking the last would
    make half the tasks unbuildable in a way nothing reports.
    """
    cfg = tomllib.loads(tasks_file.read_text())
    found: dict[str, str] = {}
    for task in cfg.get("task", []):
        # A script task starts from an empty directory: no repo to clone.
        if task.get("kind") == "script":
            continue
        target = harness.task_target(cfg, task)
        repo, commit = target["repo"], target["base_commit"]
        if repo in found and found[repo] != commit:
            raise SystemExit(
                f"{repo} is pinned to both {found[repo]} and {commit} "
                f"(task {task.get('name')}); one checkout cannot be at "
                f"two commits."
            )
        found[repo] = commit
    return found


def origin_of(configured: pathlib.Path) -> str:
    """The remote to clone from, read from the operator's checkout.

    Read-only. If his copy is not there, say so rather than guessing a URL --
    a wrong guess clones some other project and every task then fails on a
    missing file, which reads as a model result.
    """
    # Resolve through the harness: mid-batch the configured path holds the
    # EXPORT, which has a .git but no origin, and the operator's checkout is
    # parked elsewhere. Asking the configured path fails with "No such remote"
    # for a reason that has nothing to do with the operator's setup.
    real = harness.guarded_repo(configured)
    if not (real / ".git").exists():
        raise SystemExit(
            f"{real} is not a git checkout, so there is no remote to clone "
            f"from. Clone it there first, or pass --origin NAME=URL."
        )
    try:
        return git(["remote", "get-url", "origin"], real)
    except RuntimeError as exc:
        raise SystemExit(
            f"{real} has no `origin` remote ({exc}). Pass --origin NAME=URL."
        ) from exc


def sync_one(name: str, url: str, commit: str, *, dry_run: bool) -> pathlib.Path:
    dest = SANDBOX / name
    if dry_run:
        logger.info("would sync %s -> %s at %s", url, dest, commit)
        return dest
    SANDBOX.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        logger.info("cloning %s -> %s", url, dest)
        subprocess.run(["git", "clone", "--quiet", url, str(dest)], check=True)
    else:
        logger.info("fetching %s", dest)
        git(["fetch", "--quiet", "--all", "--tags"], dest)
    # Detached: the sandbox copy tracks a commit, never a branch. A branch
    # would drift the moment upstream moved, and the pinned commit is the
    # entire basis for comparing a row against an older cohort.
    git(["checkout", "--quiet", "--detach", commit], dest)
    git(["reset", "--hard", "--quiet", commit], dest)
    git(["clean", "-qfdx"], dest)
    head = git(["rev-parse", "--short", "HEAD"], dest)
    dirty = git(["status", "--porcelain"], dest)
    logger.info("%s at %s, dirty=%s", dest, head, bool(dirty))
    if dirty:
        raise SystemExit(f"{dest} is dirty right after a clean checkout")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-file", type=pathlib.Path, default=TASKS)
    parser.add_argument(
        "--origin",
        action="append",
        default=[],
        metavar="NAME=URL",
        help="clone NAME from URL instead of reading the operator's checkout",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    overrides = dict(o.split("=", 1) for o in args.origin)
    for configured, commit in sorted(targets(args.tasks_file).items()):
        path = pathlib.Path(configured).expanduser()
        name = path.name
        url = overrides.get(name) or origin_of(path)
        sync_one(name, url, commit, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
