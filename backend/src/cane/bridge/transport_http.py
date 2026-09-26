"""The real uplink: one multipart POST per frame set, over httpx.

Kept behind :class:`cane.bridge.sender.InferenceTransport` so the sender --
and every test of the one-in-flight rule -- never touches the network.
"""

from __future__ import annotations

import logging

import httpx

from cane.cloud.api_models import InferenceRequest

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 2.0
RESPONSE_PREVIEW_CHARS = 200


class HttpInferenceTransport:
    """POSTs ``meta`` + six JPEG crops as ``multipart/form-data``."""

    def __init__(self, url: str, request_timeout_s: float) -> None:
        self._url = url
        timeout = httpx.Timeout(request_timeout_s, connect=CONNECT_TIMEOUT_S)
        # One connection, kept alive: a new TCP+TLS handshake per frame would
        # show up directly in the RTT p95 this step is measured on.
        limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
        self._client = httpx.AsyncClient(timeout=timeout, limits=limits)

    @property
    def url(self) -> str:
        return self._url

    async def post_crops(self, request: InferenceRequest) -> bytes:
        """Raises ``httpx.HTTPError`` / ``CloudResponseError``; the sender
        catches, counts and logs -- the loop keeps running.
        """
        response = await self._client.post(
            self._url, data=request.multipart_data(), files=request.multipart_files()
        )
        if response.status_code != httpx.codes.OK:
            raise CloudResponseError(
                f"{self._url} answered HTTP {response.status_code}: "
                f"{response.text[:RESPONSE_PREVIEW_CHARS]!r}",
                status_code=response.status_code,
            )
        return response.content

    async def aclose(self) -> None:
        await self._client.aclose()


class CloudResponseError(RuntimeError):
    """The inference service answered, but not with a 200.

    `status_code` is carried separately from the message so step 8's
    `CloudBackend` can turn it into a `BackendError(status)` without
    parsing English.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
