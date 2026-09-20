import httpx
import pytest

openai = pytest.importorskip("openai")
from photobook_workshop.api_provider import OpenAIBatchProvider


def test_real_sdk_shapes_and_bounded_stream_without_network(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path == "/v1/files" and request.method == "POST":
            assert b"photobook-invented-operation.jsonl" in request.content
            return httpx.Response(
                200,
                json={
                    "id": "file-1",
                    "object": "file",
                    "bytes": 3,
                    "created_at": 0,
                    "filename": "photobook-invented-operation.jsonl",
                    "purpose": "batch",
                    "status": "processed",
                },
            )
        if request.url.path == "/v1/batches":
            import json

            body = json.loads(request.content)
            assert (
                body["endpoint"] == "/v1/responses"
                and body["completion_window"] == "24h"
            )
            return httpx.Response(
                200,
                json={
                    "id": "batch-1",
                    "object": "batch",
                    "status": "validating",
                    **body,
                    "created_at": 0,
                    "errors": None,
                },
            )
        return httpx.Response(200, content=b"0123456789")

    client = openai.OpenAI(
        api_key="invented-test-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    provider = OpenAIBatchProvider(client)
    path = tmp_path / "input.jsonl"
    path.write_bytes(b"{}\n")
    assert provider.upload(path, "invented-operation") == "file-1"
    assert (
        provider.create("file-1", {"photobook_operation": "invented-operation"})["id"]
        == "batch-1"
    )
    assert provider.download("file-2", 10) == b"0123456789"
    with pytest.raises(ValueError):
        provider.download("file-2", 9)
    assert len(calls) == 4
    client.close()


def test_sdk_mutation_is_never_automatically_retried(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            500, json={"error": {"message": "invented error", "type": "server_error"}}
        )

    client = openai.OpenAI(
        api_key="invented-test-key",
        max_retries=2,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    with pytest.raises(openai.APIStatusError):
        OpenAIBatchProvider(client).create("file-1", {})
    assert len(calls) == 1
    client.close()
