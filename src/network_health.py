"""
Network health checker.

Validates network connectivity and service availability through:

- **DNS resolution**: resolve domains and measure query latency.
- **DHCP lease status**: parse lease information from the OS.
- **TCP connectivity**: connect to host:port pairs with timeout.
- **Ping (ICMP)**: measure round-trip latency to targets.
- **Interface status**: report link state, speed, and duplex.

All functions return structured dicts so results can be consumed by the
alert engine or dashboard without format coupling.

Cross-platform notes:
- ICMP ping via ``ping3`` may require elevated privileges on some systems.
  On failure, falls back to reporting the error gracefully.
- DHCP lease parsing reads ``/var/lib/dhcp/`` on Linux; on Windows it
  queries ``ipconfig /all`` output.
"""

from __future__ import annotations

import platform
import re
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional

import psutil

try:
    import dns.resolver
    HAS_DNSPYTHON = True
except ImportError:
    HAS_DNSPYTHON = False

try:
    from ping3 import ping  # type: ignore[import-untyped]
    HAS_PING3 = True
except ImportError:
    HAS_PING3 = False


# ---------------------------------------------------------------------------
# DNS resolution check
# ---------------------------------------------------------------------------

def check_dns_resolution(
    domains: List[str],
    timeout_ms: float = 2000.0,
) -> List[Dict[str, Any]]:
    """
    Resolve each domain and measure query latency.

    Uses ``dnspython`` if available, otherwise falls back to
    ``socket.getaddrinfo``.

    Args:
        domains: List of domain names to resolve.
        timeout_ms: Maximum acceptable resolution time (ms).

    Returns:
        List of result dicts with ``domain``, ``resolved_ip``,
        ``latency_ms``, ``status``.
    """
    results: List[Dict[str, Any]] = []

    for domain in domains:
        t0 = time.perf_counter()
        try:
            if HAS_DNSPYTHON:
                resolver = dns.resolver.Resolver()
                resolver.lifetime = timeout_ms / 1000.0
                answers = resolver.resolve(domain, "A")
                ip = str(answers[0])
            else:
                info = socket.getaddrinfo(domain, None, socket.AF_INET)
                ip = info[0][4][0] if info else "unresolved"

            latency = (time.perf_counter() - t0) * 1000

            status = "PASS" if latency <= timeout_ms else "WARN"
            results.append({
                "domain": domain,
                "resolved_ip": ip,
                "latency_ms": round(latency, 2),
                "status": status,
                "details": f"{domain} -> {ip} in {latency:.1f}ms",
            })

        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000
            results.append({
                "domain": domain,
                "resolved_ip": None,
                "latency_ms": round(latency, 2),
                "status": "FAIL",
                "details": f"{domain}: DNS resolution failed ({exc})",
            })

    return results


# ---------------------------------------------------------------------------
# DHCP lease status
# ---------------------------------------------------------------------------

