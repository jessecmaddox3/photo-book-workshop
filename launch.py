"""Small first-run helper. Creates a local environment, then starts the invented demo."""

from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import venv


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path.home() / "Photo Book Workshop" / "Example",
    )
    parser.add_argument(
        "--demo-only",
        action="store_true",
        help="Build the example and exit instead of opening the review app.",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 12):
        raise RuntimeError(
            "Install Python 3.12 or newer from python.org/downloads, then try again."
        )
    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print(
            "Creating this project’s own Python environment. Your photos are not being read.",
            flush=True,
        )
        venv.EnvBuilder(with_pip=True).create(environment)
    files = [
        root / "pyproject.toml",
        root / "requirements-launch.txt",
        *sorted((root / "src").rglob("*")),
    ]
    signature = hashlib.sha256(
        b"".join(
            p.relative_to(root).as_posix().encode() + p.read_bytes()
            for p in files
            if p.is_file() and "__pycache__" not in p.parts
        )
    ).hexdigest()
    marker = environment / "photobook-install.txt"
    if not marker.exists() or marker.read_text() != signature:
        print(
            "Installing Photo Book Workshop and its dependencies. First setup needs internet; the example itself is local.",
            flush=True,
        )
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--constraint",
                str(root / "requirements-launch.txt"),
                str(root),
            ],
            check=True,
            cwd=root,
        )
        marker.write_text(signature)
    command = [
        str(python),
        "-m",
        "photobook_workshop",
        "demo" if args.demo_only else "start",
        "--directory",
        str(args.directory.resolve()),
    ]
    if args.no_browser and not args.demo_only:
        command.append("--no-browser")
    print("Example folder: " + str(args.directory.resolve()), flush=True)
    return subprocess.call(command, cwd=root)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(
            "Setup could not finish: "
            + str(error)
            + "\nSee docs/GETTING-STARTED.md for the manual steps.",
            file=sys.stderr,
        )
        raise SystemExit(1)
