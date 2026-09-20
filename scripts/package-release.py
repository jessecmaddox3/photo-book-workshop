"""Build a deterministic source ZIP from an explicit, reviewed file allowlist."""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def package(root, output):
    root = Path(root).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Choose a new release archive")
    names = (root / "release-files.txt").read_text(encoding="utf-8").splitlines()
    if not names or len(set(names)) != len(names):
        raise ValueError("Missing or duplicate release paths")
    for name in names:
        path = root / name
        parts = PurePosixPath(name).parts
        if (
            not parts
            or PurePosixPath(name).is_absolute()
            or ".." in parts
            or "\\" in name
            or not path.resolve(strict=True).is_relative_to(root)
            or any(
                (root / Path(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)
            )
            or not path.is_file()
        ):
            raise ValueError("Unsafe release path: " + name)
        if any(
            part
            in {
                ".git",
                ".venv",
                "__pycache__",
                ".env",
                "catalogs",
                "books",
                "review",
                "previews",
                "api-workspace",
            }
            for part in parts
        ) or path.suffix in {".sqlite3", ".log"}:
            raise ValueError(
                "Private/runtime file is in the release allowlist: " + name
            )
    for manifest in ["docs/artwork.json", "docs/fonts.json"]:
        for item in json.loads((root / manifest).read_text(encoding="utf-8")):
            if (
                item["path"] not in names
                or hashlib.sha256((root / item["path"]).read_bytes()).hexdigest()
                != item["sha256"]
            ):
                raise ValueError("Asset provenance changed: " + item["path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(
            output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name in sorted(names):
                info = zipfile.ZipInfo(
                    "photo-book-workshop-1.0.0/" + name,
                    date_time=(2026, 9, 20, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (
                    0o100755 if name in {"Start.command", "Start.sh"} else 0o100644
                ) << 16
                archive.writestr(info, (root / name).read_bytes())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {
        "archive": output.name,
        "files": len(names),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(package(Path(__file__).resolve().parents[1], a.output), indent=2))
