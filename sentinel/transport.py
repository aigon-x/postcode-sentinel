"""URL rules shared by the GitLab adapters.

A token travels in a request header, so plaintext ``http`` hands it to anyone on
the path. The adapters therefore refuse transport that would expose it. Loopback
``http`` stays allowed: that is how a local GitLab runs in development.
"""

from __future__ import annotations

import urllib.parse

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _is_loopback(host: str | None) -> bool:
    host = (host or "").lower()
    return host in LOOPBACK_HOSTS or host.startswith("127.")


def check_url(url: str, error: type[Exception], what: str) -> str:
    """Return ``url`` unchanged, or raise ``error`` when it would leak a token."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise error(
            f"{what} must be an http(s) URL, got scheme {parts.scheme or '<none>'!r}: {url!r}"
        )
    if parts.username or parts.password:
        raise error(
            f"{what} must not embed credentials (user:password@host): they end up in logs "
            "and in the action trace. Pass a token instead."
        )
    if parts.scheme == "http" and not _is_loopback(parts.hostname):
        raise error(
            f"{what} points at plaintext http on {parts.hostname!r}; the token would travel "
            "in clear text. Use https, or point the URL at localhost for a local GitLab."
        )
    return url


def redact(text: str, secret: str) -> str:
    """Strip ``secret`` from an error message; a token must never reach a log."""
    if not secret:
        return text
    return text.replace(secret, "[redacted]")
