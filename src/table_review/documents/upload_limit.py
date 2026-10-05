from starlette.formparsers import MultiPartException


class UploadSizeLimit:
    def __init__(self, app, max_pdf_bytes: int):
        self.app, self.limit = app, max_pdf_bytes + 64 * 1024

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != "/api/v1/documents"
        ):
            return await self.app(scope, receive, send)
        count = 0

        async def bounded_receive():
            nonlocal count
            message = await receive()
            if message["type"] == "http.request":
                count += len(message.get("body", b""))
                if count > self.limit:
                    scope.setdefault("state", {})["upload_size_exceeded"] = True
                    raise MultiPartException("Upload exceeds the configured size limit")
            return message

        await self.app(scope, bounded_receive, send)
