"""
Unit tests for src.network_health.

Mocks socket, dns, and ping calls to ensure tests are deterministic
and do not require actual network access.
"""

from __future__ import annotations

import socket
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest

from src.network_health import (
    check_dns_resolution,
    check_ping,
    check_tcp_connectivity,
    get_interface_status,
    run_all_network_checks,
)


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

NicStats = namedtuple("snicstats", ["isup", "duplex", "speed", "mtu", "flags"])
NicAddr = namedtuple("snicaddr", ["family", "address", "netmask", "broadcast", "ptp"])


# ---------------------------------------------------------------------------
# Tests: DNS resolution
# ---------------------------------------------------------------------------

class TestCheckDnsResolution:
    @patch("src.network_health.HAS_DNSPYTHON", False)
    @patch("src.network_health.socket")
    def test_fallback_to_socket(self, mock_socket: MagicMock) -> None:
        """When dnspython is not available, falls back to socket.getaddrinfo."""
        mock_socket.getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]
        mock_socket.AF_INET = socket.AF_INET

        results = check_dns_resolution(["example.com"])

        assert len(results) == 1
        assert results[0]["domain"] == "example.com"
        assert results[0]["resolved_ip"] == "93.184.216.34"
        assert results[0]["status"] == "PASS"
        assert results[0]["latency_ms"] >= 0

    @patch("src.network_health.HAS_DNSPYTHON", False)
    @patch("src.network_health.socket")
    def test_dns_failure(self, mock_socket: MagicMock) -> None:
        """DNS resolution failure should return FAIL status."""
        mock_socket.getaddrinfo.side_effect = socket.gaierror("Name resolution failed")
        mock_socket.AF_INET = socket.AF_INET

        results = check_dns_resolution(["nonexistent.invalid"])

        assert len(results) == 1
        assert results[0]["status"] == "FAIL"
        assert results[0]["resolved_ip"] is None

    @patch("src.network_health.HAS_DNSPYTHON", True)
    @patch("src.network_health.dns.resolver")
    def test_dnspython_resolution(self, mock_resolver_mod: MagicMock) -> None:
        """When dnspython is available, use it for resolution."""
        mock_resolver_instance = MagicMock()
        mock_resolver_mod.Resolver.return_value = mock_resolver_instance

        # Mock the answer
        mock_answer = MagicMock()
        mock_answer.__getitem__ = MagicMock(return_value="1.2.3.4")
        mock_answer.__str__ = MagicMock(return_value="1.2.3.4")
        mock_resolver_instance.resolve.return_value = [mock_answer]

        results = check_dns_resolution(["test.com"])

        assert len(results) == 1
        assert results[0]["status"] == "PASS"
        assert results[0]["latency_ms"] >= 0

    def test_multiple_domains(self) -> None:
        """Multiple domains produce one result each."""
        with patch("src.network_health.HAS_DNSPYTHON", False), \
             patch("src.network_health.socket") as mock_socket:
            mock_socket.getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0)),
            ]
            mock_socket.AF_INET = socket.AF_INET

            results = check_dns_resolution(["a.com", "b.com", "c.com"])
            assert len(results) == 3


# ---------------------------------------------------------------------------
# Tests: TCP connectivity
# ---------------------------------------------------------------------------

class TestCheckTcpConnectivity:
    @patch("src.network_health.socket.socket")
    def test_successful_connect(self, mock_sock_class: MagicMock) -> None:
        mock_instance = MagicMock()
        mock_sock_class.return_value = mock_instance
        mock_instance.connect.return_value = None

        targets = [{"host": "8.8.8.8", "port": 53, "label": "Google DNS"}]
        results = check_tcp_connectivity(targets, timeout_s=2.0)

        assert len(results) == 1
        assert results[0]["connected"] is True
        assert results[0]["status"] == "PASS"
        assert results[0]["label"] == "Google DNS"
        mock_instance.close.assert_called_once()

    @patch("src.network_health.socket.socket")
    def test_connection_refused(self, mock_sock_class: MagicMock) -> None:
        mock_instance = MagicMock()
        mock_sock_class.return_value = mock_instance
        mock_instance.connect.side_effect = ConnectionRefusedError("refused")

        targets = [{"host": "192.168.1.1", "port": 9999}]
        results = check_tcp_connectivity(targets, timeout_s=1.0)

        assert len(results) == 1
        assert results[0]["connected"] is False
        assert results[0]["status"] == "FAIL"

    @patch("src.network_health.socket.socket")
    def test_timeout(self, mock_sock_class: MagicMock) -> None:
        mock_instance = MagicMock()
        mock_sock_class.return_value = mock_instance
        mock_instance.connect.side_effect = socket.timeout("timed out")

        targets = [{"host": "10.0.0.1", "port": 80}]
        results = check_tcp_connectivity(targets, timeout_s=0.5)

        assert results[0]["connected"] is False
        assert results[0]["status"] == "FAIL"

    @patch("src.network_health.socket.socket")
    def test_default_label(self, mock_sock_class: MagicMock) -> None:
        """When no label is provided, host:port is used."""
        mock_instance = MagicMock()
        mock_sock_class.return_value = mock_instance
        mock_instance.connect.return_value = None

        targets = [{"host": "10.0.0.1", "port": 443}]
        results = check_tcp_connectivity(targets)

        assert results[0]["label"] == "10.0.0.1:443"


# ---------------------------------------------------------------------------
# Tests: Ping
# ---------------------------------------------------------------------------

