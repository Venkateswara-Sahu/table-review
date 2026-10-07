from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException


class UploadSizeLimit:
    def __init__(self, app, max_pdf_bytes: int, max_json_bytes: int):
        self.app = app
        self.upload_limit = max_pdf_bytes + 64 * 1024
        self.json_limit = max_json_bytes

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] not in ("POST", "PUT", "PATCH")
            or not scope["path"].startswith("/api/v1/")
        ):
            return await self.app(scope, receive, send)
        upload = scope["method"] == "POST" and scope["path"] == "/api/v1/documents"
        limit = self.upload_limit if upload else self.json_limit
        count = 0

        async def bounded_receive():
            nonlocal count
            message = await receive()
            if message["type"] == "http.request":
                count += len(message.get("body", b""))
                if count > limit:
                    if upload:
                        scope.setdefault("state", {})["upload_size_exceeded"] = True
                        raise MultiPartException("Upload exceeds the configured size limit")
                    scope.setdefault("state", {})["request_size_exceeded"] = True
                    raise HTTPException(413)
            return message

        await self.app(scope, bounded_receive, send)