def check_dhcp_status() -> Dict[str, Any]:
    """
    Retrieve DHCP lease information from the operating system.

    On Linux, attempts to read lease files from common locations.
    On Windows, parses ``ipconfig /all`` output.

    Returns:
        Dict with ``available`` (bool), ``leases`` list, ``details`` string.
    """
    system = platform.system()
    leases: List[Dict[str, str]] = []

    if system == "Linux":
        lease_paths = [
            "/var/lib/dhcp/dhclient.leases",
            "/var/lib/dhclient/dhclient.leases",
            "/var/lib/NetworkManager/",
        ]
        found = False
        for path in lease_paths:
            try:
                import glob as _glob
                if path.endswith("/"):
                    files = _glob.glob(path + "*.lease")
                else:
                    files = [path]
                for fpath in files:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                    # Simple parser for ISC dhclient lease blocks
                    blocks = content.split("lease {")
                    for block in blocks[1:]:
                        lease_info: Dict[str, str] = {}
                        for line in block.splitlines():
                            line = line.strip().rstrip(";")
                            if line.startswith("fixed-address"):
                                lease_info["ip"] = line.split()[-1]
                            elif line.startswith("option dhcp-server-identifier"):
                                lease_info["dhcp_server"] = line.split()[-1]
                            elif line.startswith("expire"):
                                lease_info["expires"] = " ".join(line.split()[1:])
                        if lease_info:
                            leases.append(lease_info)
                            found = True
            except (OSError, PermissionError):
                continue

        if found:
            return {
                "available": True,
                "leases": leases,
                "details": f"Found {len(leases)} DHCP lease(s) on Linux",
            }
        return {
            "available": False,
            "leases": [],
            "details": "No DHCP lease files found (static IP or non-standard path)",
        }

    elif system == "Windows":
        try:
            output = subprocess.check_output(
                ["ipconfig", "/all"],
                text=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            # Look for DHCP-related lines
            dhcp_enabled = "DHCP Enabled" in output and "Yes" in output
            # Extract DHCP server
            match = re.search(r"DHCP Server[.\s]*:\s*([\d.]+)", output)
            dhcp_server = match.group(1) if match else "unknown"

            if dhcp_enabled:
                leases.append({
                    "dhcp_server": dhcp_server,
                    "dhcp_enabled": "yes",
                })
                return {
                    "available": True,
                    "leases": leases,
                    "details": f"DHCP enabled, server: {dhcp_server}",
                }
            return {
                "available": False,
                "leases": [],
                "details": "DHCP not enabled (static IP configuration)",
            }
        except (subprocess.SubprocessError, OSError) as exc:
            return {
                "available": False,
                "leases": [],
                "details": f"Could not query DHCP status: {exc}",
            }

    else:
        return {
            "available": False,
            "leases": [],
            "details": f"DHCP lease check not implemented for {system}",
        }


# ---------------------------------------------------------------------------
# TCP connectivity test
# ---------------------------------------------------------------------------

def check_tcp_connectivity(
    targets: List[Dict[str, Any]],
    timeout_s: float = 5.0,
) -> List[Dict[str, Any]]:
    """
    Attempt a TCP connect to each host:port pair.

    Args:
        targets: List of dicts with ``host``, ``port``, and optional ``label``.
        timeout_s: Socket connect timeout in seconds.

    Returns:
        List of result dicts with ``host``, ``port``, ``label``,
        ``connected`` (bool), ``latency_ms``, ``status``, ``details``.
    """
    results: List[Dict[str, Any]] = []

    for target in targets:
        host = target["host"]
        port = int(target["port"])
        label = target.get("label", f"{host}:{port}")

        t0 = time.perf_counter()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout_s)
            sock.connect((host, port))
            latency = (time.perf_counter() - t0) * 1000
            sock.close()

            results.append({
                "host": host,
                "port": port,
                "label": label,
                "connected": True,
                "latency_ms": round(latency, 2),
                "status": "PASS",
                "details": f"{label}: connected in {latency:.1f}ms",
            })

        except (socket.timeout, socket.error, OSError) as exc:
            latency = (time.perf_counter() - t0) * 1000
            results.append({
                "host": host,
                "port": port,
                "label": label,
                "connected": False,
                "latency_ms": round(latency, 2),
                "status": "FAIL",
                "details": f"{label}: connection failed ({exc})",
            })

    return results


# ---------------------------------------------------------------------------
# Ping test
# ---------------------------------------------------------------------------

def check_ping(
    targets: List[str],
    timeout_s: float = 2.0,
    count: int = 3,
) -> List[Dict[str, Any]]:
    """
    Ping each target and measure round-trip latency.

    Uses ``ping3`` library for cross-platform ICMP. Falls back to
    reporting an error if the library is unavailable or privileges
    are insufficient.

    Args:
        targets: List of IP addresses or hostnames.
        timeout_s: Per-ping timeout in seconds.
        count: Number of pings per target.

    Returns:
        List of result dicts with ``target``, ``avg_latency_ms``,
        ``min_latency_ms``, ``max_latency_ms``, ``packet_loss``,
        ``status``, ``details``.
    """
    results: List[Dict[str, Any]] = []

    for target in targets:
        if not HAS_PING3:
            results.append({
                "target": target,
                "avg_latency_ms": None,
                "min_latency_ms": None,
                "max_latency_ms": None,
                "packet_loss": 1.0,
                "status": "FAIL",
                "details": f"{target}: ping3 library not installed",
            })
            continue

        latencies: List[float] = []
        failures = 0

        for _ in range(count):
            try:
                rtt = ping(target, timeout=timeout_s, unit="ms")
                if rtt is None or rtt is False:
                    failures += 1
                else:
                    latencies.append(float(rtt))
            except OSError:
                failures += 1

        loss = failures / count
        if latencies:
            avg_lat = sum(latencies) / len(latencies)
            min_lat = min(latencies)
            max_lat = max(latencies)
            status = "PASS" if loss < 0.5 else "WARN"
            details = (
                f"{target}: avg={avg_lat:.1f}ms, "
                f"min={min_lat:.1f}ms, max={max_lat:.1f}ms, "
                f"loss={loss:.0%}"
            )
        else:
            avg_lat = min_lat = max_lat = None  # type: ignore[assignment]
            status = "FAIL"
            details = f"{target}: 100% packet loss"

        results.append({
            "target": target,
            "avg_latency_ms": round(avg_lat, 2) if avg_lat is not None else None,
            "min_latency_ms": round(min_lat, 2) if min_lat is not None else None,
            "max_latency_ms": round(max_lat, 2) if max_lat is not None else None,
            "packet_loss": round(loss, 2),
            "status": status,
            "details": details,
        })

    return results


