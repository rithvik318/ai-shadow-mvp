"""Talking to Microsoft Graph, and nothing else.

This module knows about OAuth, HTTP, paging and Graph's error shapes. It does
not know what a document is, and nothing above it knows what a bearer token
is — the same split `app/services/llm/client.py` draws around the LLM
provider, for the same reason: the thing most likely to need replacing should
be the thing with the fewest callers.

Authentication is the OAuth 2.0 **client-credentials** flow. The application
acts as itself rather than on behalf of a signed-in person, which is what a
background sync needs: nobody is present at 3am to complete a consent prompt,
and a refresh token that expires while everyone is asleep is an outage. The
cost is that access is granted tenant-wide by an administrator rather than
per-user, which is recorded in docs/KNOWN_ISSUES.md.

`msal` would also do this. It is not used: the client-credentials flow is one
form POST and an expiry check, and the project already carries `httpx`.
Adding a dependency to avoid thirty lines is the trade this repository has
already regretted once, with `langchain-text-splitters`.
"""

import logging
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx

from app.config.settings import settings
from app.core.exceptions import (
    DeltaTokenExpiredError,
    GraphAuthError,
    GraphRequestError,
    SyncNotConfiguredError,
)

logger = logging.getLogger(__name__)

# Refresh a little before Graph would stop honouring the token, so a request
# that is slow to leave does not arrive with one that has just expired.
_TOKEN_EXPIRY_MARGIN_SECONDS = 120

# Graph signals "your delta token is too old, start again" with 410 Gone and
# this code. It is a normal part of the protocol, not a fault.
_RESYNC_CODES = frozenset({"resyncRequired", "resyncApplyDifferences"})


