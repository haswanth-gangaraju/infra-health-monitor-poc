"""
Threshold-based alerting engine.

Evaluates system metrics against configurable thresholds and produces
alerts with severity levels: **INFO**, **WARNING**, **CRITICAL**.

Features:
- Configurable thresholds via ``ThresholdConfig``.
- Alert history tracking with timestamps.
- Console output with colour coding (via ``rich``).
- Deduplication window to avoid alert storms.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.text import Text


# ---------------------------------------------------------------------------
# Severity enum
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    """Alert severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# Colour mapping for terminal output
_SEVERITY_COLOURS = {
    Severity.INFO: "cyan",
    Severity.WARNING: "yellow",
    Severity.CRITICAL: "bold red",
}

_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.CRITICAL: 2,
}


# ---------------------------------------------------------------------------
# Alert data class
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    """A single alert instance."""
    severity: Severity
    source: str          # e.g. "cpu", "memory", "disk:/dev/sda1"
    message: str
    value: Optional[float] = None
    threshold: Optional[float] = None
    timestamp: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity.value,
            "source": self.source,
            "message": self.message,
            "value": self.value,
            "threshold": self.threshold,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Alert engine
# ---------------------------------------------------------------------------

class AlertEngine:
    """
    Evaluate metrics against thresholds and manage alert lifecycle.

    Usage::

        from src.config import load_config
        cfg = load_config()
        engine = AlertEngine(cfg.thresholds)
        alerts = engine.evaluate(metrics)
        engine.print_alerts(alerts)
    """

    def __init__(
        self,
        thresholds: Any,
        dedup_window_s: float = 60.0,
        max_history: int = 500,
    ) -> None:
        """
        Args:
            thresholds: A ``ThresholdConfig`` instance (or any object with
                        the same attributes).
            dedup_window_s: Suppress duplicate alerts within this window.
            max_history: Maximum number of alerts to retain in history.
        """
        self.thresholds = thresholds
        self.dedup_window_s = dedup_window_s
        self.max_history = max_history

        self.history: List[Alert] = []
        self._last_fired: Dict[str, float] = {}  # source -> epoch
        self._console = Console()

    # ----- deduplication ---------------------------------------------------

    def _should_fire(self, source: str) -> bool:
        """Return True if enough time has passed since the last alert for this source."""
        last = self._last_fired.get(source)
        now = time.time()
        if last is None or (now - last) >= self.dedup_window_s:
            self._last_fired[source] = now
            return True
        return False

    # ----- evaluation ------------------------------------------------------

    def evaluate(self, metrics: Dict[str, Any]) -> List[Alert]:
        """
        Evaluate a full metrics snapshot and return any triggered alerts.

        Checks:
        - CPU aggregate usage
        - Memory usage
        - Per-disk usage
        - Network error/drop rates

        Args:
            metrics: Output of ``system_monitor.collect_all_metrics()``.

        Returns:
            List of ``Alert`` instances for this evaluation cycle.
        """
        alerts: List[Alert] = []

        # --- CPU -----------------------------------------------------------
        cpu = metrics.get("cpu", {})
        cpu_pct = cpu.get("percent_aggregate", 0.0)
        if cpu_pct >= self.thresholds.cpu_percent:
            severity = (
                Severity.CRITICAL if cpu_pct >= 98.0
                else Severity.WARNING
            )
            alert = Alert(
                severity=severity,
                source="cpu",
                message=f"CPU usage at {cpu_pct:.1f}%",
                value=cpu_pct,
                threshold=self.thresholds.cpu_percent,
            )
            if self._should_fire(alert.source):
                alerts.append(alert)

        # --- Memory --------------------------------------------------------
        mem = metrics.get("memory", {})
        mem_pct = mem.get("percent", 0.0)
        if mem_pct >= self.thresholds.memory_percent:
            severity = (
                Severity.CRITICAL if mem_pct >= 95.0
                else Severity.WARNING
            )
            alert = Alert(
                severity=severity,
                source="memory",
                message=f"Memory usage at {mem_pct:.1f}%",
                value=mem_pct,
                threshold=self.thresholds.memory_percent,
            )
            if self._should_fire(alert.source):
                alerts.append(alert)

        # --- Disk (per mount) -----------------------------------------------
        disks = metrics.get("disks", [])
        for disk in disks:
            disk_pct = disk.get("percent", 0.0)
            mp = disk.get("mountpoint", "unknown")
            if disk_pct >= self.thresholds.disk_percent:
                severity = (
                    Severity.CRITICAL if disk_pct >= 98.0
                    else Severity.WARNING
                )
                source = f"disk:{mp}"
                alert = Alert(
                    severity=severity,
                    source=source,
                    message=f"Disk usage at {disk_pct:.1f}% on {mp}",
                    value=disk_pct,
                    threshold=self.thresholds.disk_percent,
                )
                if self._should_fire(alert.source):
                    alerts.append(alert)

        # --- Network errors -------------------------------------------------
        net_io = metrics.get("network_io", {})
        total_pkts = (
            net_io.get("packets_sent", 0) + net_io.get("packets_recv", 0)
        )
        total_errs = net_io.get("errin", 0) + net_io.get("errout", 0)
        if total_pkts > 0:
            err_rate = total_errs / total_pkts
            if err_rate > self.thresholds.network_error_rate:
                alert = Alert(
                    severity=Severity.WARNING,
                    source="network_errors",
                    message=f"Network error rate: {err_rate:.4%}",
                    value=err_rate,
                    threshold=self.thresholds.network_error_rate,
                )
                if self._should_fire(alert.source):
                    alerts.append(alert)

        # Store in history
        self.history.extend(alerts)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

        return alerts

    def evaluate_network(self, net_results: Dict[str, Any]) -> List[Alert]:
        """
        Evaluate network health check results.

        Args:
            net_results: Output of ``network_health.run_all_network_checks()``.

        Returns:
            List of triggered alerts.
        """
        alerts: List[Alert] = []

        # DNS failures
        for dns in net_results.get("dns", []):
            if dns["status"] == "FAIL":
                alert = Alert(
                    severity=Severity.WARNING,
                    source=f"dns:{dns['domain']}",
                    message=dns["details"],
                )
                if self._should_fire(alert.source):
                    alerts.append(alert)

        # TCP failures
        for tcp in net_results.get("tcp", []):
            if tcp["status"] == "FAIL":
                alert = Alert(
                    severity=Severity.CRITICAL,
                    source=f"tcp:{tcp['label']}",
                    message=tcp["details"],
                )
                if self._should_fire(alert.source):
                    alerts.append(alert)

        # Ping failures
        for p in net_results.get("ping", []):
            if p["status"] == "FAIL":
                alert = Alert(
                    severity=Severity.WARNING,
                    source=f"ping:{p['target']}",
                    message=p["details"],
                )
                if self._should_fire(alert.source):
                    alerts.append(alert)

        self.history.extend(alerts)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

        return alerts

    def evaluate_diagnostics(self, diag_results: Dict[str, Any]) -> List[Alert]:
        """
        Evaluate hardware diagnostics results.

        Args:
            diag_results: Output of ``hardware_diagnostics.run_all_diagnostics()``.

        Returns:
            List of triggered alerts.
        """
        alerts: List[Alert] = []

        for key in ("disk_io", "memory", "cpu"):
            result = diag_results.get(key, {})
            status = result.get("status", "PASS")
            if status in ("WARN", "FAIL"):
                severity = Severity.CRITICAL if status == "FAIL" else Severity.WARNING
                alert = Alert(
                    severity=severity,
                    source=f"diag:{key}",
                    message=result.get("details", f"{key} diagnostic {status}"),
                )
                if self._should_fire(alert.source):
                    alerts.append(alert)

        # Network error checks from diagnostics
        for nic_result in diag_results.get("network_errors", []):
            if nic_result["status"] in ("WARN", "FAIL"):
                severity = (
                    Severity.CRITICAL if nic_result["status"] == "FAIL"
                    else Severity.WARNING
                )
                iface = nic_result.get("metrics", {}).get("interface", "unknown")
                alert = Alert(
                    severity=severity,
                    source=f"diag:nic:{iface}",
                    message=nic_result["details"],
                )
                if self._should_fire(alert.source):
                    alerts.append(alert)

        self.history.extend(alerts)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

        return alerts

    # ----- output ----------------------------------------------------------

    def print_alerts(self, alerts: List[Alert]) -> None:
        """Print alerts to the console with colour coding."""
        if not alerts:
            return

        for alert in alerts:
            colour = _SEVERITY_COLOURS.get(alert.severity, "white")
            tag = Text(f"[{alert.severity.value:>8s}]", style=colour)
            self._console.print(tag, alert.message)

    def get_recent_alerts(self, count: int = 10) -> List[Alert]:
        """Return the most recent ``count`` alerts from history."""
        return self.history[-count:]

    def get_alert_summary(self) -> Dict[str, int]:
        """Count alerts by severity in the current history."""
        summary: Dict[str, int] = {s.value: 0 for s in Severity}
        for alert in self.history:
            summary[alert.severity.value] += 1
        return summary

    def clear_history(self) -> None:
        """Flush the alert history."""
        self.history.clear()
        self._last_fired.clear()