# ---------------------------------------------------------------------------
# Network interface status
# ---------------------------------------------------------------------------

def get_interface_status() -> List[Dict[str, Any]]:
    """
    Get the status of all network interfaces.

    Reports link state (up/down), speed, duplex, and MTU where available.

    Returns:
        List of dicts with ``name``, ``is_up``, ``speed_mbps``, ``duplex``,
        ``mtu``, ``addresses``.
    """
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    results: List[Dict[str, Any]] = []

    for name, stat in stats.items():
        # Collect IP addresses for this interface
        iface_addrs: List[str] = []
        if name in addrs:
            for addr in addrs[name]:
                if addr.family in (socket.AF_INET, getattr(socket, "AF_INET6", -1)):
                    iface_addrs.append(addr.address)

        # Duplex mapping
        duplex_map = {
            psutil.NIC_DUPLEX_FULL: "full",
            psutil.NIC_DUPLEX_HALF: "half",
            psutil.NIC_DUPLEX_UNKNOWN: "unknown",
        }

        results.append({
            "name": name,
            "is_up": stat.isup,
            "speed_mbps": stat.speed,
            "duplex": duplex_map.get(stat.duplex, "unknown"),
            "mtu": stat.mtu,
            "addresses": iface_addrs,
        })

    return results


# ---------------------------------------------------------------------------
# Aggregate network health check
# ---------------------------------------------------------------------------

def run_all_network_checks(
    dns_domains: Optional[List[str]] = None,
    tcp_targets: Optional[List[Dict[str, Any]]] = None,
    ping_targets: Optional[List[str]] = None,
    dns_timeout_ms: float = 2000.0,
    tcp_timeout_s: float = 5.0,
    ping_timeout_s: float = 2.0,
) -> Dict[str, Any]:
    """
    Run the complete network health check suite.

    Args:
        dns_domains: Domains to resolve. Defaults to common public domains.
        tcp_targets: Host/port dicts for TCP probes.
        ping_targets: IPs/hostnames to ping.
        dns_timeout_ms: DNS query timeout.
        tcp_timeout_s: TCP connect timeout.
        ping_timeout_s: ICMP ping timeout.

    Returns:
        Dict with keys ``dns``, ``dhcp``, ``tcp``, ``ping``,
        ``interfaces``, ``summary``.
    """
    if dns_domains is None:
        dns_domains = ["google.com", "cloudflare.com"]
    if tcp_targets is None:
        tcp_targets = [
            {"host": "8.8.8.8", "port": 53, "label": "Google DNS"},
        ]
    if ping_targets is None:
        ping_targets = ["8.8.8.8"]

    dns_results = check_dns_resolution(dns_domains, timeout_ms=dns_timeout_ms)
    dhcp_status = check_dhcp_status()
    tcp_results = check_tcp_connectivity(tcp_targets, timeout_s=tcp_timeout_s)
    ping_results = check_ping(ping_targets, timeout_s=ping_timeout_s)
    interfaces = get_interface_status()

    # Determine overall summary
    all_statuses: List[str] = []
    all_statuses.extend(r["status"] for r in dns_results)
    all_statuses.extend(r["status"] for r in tcp_results)
    all_statuses.extend(r["status"] for r in ping_results)

    if "FAIL" in all_statuses:
        summary = "DEGRADED"
    elif "WARN" in all_statuses:
        summary = "WARNING"
    else:
        summary = "HEALTHY"

    return {
        "dns": dns_results,
        "dhcp": dhcp_status,
        "tcp": tcp_results,
        "ping": ping_results,
        "interfaces": interfaces,
        "summary": summary,
    }
