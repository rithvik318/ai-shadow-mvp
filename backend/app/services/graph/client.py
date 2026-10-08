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
from collections.abc import Callable, Iterator
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

# Graph throttles aggressively, and a first sync over a large corpus is exactly
# the shape of traffic that triggers it. 429 is not a failure — it is Graph
# telling us the rate, and it names the wait in `Retry-After`. 503 and 504 are
# the same conversation in a different register. Treating any of them as a
# failed file would leave the run permanently `partial`, holding the delta
# token back forever and never finishing the corpus.
_RETRYABLE_STATUS = frozenset({429, 503, 504})

# A ceiling on politeness. Beyond this the far end is not throttling, it is
# down, and the caller should hear about it rather than block for minutes.
_MAX_RETRY_ATTEMPTS = 5
_MAX_RETRY_WAIT_SECONDS = 60.0

# The ceiling that actually bounds a run. Per-attempt caps do not: five attempts
# each honouring a 60-second `Retry-After` is five minutes of sleeping inside
# one file, and a folder of several hundred throttled files turns that into a
# sync measured in days. Once this much has been spent waiting, the answer
# already is "not now" — returning it lets the file be recorded as transient and
# retried on the next run, which is cheaper than holding the run open for it.
_MAX_TOTAL_RETRY_SECONDS = 120.0

# Statuses a *download* will keep returning however many times it is asked.
# 401 and 403 are the exception and are handled before this is consulted: on a
# pre-authorised storage URL they almost always mean the URL has expired rather
# than that access is refused, which is a different remedy — mint a new one.
_PERMANENT_DOWNLOAD_STATUS = frozenset({400, 404, 405, 410, 414, 416})

