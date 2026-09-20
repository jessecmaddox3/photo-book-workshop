"""Optional OpenAI adapter. Constructed only for explicit API commands."""

from __future__ import annotations


class OpenAIBatchProvider:
    def __init__(self, client=None):
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as error:
                raise ValueError(
                    "Install the api extra before using API commands"
                ) from error
            # Retrying an accepted mutation after a lost reply can create duplicate work.
            client = OpenAI(max_retries=0, timeout=120)
        self.client = client.with_options(max_retries=0)

    def upload(self, path, operation_id):
        with open(path, "rb") as stream:
            return self.client.files.create(
                file=(
                    "photobook-" + operation_id + ".jsonl",
                    stream,
                    "application/jsonl",
                ),
                purpose="batch",
            ).id

    def create(self, file_id, metadata):
        return self.client.batches.create(
            input_file_id=file_id,
            endpoint="/v1/responses",
            completion_window="24h",
            metadata=metadata,
        ).model_dump()

    def get_batch(self, batch_id):
        return self.client.batches.retrieve(batch_id).model_dump()

    def download(self, file_id, max_bytes):
        chunks = []
        size = 0
        with self.client.files.with_streaming_response.content(file_id) as response:
            for chunk in response.iter_bytes(chunk_size=65536):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("Remote file exceeds the collection limit")
                chunks.append(chunk)
        return b"".join(chunks)

    def find_operation(self, operation_id):
        """Bounded discovery is a lead, never proof that a lost request failed."""
        batches = []
        files = []
        complete = {}
        for kind in ["batches", "files"]:
            kwargs = {"limit": 100}
            if kind == "files":
                kwargs.update(purpose="batch", order="desc")
            endpoint = getattr(self.client, kind)
            page = endpoint.list(**kwargs)
            for index in range(10):
                for item in page.data:
                    if (
                        kind == "batches"
                        and (item.metadata or {}).get("photobook_operation")
                        == operation_id
                    ):
                        batches.append(item.id)
                    if (
                        kind == "files"
                        and item.filename == "photobook-" + operation_id + ".jsonl"
                    ):
                        files.append(item.id)
                complete[kind] = not page.has_next_page()
                if complete[kind] or index == 9:
                    break
                page = page.get_next_page()
        return {
            "batches": batches,
            "files": files,
            "completeSearch": all(complete.values()),
            "note": "Listing results can be delayed or incomplete. No match does not authorize another submission.",
        }
