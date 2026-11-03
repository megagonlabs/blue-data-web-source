from __future__ import annotations

import ipaddress
import socket
from typing import List, Optional, Sequence, Set
from urllib.parse import urlsplit

# Cloud instance-metadata endpoints. Both are already covered by the link-local /
# unique-local checks below, but naming them explicitly makes the block reason
# clear and documents intent (AWS/GCP/Azure IMDS, the classic SSRF target).
_METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)


class FetchBlockedError(ValueError):
    """Raised when the destination of a fetch is refused by the policy.

    Subclasses ``ValueError`` so REST handlers map it to 400 and the per-URL
    ingest paths record it as an ``error`` outcome rather than crashing.
    """


def _blocked_reason(ip: ipaddress._BaseAddress) -> Optional[str]:
    """Return a human-readable category if *ip* is non-public, else ``None``."""
    if ip in _METADATA_IPS:
        return "cloud-metadata"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.is_private:
        return "private"
    if ip.is_reserved:
        return "reserved"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "unspecified"
    return None


class DestinationPolicy:
    """Decides whether the server may fetch a given URL's destination.

    The server fetches caller-supplied URLs (ingest, crawl, feed, search-web,
    extract), so without a policy a caller could make it reach loopback,
    private-network, link-local or cloud-metadata addresses — the classic SSRF
    vector (#112). By default every such address is refused; the check runs
    **after DNS resolution**, so a public hostname that resolves to an internal
    IP is still blocked.

    Opt in to internal fetches with either:

    - ``allow_private=True`` — disable all blocking (trusted/offline deployments), or
    - ``allowlist=[...]`` — admit specific hostnames and/or IP networks. Entries
      that parse as an IP/CIDR (e.g. ``"127.0.0.1"``, ``"10.0.0.0/8"``) match the
      resolved address; anything else is treated as an exact hostname.
    """

    def __init__(
        self,
        *,
        allow_private: bool = False,
        allowlist: Optional[Sequence[str]] = None,
    ) -> None:
        self.allow_private = allow_private
        self._allow_hosts: Set[str] = set()
        self._allow_networks: List[ipaddress._BaseNetwork] = []
        for entry in allowlist or []:
            entry = entry.strip()
            if not entry:
                continue
            try:
                self._allow_networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                self._allow_hosts.add(entry)

    def check_url(self, url: str) -> None:
        """Raise :class:`FetchBlockedError` if *url*'s destination is refused.

        Resolves the host and inspects **every** address it maps to; a single
        blocked address refuses the fetch. Unresolvable hosts are left for the
        HTTP client to fail on naturally (there is nothing to reach, so no SSRF).
        """
        if self.allow_private:
            return
        host = urlsplit(url).hostname
        if not host:
            raise FetchBlockedError(f"URL has no host: {url!r}")
        if host in self._allow_hosts:
            return
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            # Can't resolve → the client can't connect either; let it fail there.
            return
        for info in infos:
            raw = info[4][0].split("%", 1)[0]  # strip any IPv6 zone id
            ip = ipaddress.ip_address(raw)
            if any(ip in net for net in self._allow_networks):
                continue
            reason = _blocked_reason(ip)
            if reason:
                raise FetchBlockedError(
                    f"refusing to fetch {host!r}: resolves to {reason} address {ip}. "
                    f"Enable internal fetches with an allow-list "
                    f"(BLUE_WEB_FETCH_ALLOWLIST) or BLUE_WEB_FETCH_ALLOW_PRIVATE=1."
                )