# What an expired pre-authorised download URL answers. Graph mints these with a
# life of roughly an hour, so any run that enumerates first and downloads later
# will meet them; the fix is a fresh URL, not a retry of the stale one.
_EXPIRED_URL_STATUS = frozenset({401, 403})


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
            # Deliberately names no permission. This client serves files, mail
            # and calendar, which are three separate admin consents, and it
            # cannot see which one the caller was exercising. It used to say
            # `Files.Read.All` unconditionally — correct for a sync, and
            # actively misleading everywhere else: a calendar 403 told the
            # reader to re-grant a files permission that could not possibly
            # have been the cause. That is the exact failure this project
            # already recorded once, in the other direction, when the message
            # named `Sites.Read.All`.
            #
            # The status and code travel on the exception, so the layer that
            # does know which API it called — `calendar_service.status`,
            # `outlook_provider.status`, `source_resolver` — names the consent.
            raise GraphAuthError(
                f"Graph denied the request (HTTP {response.status_code}"
                f"{f', {code}' if code else ''}). This application's Graph "
                "permissions are granted per API, so the one to check is the "
                "one for the call that failed. The other possibility is "
                "content outside this tenant, which no consent can change.",
                status_code=response.status_code,
                graph_code=code or None,
            )

        raise GraphRequestError(
            f"Graph request failed (HTTP {response.status_code}"
            f"{f', {code}' if code else ''}): {_safe_path(url)}"
        )

    def _retry_after(self, response: httpx.Response | None, attempt: int) -> float:
        """How long to wait before retrying, in seconds.

        Graph's `Retry-After` is authoritative and is honoured as given; it is
        the far end telling us its own rate. Only when the header is absent or
        unreadable does this fall back to exponential backoff, and the result
        is capped either way so one hostile header cannot stall a sync run.

        `response` is None when the request never produced one — a transport
        error — which is simply the case where there is no header to read.
        """

        header = "" if response is None else response.headers.get("Retry-After", "")

        try:
            seconds = float(header)
        except ValueError:
            # The header may also be an HTTP-date. Parsing that precisely is
            # not worth it — back off instead and try again.
            seconds = 2.0**attempt

        if seconds <= 0:
            seconds = 2.0**attempt

        return min(seconds, _MAX_RETRY_WAIT_SECONDS)

    def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """Perform one Graph request, waiting out throttling rather than failing.

        Retries only the statuses Graph uses to mean "not now": a 404 or a 400
        is an answer, and repeating it would just be slower.

        `json` is safe to retry for the write this client performs — Graph's
        `sendMail` is the only one, and a throttled request never reached the
        mailbox. A write that were not idempotent would need an idempotency key
        rather than a retry, and would not belong on this path.
        """

        last: httpx.Response | None = None
        spent = 0.0

        for attempt in range(_MAX_RETRY_ATTEMPTS):
            try:
                with self._http() as http:
                    response = http.request(
                        method,
                        url,
                        params=params,
                        headers=headers,
                        json=json,
                        follow_redirects=follow_redirects,
                    )
            except httpx.HTTPError as exc:
                # A dropped read or a connection reset is the most literal
                # "not now" there is — no status came back at all — and until
                # now it was the one thing this loop did not retry. One
                # `ReadError` on one file therefore marked an 800-file run
                # `partial` and withheld its delta token, which is a whole
                # re-enumeration paid for a hiccup.
                #
                # Retried under the same budget as throttling, with the same
                # backoff. If the budget or the attempts run out the failure is
                # raised `transient=True`: the bytes were never seen, so the
                # token must not move past this file.
                wait = self._retry_after(None, attempt)

                if attempt + 1 >= _MAX_RETRY_ATTEMPTS or spent + wait > (
                    _MAX_TOTAL_RETRY_SECONDS
                ):
                    raise GraphRequestError(
                        f"Could not reach Graph ({type(exc).__name__}) after "
                        f"{attempt + 1} attempt(s): {_safe_path(url)}",
                        transient=True,
                    ) from exc

                logger.warning(
                    "graph_transport_error_retried",
                    extra={
                        "error": type(exc).__name__,
                        "attempt": attempt + 1,
                        "wait_seconds": wait,
                        "path": _safe_path(url),
                    },
                )

                time.sleep(wait)
                spent += wait
                continue

            if response.status_code not in _RETRYABLE_STATUS:
                return response

            last = response
            wait = self._retry_after(response, attempt)

            # The URL is logged without its query string: Graph puts delta and
            # download tokens there.
            logger.warning(
                "graph_throttled",
                extra={
                    "status": response.status_code,
                    "attempt": attempt + 1,
                    "wait_seconds": wait,
                    "path": _safe_path(url),
                },
            )

            # Stop when the *budget* is gone, not only when the attempts are.
            # Without this a file can sit here for five minutes, and several
            # hundred of them turn one sync into a multi-day run.
            if spent + wait > _MAX_TOTAL_RETRY_SECONDS:
                logger.warning(
                    "graph_retry_budget_exhausted",
                    extra={"spent_seconds": round(spent, 1), "path": _safe_path(url)},
                )
                return response

            if attempt + 1 < _MAX_RETRY_ATTEMPTS:
                time.sleep(wait)
                spent += wait

        assert last is not None  # noqa: S101 - the loop runs at least once

        return last

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict:
        """GET one Graph resource and return its JSON body."""

        url = self._absolute(path_or_url)
        response = self._send(
            "GET",
            url,
            params=params,
            headers={"Authorization": f"Bearer {self.access_token()}"},
        )

        self._raise_for_status(response, url)

        return response.json()

    def post(self, path_or_url: str, payload: dict[str, Any]) -> dict | None:
        """POST a JSON body to Graph and return its JSON body, if it has one.

        Returns None for `204 No Content` and `202 Accepted`, which is what
        Graph answers to `sendMail` — the message was accepted and no resource
        was returned. A caller must not read that as failure, and must not
        invent an id to fill the gap.
        """

        url = self._absolute(path_or_url)
        response = self._send(
            "POST",
            url,
            headers={"Authorization": f"Bearer {self.access_token()}"},
            json=payload,
        )

        self._raise_for_status(response, url)

        if response.status_code in (202, 204) or not response.content:
            return None

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

    def redirect_location(self, path_or_url: str) -> str | None:
        """Ask Graph for a resource and return where it points, without going.

        `/content` answers `302` with a freshly minted, pre-authorised URL in
        `Location`. Following that redirect here would carry the bearer token to
        a storage host that has no business holding one — which is why the
        redirect is deliberately *not* followed. The caller fetches the location
        separately, unauthenticated, through `download()`.

        Returns None when Graph answered something other than a redirect, so the
        caller can fall back rather than treat it as a failure.
        """

        url = self._absolute(path_or_url)
        response = self._send(
            "GET",
            url,
            headers={"Authorization": f"Bearer {self.access_token()}"},
            follow_redirects=False,
        )

        if response.status_code in (301, 302, 303, 307, 308):
            return response.headers.get("Location")

        # Not a redirect. Let the normal translation decide whether that is an
        # error worth raising — a 404 here is a real answer about the item.
        self._raise_for_status(response, url)

        return None

    def resolve_redirect(self, url: str) -> str:
        """Follow a shortened Microsoft link to the URL it stands for.

        Not a Graph call and deliberately unauthenticated — a short link is a
        public redirect, and attaching a bearer token would hand a tenant
        credential to a host that has no business holding one.

        This exists because `/shares/{token}` is told a URL, and a short link
        is not the URL of anything: it is a redirect to one. Resolving it first
        is also the only way to learn which tenant, if any, the content is in.
        Returns the original URL unchanged if the far end does not redirect.

        The status of the final response is deliberately ignored. What is
        wanted is the address, not the page: a link to content this caller has
        no rights to still answers `403` *from the host that holds it*, and
        that host is the fact worth having. Raising on the status would throw
        away the answer at exactly the moment it is most useful. Only a genuine
        transport failure raises, from `_send`.
        """

        return str(self._send("GET", url, follow_redirects=True).url)

    def download(self, url: str, *, refresh: Callable[[], str] | None = None) -> bytes:
        """Fetch a file's bytes, re-minting the URL once if it has expired.

        Graph's download URLs are pre-authorised and short-lived — roughly an
        hour — so no bearer token is attached: sending one to the storage host
        would leak it somewhere it is not needed.

        That short life is the whole reason `refresh` exists. Any caller that
        enumerates a folder first and downloads afterwards is holding URLs that
        were minted before the run began, and every one of them goes stale while
        the run is still working through the list. An expired URL answers 401 or
        403, which is indistinguishable from "forbidden" by status alone and was
        previously recorded as a failure — so a long sync failed every file
        after its first hour, and the failures looked like a permissions
        problem. `refresh` is called **once**, and only for those two statuses,
        to ask Graph for a current URL. Once, because a refreshed URL that is
        also refused is telling us something real, and a loop there would be an
        unbounded retry against the one status that will not change.
        """

        # No Authorization header: these URLs are pre-authorised and point at
        # storage hosts, so a bearer token would be handed somewhere it is not
        # needed. Throttling is honoured here too — a large corpus is throttled
        # on its downloads as readily as on its listings.
        response = self._send("GET", url, follow_redirects=True)

        if response.status_code in _EXPIRED_URL_STATUS and refresh is not None:
            logger.info(
                "graph_download_url_refreshed", extra={"status": response.status_code}
            )
            response = self._send("GET", refresh(), follow_redirects=True)

        if response.status_code < 400:
            return response.content

        status = response.status_code

        if status in _EXPIRED_URL_STATUS:
            # Either no refresher was offered, or a freshly minted URL was
            # refused too. Permanent: the remedy is a consent change, not
            # another attempt, and retrying would hold the delta token back
            # forever over a file nothing can read.
            raise GraphRequestError(
                f"Download refused (HTTP {status}) — the pre-authorised URL was "
                "rejected even after being re-minted, so the application does "
                "not have access to this item.",
                transient=False,
            )

        if status in _PERMANENT_DOWNLOAD_STATUS:
            raise GraphRequestError(
                f"Download failed permanently (HTTP {status}).", transient=False
            )

        # 429 and 5xx arrive here only once `_send` has exhausted its retries or
        # its budget. Still transient: the content was never seen, so the delta
        # token must not move past it.
        raise GraphRequestError(
            f"Download failed (HTTP {status}); it may succeed on a later run.",
            transient=True,
        )


def _safe_path(url: str) -> str:
    """Strip the query from a URL before it reaches a log or an error body.

    Graph puts delta and download tokens in the query string. They are
    credentials for the duration of their life, and an exception that
    propagates to a response body is exactly where they must not appear.
    """

    return url.split("?", 1)[0]