class GraphClient:
    """A thin, synchronous Graph client with a cached application token.

    Synchronous on purpose. Ingestion is synchronous, the sync service that
    drives this is synchronous, and an async client here would mean colouring
    that whole path async to gain concurrency the MVP has no use for.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        base_url: str = "https://graph.microsoft.com/v1.0",
        authority: str = "https://login.microsoftonline.com",
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._authority = authority.rstrip("/")
        self._timeout = timeout
        # Injected by tests. A real deployment never passes this, and the
        # client is otherwise identical, so the code under test is the code
        # that ships.
        self._transport = transport

        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = threading.Lock()

    # --- construction ----------------------------------------------------

    @classmethod
    def from_settings(
        cls, *, transport: httpx.BaseTransport | None = None
    ) -> "GraphClient":
        """Build a client from configuration, or explain what is missing.

        Raises rather than returning None: every caller would have to handle
        the None, and "not configured" is a state worth naming in one place.
        """

        missing = [
            name
            for name, value in (
                ("ONEDRIVE_TENANT_ID", settings.ONEDRIVE_TENANT_ID),
                ("ONEDRIVE_CLIENT_ID", settings.ONEDRIVE_CLIENT_ID),
                ("ONEDRIVE_CLIENT_SECRET", settings.ONEDRIVE_CLIENT_SECRET),
            )
            if not value
        ]

        if missing:
            raise SyncNotConfiguredError(
                "OneDrive synchronisation is not configured. Missing: "
                + ", ".join(missing)
                + "."
            )

        return cls(
            tenant_id=str(settings.ONEDRIVE_TENANT_ID),
            client_id=str(settings.ONEDRIVE_CLIENT_ID),
            client_secret=str(settings.ONEDRIVE_CLIENT_SECRET),
            base_url=settings.ONEDRIVE_GRAPH_BASE_URL,
            authority=settings.ONEDRIVE_AUTHORITY,
            timeout=settings.ONEDRIVE_REQUEST_TIMEOUT_SECONDS,
            transport=transport,
        )

    # --- authentication --------------------------------------------------

    def _http(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout, transport=self._transport)

    def access_token(self) -> str:
        """Return a valid application token, minting one only when needed.

        Locked because the scheduler and a manual request can both reach this
        at once, and two threads racing to mint would spend two token calls to
        arrive at the same answer.
        """

        with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token

            url = f"{self._authority}/{self._tenant_id}/oauth2/v2.0/token"
            form = {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            }

            try:
                with self._http() as http:
                    response = http.post(url, data=form)
            except httpx.HTTPError as exc:
                raise GraphAuthError(
                    f"Could not reach the identity provider: {type(exc).__name__}."
                ) from exc

            if response.status_code != 200:
                # Deliberately not echoing the body: it is an auth response,
                # and the useful part is the status.
                raise GraphAuthError(
                    "Microsoft Entra ID rejected the application credentials "
                    f"(HTTP {response.status_code}). Check ONEDRIVE_TENANT_ID, "
                    "ONEDRIVE_CLIENT_ID and ONEDRIVE_CLIENT_SECRET."
                )

            payload = response.json()
            token = payload.get("access_token")

            if not token:
                raise GraphAuthError("Token response contained no access_token.")

            expires_in = int(payload.get("expires_in", 3600))
            self._token = str(token)
            self._token_expires_at = time.monotonic() + max(
                expires_in - _TOKEN_EXPIRY_MARGIN_SECONDS, 0
            )

            logger.info("graph_token_acquired", extra={"expires_in": expires_in})

            return self._token

    def invalidate_token(self) -> None:
        """Forget the cached token, so the next call mints a fresh one."""

        with self._token_lock:
            self._token = None
            self._token_expires_at = 0.0

    # --- requests --------------------------------------------------------

    def _absolute(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url

        return f"{self._base_url}/{path_or_url.lstrip('/')}"

    def _raise_for_status(self, response: httpx.Response, url: str) -> None:
        if response.status_code < 400:
            return

        code = ""
        try:
            code = str(response.json().get("error", {}).get("code", ""))
        except ValueError:
            code = ""

        if response.status_code == 410 or code in _RESYNC_CODES:
            raise DeltaTokenExpiredError(
                "Graph rejected the stored delta token and asked for a full "
                "resynchronisation."
            )

        if response.status_code in (401, 403):
            raise GraphAuthError(
                f"Graph denied the request (HTTP {response.status_code}"
                f"{f', {code}' if code else ''}). The application may lack "
                "Files.Read.All or Sites.Read.All consent."
            )

        raise GraphRequestError(
            f"Graph request failed (HTTP {response.status_code}"
            f"{f', {code}' if code else ''}): {_safe_path(url)}"
        )

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict:
        """GET one Graph resource and return its JSON body."""

        url = self._absolute(path_or_url)
        headers = {"Authorization": f"Bearer {self.access_token()}"}

        try:
            with self._http() as http:
                response = http.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise GraphRequestError(
                f"Could not reach Graph ({type(exc).__name__}): {_safe_path(url)}"
            ) from exc

        self._raise_for_status(response, url)

        return response.json()

    def paged(
        self, path_or_url: str, params: dict[str, Any] | None = None
    ) -> Iterator[dict]:
        """Yield every item across every page of a Graph collection.

        Graph paginates with `@odata.nextLink`, an absolute URL that already
        carries the query. Following it rather than incrementing an offset is
        what makes paging correct while the collection is changing underneath.
        """

        payload = self.get(path_or_url, params)

        while True:
            yield from payload.get("value", [])

            next_link = payload.get("@odata.nextLink")
            if not next_link:
                return

            payload = self.get(next_link)

    def delta(self, path_or_url: str) -> tuple[list[dict], str | None]:
        """Walk a delta collection to its end, returning items and the new link.

        The delta link only appears on the final page. Returning it separately
        from the items is what lets the caller decide whether the work those
        items describe actually succeeded before committing to it.
        """

        items: list[dict] = []
        payload = self.get(path_or_url)

        while True:
            items.extend(payload.get("value", []))

            next_link = payload.get("@odata.nextLink")
            if next_link:
                payload = self.get(next_link)
                continue

            return items, payload.get("@odata.deltaLink")

    def download(self, url: str) -> bytes:
        """Fetch a file's bytes.

        Graph's download URLs are pre-authorised and short-lived, so no bearer
        token is attached — sending one to the storage host would leak it
        somewhere it is not needed.
        """

        try:
            with self._http() as http:
                response = http.get(url, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise GraphRequestError(f"Download failed ({type(exc).__name__}).") from exc

        if response.status_code >= 400:
            raise GraphRequestError(f"Download failed (HTTP {response.status_code}).")

        return response.content


def _safe_path(url: str) -> str:
    """Strip the query from a URL before it reaches a log or an error body.

    Graph puts delta and download tokens in the query string. They are
    credentials for the duration of their life, and an exception that
    propagates to a response body is exactly where they must not appear.
    """

    return url.split("?", 1)[0]
