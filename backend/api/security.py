"""Pure-ASGI guard: bearer token, Host and Origin checks. Fail-closed.
(Pure ASGI rather than BaseHTTPMiddleware so SSE streaming is never buffered.)"""
from __future__ import annotations

import hmac
import json
from urllib.parse import urlsplit

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class SecurityMiddleware:
    def __init__(self, app, token: str, allowed_origins: list[str]):
        if not token or len(token) < 16:
            raise ValueError("API token must be at least 16 characters")
        self.app, self.token, self.origins = app, token, set(allowed_origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}

        host = headers.get("host", "")
        hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        if hostname not in _LOCAL_HOSTS:
            return await self._deny(send, 403, "Forbidden host")

        origin = headers.get("origin")
        if origin is not None and origin not in self.origins:
            return await self._deny(send, 403, "Forbidden origin")

        auth = headers.get("authorization", "")
        scheme, _, supplied = auth.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.encode(), self.token.encode()
        ):
            return await self._deny(send, 401, "Missing or invalid bearer token",
                                    {"www-authenticate": "Bearer"})
        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(send, status: int, detail: str, extra: dict | None = None) -> None:
        body = json.dumps({"detail": detail}).encode()
        hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        hdrs += [(k.encode(), v.encode()) for k, v in (extra or {}).items()]
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"
