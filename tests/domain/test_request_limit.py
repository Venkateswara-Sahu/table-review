import asyncio

from starlette.exceptions import HTTPException

from table_review.documents.upload_limit import UploadSizeLimit


def test_chunked_request_stops_receiving_before_unbounded_body_is_read():
    received = []
    saved = []
    scope = {"type": "http", "method": "POST", "path": "/api/v1/tables/opaque/edits"}

    async def receive():
        received.append(True)
        return {"type": "http.request", "body": b"x" * 300, "more_body": True}

    async def endpoint(scope, receive, send):
        try:
            while (await receive())["more_body"]:
                pass
        except HTTPException as error:
            assert error.status_code == 413
            return
        saved.append(True)

    asyncio.run(UploadSizeLimit(endpoint, 50 * 1024**2, 512)(scope, receive, None))
    assert len(received) == 2
    assert not saved
    assert scope["state"]["request_size_exceeded"] is True
