import http.client
import json
from pathlib import Path
from threading import Thread
import pytest
from photobook_workshop.server import make_server
from photobook_workshop.book import load_book


@pytest.fixture
def server(tmp_path):
    book_path = Path(__file__).parents[1] / "src/photobook_workshop/demo/book.json"
    srv = make_server(book_path, tmp_path / "review", port=0)
    thread = Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(3)


def request(server, path="/", method="GET", body=None, host=None, origin=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{server.server_port}"}
    if origin is not None:
        headers["Origin"] = origin
    if body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(body)
    conn.request(method, path, body, headers)
    res = conn.getresponse()
    data = res.read()
    status = res.status
    conn.close()
    return status, data


def test_every_route_checks_host_and_paths_are_confined(server):
    for path in ["/", "/api/book", "/api/state", "/asset/lighthouse", "/ui/app.js"]:
        assert (
            request(server, path, host=f"evil.invalid:{server.server_port}")[0] == 403
        )
    for path in [
        "/../book.json",
        "/asset/../book.json",
        "/ui/../../book.json",
        "/asset/%2e%2e%2fbook.json",
    ]:
        assert request(server, path)[0] == 404
    status, data = request(server, "/api/book")
    b = json.loads(data)
    assert status == 200 and b["bookId"] == "small-days-invented"
    assert "source_sha256" not in data.decode() and "normalized/" not in data.decode()
    assert request(server, "/asset/lighthouse")[0] == 200


def test_mutations_need_exact_origin_and_saved_choice_reloads(server):
    _, data = request(server, "/api/book")
    b = json.loads(data)
    from uuid import uuid4

    p = b["pages"][0]
    cmd = {
        "bookId": b["bookId"],
        "optionsHash": b["optionsHash"],
        "pageId": p["id"],
        "operationId": str(uuid4()),
        "baseRevision": 0,
        "optionId": p["options"][0]["id"],
        "note": "Café",
    }
    assert request(server, "/api/choice", "POST", cmd)[0] == 403
    assert (
        request(server, "/api/choice", "POST", cmd, origin="https://evil.invalid")[0]
        == 403
    )
    origin = f"http://127.0.0.1:{server.server_port}"
    assert request(server, "/api/choice", "POST", cmd, origin=origin)[0] == 200
    _, data = request(server, "/api/state")
    assert json.loads(data)["choices"][p["id"]]["note"] == "Café"
    cmd["operationId"] = str(uuid4())
    assert request(server, "/api/choice", "POST", cmd, origin=origin)[0] == 409


def test_unrelated_workspace_file_is_not_served_or_overwritten(tmp_path):
    book_path = Path(__file__).parents[1] / "src/photobook_workshop/demo/book.json"
    with pytest.raises(ValueError):
        make_server(book_path, book_path.parent, port=0)


def test_symlinked_proof_root_cannot_write_outside_bound_workspace(server, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (server.workspace / "proofs").symlink_to(outside, target_is_directory=True)
    _, data = request(server, "/api/book")
    b = json.loads(data)
    origin = f"http://127.0.0.1:{server.server_port}"
    status, _ = request(
        server,
        "/api/build",
        "POST",
        {"bookId": b["bookId"], "optionsHash": b["optionsHash"]},
        origin=origin,
    )
    assert status == 422 and not list(outside.iterdir())


def test_review_workspace_cannot_be_nested_inside_source_book(tmp_path):
    import shutil

    source = tmp_path / "book"
    shutil.copytree(Path(__file__).parents[1] / "src/photobook_workshop/demo", source)
    with pytest.raises(ValueError):
        make_server(source / "book.json", source / "review", port=0)


def test_saved_choice_symlink_is_not_followed(server, tmp_path):
    target = tmp_path / "unrelated.json"
    target.write_text('{"private":"untouched"}')
    (server.workspace / "choices.json").symlink_to(target)
    assert request(server, "/api/state")[0] == 422
    assert target.read_text() == '{"private":"untouched"}'


def test_proof_read_rejects_replaced_directory_symlink(server, tmp_path):
    _, data = request(server, "/api/book")
    b = json.loads(data)
    origin = f"http://127.0.0.1:{server.server_port}"
    status, data = request(
        server,
        "/api/build",
        "POST",
        {"bookId": b["bookId"], "optionsHash": b["optionsHash"]},
        origin=origin,
    )
    assert status == 200
    report = json.loads(data)
    folder = server.workspace / "proofs" / report["id"]
    folder.rename(folder.with_name(folder.name + "-preserved"))
    outside = tmp_path / "outside-proof"
    outside.mkdir()
    (outside / "captions.pdf").write_bytes(b"invented outside bytes")
    folder.symlink_to(outside, target_is_directory=True)
    status, data = request(server, report["links"]["captions"])
    assert status == 422 and b"invented outside bytes" not in data


def test_restart_reuses_browser_origin_and_never_silently_changes_busy_port(tmp_path):
    book = Path(__file__).parents[1] / "src/photobook_workshop/demo/book.json"
    workspace = tmp_path / "review"
    first = make_server(book, workspace)
    port = first.server_port
    try:
        with pytest.raises(OSError, match="No new address"):
            make_server(book, workspace)
    finally:
        first.server_close()
    second = make_server(book, workspace)
    try:
        assert second.server_port == port
    finally:
        second.server_close()


def test_saved_port_cannot_redirect_to_symbolic_file(tmp_path):
    book = Path(__file__).parents[1] / "src/photobook_workshop/demo/book.json"
    workspace = tmp_path / "review"
    first = make_server(book, workspace)
    first.server_close()
    (workspace / ".port.json").unlink()
    other = tmp_path / "outside-port.json"
    other.write_text('{"port":1234}')
    (workspace / ".port.json").symlink_to(other)
    with pytest.raises(ValueError):
        make_server(book, workspace)
    assert other.read_text() == '{"port":1234}'
