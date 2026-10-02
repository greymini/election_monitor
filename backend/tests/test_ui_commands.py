"""Every `python -m ...` command the dashboard tells an operator to run must exist.

The data-health card, the Candidates page and the Local politics page each show
the command that loads what is missing. Three of them did not work: two named a
module that was never written (`ingest.load_csv`), and three passed `--ac` to
scripts that had no such option. Nothing checked them, so the screen that exists
to tell an operator how to fix a gap was itself the gap.

Static on purpose: it reads the frontend source and each module's argparse
calls, so it needs no database and runs everywhere.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = [
    ROOT.parent / "frontend" / "src" / "components" / "DataHealth.tsx",
    ROOT.parent / "frontend" / "src" / "pages" / "Candidates.tsx",
    ROOT.parent / "frontend" / "src" / "pages" / "LocalPolitics.tsx",
    ROOT.parent / "frontend" / "src" / "pages" / "MapExplorer.tsx",
]

COMMAND = re.compile(r"python -m ([a-z0-9_.]+)([^`\"'\n}]*)")


def commands():
    for source in SOURCES:
        for module, args in COMMAND.findall(source.read_text(encoding="utf-8")):
            yield source.name, module, args


def test_there_are_commands_to_check():
    assert len(list(commands())) >= 8


def test_every_command_names_a_module_that_exists():
    missing = []
    for source, module, _ in commands():
        path = ROOT / pathlib.Path(*module.split("."))
        if not (path.with_suffix(".py").exists() or (path / "__main__.py").exists()):
            missing.append(f"{source}: {module}")
    assert missing == [], missing


def test_every_flag_a_command_passes_is_one_its_module_defines():
    wrong = []
    for source, module, args in commands():
        text = (ROOT / pathlib.Path(*module.split("."))).with_suffix(".py").read_text(
            encoding="utf-8")
        for flag in re.findall(r"(--[a-z][a-z0-9-]*)", args):
            if f'"{flag}"' not in text:
                wrong.append(f"{source}: {module} {flag}")
    assert wrong == [], wrong