class TestCheckPing:
    @patch("src.network_health.HAS_PING3", True)
    @patch("src.network_health.ping")
    def test_successful_ping(self, mock_ping: MagicMock) -> None:
        mock_ping.return_value = 15.5  # ms

        results = check_ping(["8.8.8.8"], count=3)

        assert len(results) == 1
        assert results[0]["status"] == "PASS"
        assert results[0]["avg_latency_ms"] is not None
        assert results[0]["packet_loss"] == 0.0

    @patch("src.network_health.HAS_PING3", True)
    @patch("src.network_health.ping")
    def test_all_pings_lost(self, mock_ping: MagicMock) -> None:
        mock_ping.return_value = None  # timeout

        results = check_ping(["192.168.99.99"], count=3)

        assert results[0]["status"] == "FAIL"
        assert results[0]["packet_loss"] == 1.0

    @patch("src.network_health.HAS_PING3", True)
    @patch("src.network_health.ping")
    def test_partial_loss(self, mock_ping: MagicMock) -> None:
        mock_ping.side_effect = [10.0, None, 12.0]

        results = check_ping(["1.1.1.1"], count=3)

        r = results[0]
        assert r["packet_loss"] == pytest.approx(0.33, abs=0.01)
        assert r["status"] == "PASS"  # < 50% loss

    @patch("src.network_health.HAS_PING3", False)
    def test_no_ping3_library(self) -> None:
        results = check_ping(["8.8.8.8"], count=1)

        assert results[0]["status"] == "FAIL"
        assert "ping3 library not installed" in results[0]["details"]


# ---------------------------------------------------------------------------
# Tests: Interface status
# ---------------------------------------------------------------------------

class TestGetInterfaceStatus:
    @patch("src.network_health.psutil")
    def test_interface_status(self, mock_psutil: MagicMock) -> None:
        mock_psutil.NIC_DUPLEX_FULL = 2
        mock_psutil.NIC_DUPLEX_HALF = 1
        mock_psutil.NIC_DUPLEX_UNKNOWN = 0

        mock_psutil.net_if_stats.return_value = {
            "eth0": NicStats(isup=True, duplex=2, speed=1000, mtu=1500, flags="up"),
            "lo": NicStats(isup=True, duplex=0, speed=0, mtu=65536, flags="up"),
        }
        mock_psutil.net_if_addrs.return_value = {
            "eth0": [
                NicAddr(family=socket.AF_INET, address="192.168.1.100",
                        netmask="255.255.255.0", broadcast="192.168.1.255", ptp=None),
            ],
            "lo": [
                NicAddr(family=socket.AF_INET, address="127.0.0.1",
                        netmask="255.0.0.0", broadcast=None, ptp=None),
            ],
        }

        results = get_interface_status()

        assert len(results) == 2
        eth0 = next(r for r in results if r["name"] == "eth0")
        assert eth0["is_up"] is True
        assert eth0["speed_mbps"] == 1000
        assert eth0["duplex"] == "full"
        assert "192.168.1.100" in eth0["addresses"]


# ---------------------------------------------------------------------------
# Tests: Aggregate network checks
# ---------------------------------------------------------------------------

class TestRunAllNetworkChecks:
    @patch("src.network_health.get_interface_status")
    @patch("src.network_health.check_ping")
    @patch("src.network_health.check_tcp_connectivity")
    @patch("src.network_health.check_dhcp_status")
    @patch("src.network_health.check_dns_resolution")
    def test_all_pass(
        self,
        mock_dns: MagicMock,
        mock_dhcp: MagicMock,
        mock_tcp: MagicMock,
        mock_ping: MagicMock,
        mock_iface: MagicMock,
    ) -> None:
        mock_dns.return_value = [
            {"domain": "google.com", "resolved_ip": "1.2.3.4",
             "latency_ms": 5.0, "status": "PASS", "details": "ok"},
        ]
        mock_dhcp.return_value = {"available": True, "leases": [], "details": "ok"}
        mock_tcp.return_value = [
            {"host": "8.8.8.8", "port": 53, "label": "DNS",
             "connected": True, "latency_ms": 10.0, "status": "PASS", "details": "ok"},
        ]
        mock_ping.return_value = [
            {"target": "8.8.8.8", "avg_latency_ms": 15.0,
             "min_latency_ms": 10.0, "max_latency_ms": 20.0,
             "packet_loss": 0.0, "status": "PASS", "details": "ok"},
        ]
        mock_iface.return_value = []

        result = run_all_network_checks()

        assert result["summary"] == "HEALTHY"

    @patch("src.network_health.get_interface_status")
    @patch("src.network_health.check_ping")
    @patch("src.network_health.check_tcp_connectivity")
    @patch("src.network_health.check_dhcp_status")
    @patch("src.network_health.check_dns_resolution")
    def test_degraded_on_failure(
        self,
        mock_dns: MagicMock,
        mock_dhcp: MagicMock,
        mock_tcp: MagicMock,
        mock_ping: MagicMock,
        mock_iface: MagicMock,
    ) -> None:
        mock_dns.return_value = [
            {"domain": "bad.com", "resolved_ip": None,
             "latency_ms": 2000.0, "status": "FAIL", "details": "timeout"},
        ]
        mock_dhcp.return_value = {"available": False, "leases": [], "details": "n/a"}
        mock_tcp.return_value = []
        mock_ping.return_value = []
        mock_iface.return_value = []

        result = run_all_network_checks()

        assert result["summary"] == "DEGRADED"
