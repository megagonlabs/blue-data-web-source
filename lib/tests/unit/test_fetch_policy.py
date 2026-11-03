"""Destination-policy / SSRF-guard tests for the fetcher (#112).

A local server on 127.0.0.1 shows that the default policy refuses it and that
the allow-list (and allow_private) admit it — exactly the acceptance criteria in
the issue. No external network is used.
"""
import http.server
import threading

import pytest

from blue_data_web_source.source.web.fetch_policy import DestinationPolicy, FetchBlockedError
from blue_data_web_source.source.web.fetcher import WebFetcher


@pytest.fixture
def loopback_server():
    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><article><p>internal page</p></article></body></html>")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/"
    finally:
        srv.shutdown()


# ── DestinationPolicy.check_url ──────────────────────────────────────────────

class TestDestinationPolicy:
    @pytest.mark.parametrize("url,reason", [
        ("http://127.0.0.1/", "loopback"),
        ("http://[::1]/", "loopback"),
        ("http://10.0.0.5/", "private"),
        ("http://192.168.1.1/", "private"),
        ("http://172.16.0.1/", "private"),
        ("http://169.254.169.254/latest/meta-data/", "cloud-metadata"),
    ])
    def test_default_refuses_internal(self, url, reason):
        with pytest.raises(FetchBlockedError, match=reason):
            DestinationPolicy().check_url(url)

    def test_allow_private_admits_everything(self):
        DestinationPolicy(allow_private=True).check_url("http://127.0.0.1/")  # no raise

    def test_allowlist_ip_admits_that_address(self):
        DestinationPolicy(allowlist=["127.0.0.1"]).check_url("http://127.0.0.1/")  # no raise

    def test_allowlist_cidr_admits_network(self):
        DestinationPolicy(allowlist=["10.0.0.0/8"]).check_url("http://10.1.2.3/")  # no raise

    def test_allowlist_is_specific(self):
        # Allow-listing one loopback address does not admit a private one.
        policy = DestinationPolicy(allowlist=["127.0.0.1"])
        with pytest.raises(FetchBlockedError):
            policy.check_url("http://10.0.0.5/")

    def test_unresolvable_host_is_left_to_the_client(self):
        # Can't resolve → nothing to reach → not our job to block.
        DestinationPolicy().check_url("http://nonexistent.invalid/")  # no raise

    def test_missing_host_raises(self):
        with pytest.raises(FetchBlockedError, match="no host"):
            DestinationPolicy().check_url("not-a-url")


# ── WebFetcher end-to-end against a real loopback server ─────────────────────

class TestFetcherPolicy:
    def test_default_fetcher_refuses_loopback(self, loopback_server):
        with pytest.raises(FetchBlockedError):
            WebFetcher().fetch(loopback_server)

    def test_allowlisted_fetcher_admits_loopback(self, loopback_server):
        fetcher = WebFetcher(policy=DestinationPolicy(allowlist=["127.0.0.1"]))
        result = fetcher.fetch(loopback_server)
        assert result.status_code == 200
        assert "internal page" in result.html

    def test_allow_private_fetcher_admits_loopback(self, loopback_server):
        fetcher = WebFetcher(policy=DestinationPolicy(allow_private=True))
        assert fetcher.fetch(loopback_server).status_code == 200
