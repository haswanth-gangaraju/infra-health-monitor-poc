"""
Unit tests for src.alert_engine.

Tests threshold evaluation, severity assignment, deduplication, and
alert history management.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from src.alert_engine import Alert, AlertEngine, Severity
from src.config import ThresholdConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_thresholds() -> ThresholdConfig:
    """Standard thresholds for testing."""
    return ThresholdConfig(
        cpu_percent=90.0,
        memory_percent=85.0,
        disk_percent=90.0,
        network_error_rate=0.01,
    )


@pytest.fixture
def engine(default_thresholds: ThresholdConfig) -> AlertEngine:
    """AlertEngine with default thresholds and no dedup window for testing."""
    return AlertEngine(default_thresholds, dedup_window_s=0.0)


@pytest.fixture
def normal_metrics() -> dict:
    """System metrics where everything is within normal thresholds."""
    return {
        "cpu": {
            "percent_aggregate": 45.0,
            "percent_per_core": [40.0, 50.0],
        },
        "memory": {
            "percent": 60.0,
            "total_bytes": 16 * 1024 ** 3,
            "used_bytes": 10 * 1024 ** 3,
        },
        "disks": [
            {"mountpoint": "/", "percent": 55.0, "device": "/dev/sda1"},
        ],
        "network_io": {
            "packets_sent": 1000,
            "packets_recv": 2000,
            "errin": 0,
            "errout": 0,
        },
    }


# ---------------------------------------------------------------------------
# Tests: Alert data class
# ---------------------------------------------------------------------------

class TestAlert:
    def test_to_dict(self) -> None:
        alert = Alert(
            severity=Severity.WARNING,
            source="cpu",
            message="CPU high",
            value=95.0,
            threshold=90.0,
        )
        d = alert.to_dict()

        assert d["severity"] == "WARNING"
        assert d["source"] == "cpu"
        assert d["value"] == 95.0
        assert "timestamp" in d

    def test_default_timestamp(self) -> None:
        alert = Alert(severity=Severity.INFO, source="test", message="msg")
        assert alert.timestamp is not None
        assert len(alert.timestamp) > 0


# ---------------------------------------------------------------------------
# Tests: Severity enum
# ---------------------------------------------------------------------------

class TestSeverity:
    def test_values(self) -> None:
        assert Severity.INFO.value == "INFO"
        assert Severity.WARNING.value == "WARNING"
        assert Severity.CRITICAL.value == "CRITICAL"

    def test_is_string_enum(self) -> None:
        assert isinstance(Severity.INFO, str)
        assert Severity.INFO == "INFO"


# ---------------------------------------------------------------------------
# Tests: evaluate() - system metrics
# ---------------------------------------------------------------------------

class TestEvaluateSystemMetrics:
    def test_no_alerts_when_normal(
        self, engine: AlertEngine, normal_metrics: dict
    ) -> None:
        alerts = engine.evaluate(normal_metrics)
        assert len(alerts) == 0

    def test_cpu_warning(self, engine: AlertEngine) -> None:
        metrics = {
            "cpu": {"percent_aggregate": 92.0},
            "memory": {"percent": 50.0},
            "disks": [],
            "network_io": {"packets_sent": 100, "packets_recv": 100, "errin": 0, "errout": 0},
        }
        alerts = engine.evaluate(metrics)

        cpu_alerts = [a for a in alerts if a.source == "cpu"]
        assert len(cpu_alerts) == 1
        assert cpu_alerts[0].severity == Severity.WARNING
        assert cpu_alerts[0].value == 92.0

    def test_cpu_critical(self, engine: AlertEngine) -> None:
        metrics = {
            "cpu": {"percent_aggregate": 99.0},
            "memory": {"percent": 50.0},
            "disks": [],
            "network_io": {"packets_sent": 100, "packets_recv": 100, "errin": 0, "errout": 0},
        }
        alerts = engine.evaluate(metrics)

        cpu_alerts = [a for a in alerts if a.source == "cpu"]
        assert len(cpu_alerts) == 1
        assert cpu_alerts[0].severity == Severity.CRITICAL

    def test_memory_warning(self, engine: AlertEngine) -> None:
        metrics = {
            "cpu": {"percent_aggregate": 30.0},
            "memory": {"percent": 88.0},
            "disks": [],
            "network_io": {"packets_sent": 100, "packets_recv": 100, "errin": 0, "errout": 0},
        }
        alerts = engine.evaluate(metrics)

        mem_alerts = [a for a in alerts if a.source == "memory"]
        assert len(mem_alerts) == 1
        assert mem_alerts[0].severity == Severity.WARNING

    def test_memory_critical(self, engine: AlertEngine) -> None:
        metrics = {
            "cpu": {"percent_aggregate": 30.0},
            "memory": {"percent": 96.0},
            "disks": [],
            "network_io": {"packets_sent": 100, "packets_recv": 100, "errin": 0, "errout": 0},
        }
        alerts = engine.evaluate(metrics)

        mem_alerts = [a for a in alerts if a.source == "memory"]
        assert len(mem_alerts) == 1
        assert mem_alerts[0].severity == Severity.CRITICAL

    def test_disk_warning(self, engine: AlertEngine) -> None:
        metrics = {
            "cpu": {"percent_aggregate": 30.0},
            "memory": {"percent": 50.0},
            "disks": [
                {"mountpoint": "/", "percent": 92.0},
                {"mountpoint": "/data", "percent": 45.0},
            ],
            "network_io": {"packets_sent": 100, "packets_recv": 100, "errin": 0, "errout": 0},
        }
        alerts = engine.evaluate(metrics)

        disk_alerts = [a for a in alerts if a.source.startswith("disk:")]
        assert len(disk_alerts) == 1
        assert disk_alerts[0].source == "disk:/"
        assert disk_alerts[0].severity == Severity.WARNING

    def test_network_error_rate(self, engine: AlertEngine) -> None:
        metrics = {
            "cpu": {"percent_aggregate": 30.0},
            "memory": {"percent": 50.0},
            "disks": [],
            "network_io": {
                "packets_sent": 500,
                "packets_recv": 500,
                "errin": 20,   # 2% of 1000 total
                "errout": 0,
            },
        }
        alerts = engine.evaluate(metrics)

        net_alerts = [a for a in alerts if a.source == "network_errors"]
        assert len(net_alerts) == 1

    def test_multiple_alerts_at_once(self, engine: AlertEngine) -> None:
        metrics = {
            "cpu": {"percent_aggregate": 95.0},
            "memory": {"percent": 90.0},
            "disks": [{"mountpoint": "/", "percent": 95.0}],
            "network_io": {"packets_sent": 100, "packets_recv": 100, "errin": 5, "errout": 5},
        }
        alerts = engine.evaluate(metrics)

        sources = {a.source for a in alerts}
        assert "cpu" in sources
        assert "memory" in sources
        assert "disk:/" in sources
        assert "network_errors" in sources


# ---------------------------------------------------------------------------
# Tests: evaluate_network()
# ---------------------------------------------------------------------------

class TestEvaluateNetwork:
    def test_dns_failure_alert(self, engine: AlertEngine) -> None:
        net_results = {
            "dns": [
                {"domain": "bad.com", "status": "FAIL", "details": "timeout"},
            ],
            "tcp": [],
            "ping": [],
        }
        alerts = engine.evaluate_network(net_results)

        assert len(alerts) == 1
        assert alerts[0].source == "dns:bad.com"
        assert alerts[0].severity == Severity.WARNING

    def test_tcp_failure_alert(self, engine: AlertEngine) -> None:
        net_results = {
            "dns": [],
            "tcp": [
                {"label": "Web Server", "status": "FAIL",
                 "details": "connection refused"},
            ],
            "ping": [],
        }
        alerts = engine.evaluate_network(net_results)

        assert len(alerts) == 1
        assert alerts[0].source == "tcp:Web Server"
        assert alerts[0].severity == Severity.CRITICAL

    def test_ping_failure_alert(self, engine: AlertEngine) -> None:
        net_results = {
            "dns": [],
            "tcp": [],
            "ping": [
                {"target": "10.0.0.1", "status": "FAIL",
                 "details": "100% packet loss"},
            ],
        }
        alerts = engine.evaluate_network(net_results)

        assert len(alerts) == 1
        assert alerts[0].source == "ping:10.0.0.1"

    def test_no_alerts_when_healthy(self, engine: AlertEngine) -> None:
        net_results = {
            "dns": [{"domain": "ok.com", "status": "PASS", "details": "fine"}],
            "tcp": [{"label": "OK", "status": "PASS", "details": "fine"}],
            "ping": [{"target": "8.8.8.8", "status": "PASS", "details": "fine"}],
        }
        alerts = engine.evaluate_network(net_results)
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# Tests: evaluate_diagnostics()
# ---------------------------------------------------------------------------

class TestEvaluateDiagnostics:
    def test_disk_warn(self, engine: AlertEngine) -> None:
        diag = {
            "disk_io": {"status": "WARN", "details": "latency spike"},
            "memory": {"status": "PASS", "details": "ok"},
            "cpu": {"status": "PASS", "details": "ok"},
            "network_errors": [],
        }
        alerts = engine.evaluate_diagnostics(diag)

        assert len(alerts) == 1
        assert alerts[0].source == "diag:disk_io"
        assert alerts[0].severity == Severity.WARNING

    def test_memory_fail(self, engine: AlertEngine) -> None:
        diag = {
            "disk_io": {"status": "PASS", "details": "ok"},
            "memory": {"status": "FAIL", "details": "hash mismatch"},
            "cpu": {"status": "PASS", "details": "ok"},
            "network_errors": [],
        }
        alerts = engine.evaluate_diagnostics(diag)

        assert len(alerts) == 1
        assert alerts[0].severity == Severity.CRITICAL

    def test_nic_error_from_diagnostics(self, engine: AlertEngine) -> None:
        diag = {
            "disk_io": {"status": "PASS", "details": "ok"},
            "memory": {"status": "PASS", "details": "ok"},
            "cpu": {"status": "PASS", "details": "ok"},
            "network_errors": [
                {"status": "WARN", "details": "eth0 high errors",
                 "metrics": {"interface": "eth0"}},
            ],
        }
        alerts = engine.evaluate_diagnostics(diag)

        assert len(alerts) == 1
        assert alerts[0].source == "diag:nic:eth0"


# ---------------------------------------------------------------------------
# Tests: Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_dedup_suppresses_rapid_fires(self) -> None:
        """With a 60s dedup window, the same source should not fire twice."""
        thresholds = ThresholdConfig(cpu_percent=50.0)
        engine = AlertEngine(thresholds, dedup_window_s=60.0)

        metrics = {
            "cpu": {"percent_aggregate": 80.0},
            "memory": {"percent": 30.0},
            "disks": [],
            "network_io": {"packets_sent": 100, "packets_recv": 100, "errin": 0, "errout": 0},
        }

        first = engine.evaluate(metrics)
        second = engine.evaluate(metrics)

        assert len(first) == 1
        assert len(second) == 0  # suppressed by dedup

    def test_dedup_allows_after_window(self) -> None:
        """After the dedup window expires, the alert fires again."""
        thresholds = ThresholdConfig(cpu_percent=50.0)
        engine = AlertEngine(thresholds, dedup_window_s=0.0)

        metrics = {
            "cpu": {"percent_aggregate": 80.0},
            "memory": {"percent": 30.0},
            "disks": [],
            "network_io": {"packets_sent": 100, "packets_recv": 100, "errin": 0, "errout": 0},
        }

        first = engine.evaluate(metrics)
        second = engine.evaluate(metrics)

        assert len(first) == 1
        assert len(second) == 1  # dedup window is 0


# ---------------------------------------------------------------------------
# Tests: History management
# ---------------------------------------------------------------------------

class TestAlertHistory:
    def test_history_accumulates(self, engine: AlertEngine) -> None:
        metrics = {
            "cpu": {"percent_aggregate": 95.0},
            "memory": {"percent": 30.0},
            "disks": [],
            "network_io": {"packets_sent": 100, "packets_recv": 100, "errin": 0, "errout": 0},
        }
        engine.evaluate(metrics)

        assert len(engine.history) == 1
        assert engine.history[0].source == "cpu"

    def test_get_recent_alerts(self, engine: AlertEngine) -> None:
        for i in range(5):
            engine.history.append(Alert(
                severity=Severity.INFO,
                source=f"test:{i}",
                message=f"Alert {i}",
            ))

        recent = engine.get_recent_alerts(count=3)
        assert len(recent) == 3
        assert recent[-1].source == "test:4"

    def test_get_alert_summary(self, engine: AlertEngine) -> None:
        engine.history = [
            Alert(severity=Severity.INFO, source="a", message="m"),
            Alert(severity=Severity.WARNING, source="b", message="m"),
            Alert(severity=Severity.WARNING, source="c", message="m"),
            Alert(severity=Severity.CRITICAL, source="d", message="m"),
        ]

        summary = engine.get_alert_summary()

        assert summary["INFO"] == 1
        assert summary["WARNING"] == 2
        assert summary["CRITICAL"] == 1

    def test_clear_history(self, engine: AlertEngine) -> None:
        engine.history.append(
            Alert(severity=Severity.INFO, source="x", message="m")
        )
        assert len(engine.history) == 1

        engine.clear_history()
        assert len(engine.history) == 0

    def test_max_history_cap(self) -> None:
        thresholds = ThresholdConfig(cpu_percent=1.0)  # always triggers
        engine = AlertEngine(thresholds, dedup_window_s=0.0, max_history=5)

        metrics = {
            "cpu": {"percent_aggregate": 50.0},
            "memory": {"percent": 30.0},
            "disks": [],
            "network_io": {"packets_sent": 100, "packets_recv": 100, "errin": 0, "errout": 0},
        }

        for _ in range(10):
            engine.evaluate(metrics)

        assert len(engine.history) <= 5


# ---------------------------------------------------------------------------
# Tests: print_alerts (ensure no crash)
# ---------------------------------------------------------------------------

class TestPrintAlerts:
    def test_print_empty_list(self, engine: AlertEngine) -> None:
        """Printing an empty list should not crash."""
        engine.print_alerts([])

    def test_print_alerts_runs(self, engine: AlertEngine) -> None:
        alerts = [
            Alert(severity=Severity.INFO, source="test", message="Info msg"),
            Alert(severity=Severity.WARNING, source="test", message="Warn msg"),
            Alert(severity=Severity.CRITICAL, source="test", message="Crit msg"),
        ]
        # Should complete without raising
        engine.print_alerts(alerts)
