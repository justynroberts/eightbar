#!/usr/bin/env python3
"""Install the AbletonAI remote script into Ableton Live.

Defaults to the User Library, which is the only location that survives on
modern macOS: Sequoia and later actively revert writes into a signed app
bundle, so a script copied into Ableton's own bundle looks installed, passes a
read-back check, and is then silently gone by the next launch.

Use --bundle only if the User Library location genuinely does not show up in
Live's Control Surface dropdown, and expect to reinstall after every Live
update and macOS bundle re-validation.

    python3 install_remote_script.py             # User Library (recommended)
    python3 install_remote_script.py --bundle    # inside the Ableton app
    python3 install_remote_script.py --uninstall
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
from pathlib import Path

SOURCE = Path(__file__).parent / "remote_script" / "AbletonAI"
NAME = "AbletonAI"


def bundle_targets() -> list[Path]:
    found = []
    for app in sorted(glob.glob("/Applications/Ableton Live*.app")):
        path = Path(app) / "Contents/App-Resources/MIDI Remote Scripts"
        if path.is_dir():
            found.append(path)
    return found


def user_target() -> Path:
    return Path.home() / "Music/Ableton/User Library/Remote Scripts"


def install(targets: list[Path]) -> int:
    if not SOURCE.is_dir():
        print(f"error: {SOURCE} not found", file=sys.stderr)
        return 1

    installed = 0
    for target in targets:
        destination = target / NAME
        try:
            target.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(SOURCE, destination)
            # Stale bytecode from a previous version shadows the new source.
            cache = destination / "__pycache__"
            if cache.exists():
                shutil.rmtree(cache)
            print(f"installed -> {destination}")
            installed += 1
        except PermissionError:
            print(
                f"permission denied writing {destination}\n"
                f"  retry with: sudo python3 {Path(__file__).name}",
                file=sys.stderr,
            )
        except OSError as exc:
            print(f"failed writing {destination}: {exc}", file=sys.stderr)

    if installed:
        print(
            "\nNext:\n"
            "  1. Quit and reopen Ableton Live (scripts load only at launch).\n"
            "  2. Preferences > Link, Tempo & MIDI > Control Surface -> AbletonAI\n"
            "  3. You should see 'AbletonAI: listening on port 9878' in the status bar.\n"
            "  4. Check it with: python3 -m ableton_ai.cli --check"
        )
    return 0 if installed else 1


def uninstall(targets: list[Path]) -> int:
    for target in targets:
        destination = target / NAME
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
            print(f"removed -> {destination}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", action="store_true",
                        help="install inside the Ableton app bundle (fragile on "
                             "macOS Sequoia and later)")
    parser.add_argument("--user", action="store_true",
                        help="install to the User Library (the default)")
    parser.add_argument("--all", action="store_true",
                        help="install to both locations")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    if args.all:
        targets = [user_target()] + bundle_targets()
    elif args.bundle:
        targets = bundle_targets()
        if not targets:
            print("error: no Ableton Live installation found in /Applications",
                  file=sys.stderr)
            return 1
        print(
            "warning: writing into Ableton's app bundle. macOS Sequoia and later\n"
            "         revert changes to signed bundles, so this may silently\n"
            "         disappear on the next launch.\n",
            file=sys.stderr,
        )
    else:
        targets = [user_target()]

    if args.uninstall:
        # Always clean both locations, whichever was asked for.
        return uninstall([user_target()] + bundle_targets())
    return install(targets)


if __name__ == "__main__":
    raise SystemExit(main())
