import json
from pathlib import Path
import subprocess
import sys
from photobook_workshop.cli import main


def test_complete_cli_demo_and_choices_rebuild(tmp_path, capsys):
    root = tmp_path / "invented example"
    assert main(["demo", "--directory", str(root)]) == 0
    capsys.readouterr()
    assert (root / "first-proof/book-proof.pdf").is_file()
    from photobook_workshop.book import load_book, options_hash

    book = load_book(root / "book/book.json")
    choice = {
        "bookId": book["bookId"],
        "optionsHash": options_hash(book),
        "choices": {
            "sky": {"optionId": "option-2", "note": "This is a note, not caption text."}
        },
    }
    choices = tmp_path / "choices.json"
    choices.write_text(json.dumps(choice), encoding="utf-8")
    out = tmp_path / "proof"
    assert (
        main(
            [
                "build",
                "--book",
                str(root / "book/book.json"),
                "--choices",
                str(choices),
                "--output",
                str(out),
            ]
        )
        == 0
    )
    html = (out / "html/pages/sky.html").read_text(encoding="utf-8")
    assert (
        "Some mornings deserve a longer look." in html and "This is a note" not in html
    )
    original = (out / "book-proof.pdf").read_bytes()
    assert (
        main(["build", "--book", str(root / "book/book.json"), "--output", str(out)])
        == 2
    )
    assert (out / "book-proof.pdf").read_bytes() == original


def test_help_and_import_do_not_open_media_spawn_or_connect(tmp_path):
    code = """
import socket,subprocess,webbrowser
from PIL import Image
def forbidden(*a,**k):raise AssertionError("unexpected effect")
socket.socket.connect=forbidden;subprocess.run=forbidden;subprocess.Popen=forbidden;webbrowser.open=forbidden;Image.open=forbidden
import photobook_workshop.cli
photobook_workshop.cli.main(["--help"])
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
