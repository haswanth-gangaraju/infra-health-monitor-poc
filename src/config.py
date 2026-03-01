"""
Configuration management for Infrastructure Health Monitor.

Loads settings from a YAML file or falls back to sensible defaults.
Provides typed access to thresholds, monitored hosts, DNS targets,
and check intervals.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# ---------------------------------------------------------------------------
# Data classes for structured configuration
# ---------------------------------------------------------------------------

@dataclass
class ThresholdConfig:
    """Alert thresholds -- values above these trigger warnings/criticals."""
    cpu_percent: float = 90.0
    memory_percent: float = 85.0
    disk_percent: float = 90.0
    network_error_rate: float = 0.01       # fraction of packets
    disk_latency_ms: float = 100.0         # milliseconds
    ping_timeout_ms: float = 1000.0        # milliseconds
    dns_timeout_ms: float = 2000.0         # milliseconds
    tcp_connect_timeout_s: float = 5.0     # seconds


@dataclass
class MonitoredHost:
    """A host/port pair to check with TCP connect probes."""
    host: str
    port: int
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = f"{self.host}:{self.port}"


@dataclass
class AppConfig:
    """Top-level application configuration."""
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    monitored_hosts: List[MonitoredHost] = field(default_factory=list)
    dns_check_domains: List[str] = field(default_factory=list)
    ping_targets: List[str] = field(default_factory=list)
    check_interval_seconds: int = 5
    dashboard_refresh_seconds: int = 5
    log_file: Optional[str] = None

    def __post_init__(self) -> None:
        # Provide sensible defaults when lists are empty
        if not self.monitored_hosts:
            self.monitored_hosts = [
                MonitoredHost(host="8.8.8.8", port=53, label="Google DNS"),
                MonitoredHost(host="1.1.1.1", port=53, label="Cloudflare DNS"),
                MonitoredHost(host="208.67.222.222", port=53, label="OpenDNS"),
            ]
        if not self.dns_check_domains:
            self.dns_check_domains = [
                "google.com",
                "cloudflare.com",
                "github.com",
            ]
        if not self.ping_targets:
            self.ping_targets = ["8.8.8.8", "1.1.1.1"]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _parse_thresholds(raw: Dict[str, Any]) -> ThresholdConfig:
    """Parse threshold section from raw YAML dict."""
    return ThresholdConfig(
        cpu_percent=float(raw.get("cpu_percent", 90.0)),
        memory_percent=float(raw.get("memory_percent", 85.0)),
        disk_percent=float(raw.get("disk_percent", 90.0)),
        network_error_rate=float(raw.get("network_error_rate", 0.01)),
        disk_latency_ms=float(raw.get("disk_latency_ms", 100.0)),
        ping_timeout_ms=float(raw.get("ping_timeout_ms", 1000.0)),
        dns_timeout_ms=float(raw.get("dns_timeout_ms", 2000.0)),
        tcp_connect_timeout_s=float(raw.get("tcp_connect_timeout_s", 5.0)),
    )


def _parse_hosts(raw_list: List[Dict[str, Any]]) -> List[MonitoredHost]:
    """Parse monitored_hosts list from raw YAML."""
    hosts: List[MonitoredHost] = []
    for entry in raw_list:
        hosts.append(MonitoredHost(
            host=str(entry["host"]),
            port=int(entry.get("port", 443)),
            label=str(entry.get("label", "")),
        ))
    return hosts


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """
    Load configuration from a YAML file.

    Falls back to built-in defaults if no file is provided or if the
    file cannot be read.

    Args:
        config_path: Path to a YAML configuration file. If ``None``,
                     looks for ``config.yaml`` next to the project root.

    Returns:
        Fully populated ``AppConfig`` instance.
    """
    if config_path is None:
        # Try the default location relative to project root
        project_root = Path(__file__).resolve().parent.parent
        candidate = project_root / "config.yaml"
        if candidate.exists():
            config_path = str(candidate)

    if config_path and os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as fh:
            raw: Dict[str, Any] = yaml.safe_load(fh) or {}
    else:
        raw = {}

    thresholds = _parse_thresholds(raw.get("thresholds", {}))
    hosts_raw = raw.get("monitored_hosts", [])
    monitored_hosts = _parse_hosts(hosts_raw) if hosts_raw else []
    dns_domains = raw.get("dns_check_domains", [])
    ping_targets = raw.get("ping_targets", [])
    interval = int(raw.get("check_interval_seconds", 5))
    refresh = int(raw.get("dashboard_refresh_seconds", 5))
    log_file = raw.get("log_file")

    return AppConfig(
        thresholds=thresholds,
        monitored_hosts=monitored_hosts,
        dns_check_domains=dns_domains,
        ping_targets=ping_targets,
        check_interval_seconds=interval,
        dashboard_refresh_seconds=refresh,
        log_file=log_file,
    )
