"""Build the Orchestrate tool package and import the nyp_ tools and agents.

    venv\\Scripts\\python.exe scripts\\deploy.py          # build + check only
    venv\\Scripts\\python.exe scripts\\deploy.py --run    # also import

Orchestrate uploads everything under the tool's package root, so the tools
are not imported from the repo root (that would ship venv/, .git/ and
data/golden/answer_key.json). Instead this builds a fresh nyp_package/ in
the system temp folder, with only what the tools need:

    closer/          minus cli.py, eval/ and data/generator.py
    tools/
    data/golden/     the six agent-visible files, never answer_key.json
    config/          logins.json (login -> staff ID), if present; gitignored
    requirements.txt

The temp folder keeps the build out of OneDrive, which locks files it is
still syncing and makes clearing a build folder in the repo fail.

Run `python -m closer.cli generate` first if data/golden/ is missing.
--run needs an active environment (Bob's Environment Manager, or
`orchestrate env activate`).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = Path(tempfile.mkdtemp(prefix="nyp_deploy_")) / "nyp_package"

AGENT_FILES = (
    "august_payouts.json",
    "bank.csv",
    "crew_messages.json",
    "pos_orders.json",
    "roadshow.csv",
    "settlements.csv",
)
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "cli.py", "eval", "generator.py")

# Pinned to the versions the tests run on; the tool runtime is Python 3.12.
REQUIREMENTS = """\
ibm-watsonx-orchestrate==2.18.2
pydantic==2.14.1
PyYAML==6.0.3
"""

# Collaborators before the supervisor that names them.
AGENTS = (
    "nyp_intake_agent",
    "nyp_reconcile_agent",
    "nyp_commission_agent",
    "nyp_close_supervisor",
    "nyp_closer",
)


def build() -> list[Path]:
    golden = ROOT / "data" / "golden"
    missing = [f for f in AGENT_FILES if not (golden / f).is_file()]
    if missing:
        sys.exit(f"data/golden is missing {missing}; run `python -m closer.cli generate` first.")

    shutil.copytree(ROOT / "closer", BUILD / "closer", ignore=SKIP)
    shutil.copytree(ROOT / "tools", BUILD / "tools", ignore=SKIP)
    (BUILD / "data" / "golden").mkdir(parents=True)
    for name in AGENT_FILES:
        shutil.copy2(golden / name, BUILD / "data" / "golden" / name)
    (BUILD / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
    logins = ROOT / "config" / "logins.json"
    if logins.is_file():
        (BUILD / "config").mkdir()
        shutil.copy2(logins, BUILD / "config" / "logins.json")
    else:
        print("Note: config/logins.json is missing, so nobody can approve a payout.")

    leaked = [p for p in BUILD.rglob("*") if p.name in ("answer_key.json", "generator.py", ".env")]
    if leaked:
        sys.exit(f"Refusing to deploy, package contains {leaked}.")
    return sorted((BUILD / "tools").glob("nyp_*.py"))


def check(tool_files: list[Path]) -> None:
    """Import every tool from the package alone, the way the runtime will."""
    modules = ", ".join(f"tools.{p.stem}" for p in tool_files)
    code = (
        "import sys; sys.path.insert(0, '.'); "
        f"import closer, {modules}; "
        "from pathlib import Path; "
        "assert Path(closer.__file__).resolve().is_relative_to(Path('.').resolve()), closer.__file__; "
        "print('imports ok')"
    )
    subprocess.run([sys.executable, "-I", "-c", code], cwd=BUILD, check=True)


def orchestrate_exe() -> str:
    """The venv's orchestrate CLI, even when the venv is not activated."""
    found = shutil.which("orchestrate", path=str(Path(sys.executable).parent))
    return found or "orchestrate"


def commands(tool_files: list[Path]) -> list[list[str]]:
    exe = orchestrate_exe()
    cmds = [
        [exe, "tools", "import", "-k", "python",
         "-p", str(BUILD), "-f", str(tool), "-r", str(BUILD / "requirements.txt")]
        for tool in tool_files
    ]
    cmds += [
        [exe, "tools", "import", "-k", "flow", "-f", str(flow)]
        for flow in sorted((ROOT / "flows").glob("nyp_*.py"))
    ]
    cmds += [
        [exe, "agents", "import", "-f", f"agents/{name}.agent.yaml"]
        for name in AGENTS
    ]
    return cmds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true", help="import into the active environment")
    args = parser.parse_args()

    tool_files = build()
    check(tool_files)
    print(f"Package built in {BUILD}")
    for cmd in commands(tool_files):
        print(" ".join(cmd))
        if args.run:
            subprocess.run(cmd, cwd=ROOT, check=True)
    if not args.run:
        print("Build ok. Re-run with --run to import into the active environment.")


if __name__ == "__main__":
    main()
