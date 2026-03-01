"""
Unit tests for src.system_monitor.

Mocks psutil calls so the tests are deterministic and do not depend on
the host machine's actual hardware or OS.
"""

from __future__ import annotations

from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest

from src.system_monitor import (
    _bytes_to_gb,
    collect_all_metrics,
    get_cpu_metrics,
    get_disk_metrics,
    get_memory_metrics,
    get_network_io,
    get_per_interface_io,
    get_temperature_sensors,
    get_uptime_info,
)


# ---------------------------------------------------------------------------
# Fixtures / mock data
# ---------------------------------------------------------------------------

# Named tuples to mimic psutil return types
VirtualMemory = namedtuple("svmem", ["total", "available", "used", "percent"])
SwapMemory = namedtuple("sswap", ["total", "used", "free", "percent", "sin", "sout"])
DiskUsage = namedtuple("sdiskusage", ["total", "used", "free", "percent"])
DiskPartition = namedtuple("sdiskpart", ["device", "mountpoint", "fstype", "opts"])
NetIOCounters = namedtuple(
    "snetio",
    ["bytes_sent", "bytes_recv", "packets_sent", "packets_recv",
     "errin", "errout", "dropin", "dropout"],
)
CpuFreq = namedtuple("scpufreq", ["current", "min", "max"])


# ---------------------------------------------------------------------------
# Tests: _bytes_to_gb
# ---------------------------------------------------------------------------

class TestBytesToGb:
    def test_zero(self) -> None:
        assert _bytes_to_gb(0) == 0.0

    def test_one_gb(self) -> None:
        assert _bytes_to_gb(1024 ** 3) == 1.0

    def test_fractional(self) -> None:
        result = _bytes_to_gb(int(2.5 * 1024 ** 3))
        assert abs(result - 2.5) < 0.01


# ---------------------------------------------------------------------------
# Tests: CPU metrics
# ---------------------------------------------------------------------------

class TestGetCpuMetrics:
    @patch("src.system_monitor.psutil")
    def test_basic_cpu_metrics(self, mock_psutil: MagicMock) -> None:
        mock_psutil.cpu_percent.side_effect = [
            [10.0, 20.0, 30.0, 40.0],  # percpu=True call
            25.0,                        # percpu=False call
        ]
        mock_psutil.getloadavg.return_value = (1.5, 2.0, 2.5)
        mock_psutil.cpu_count.side_effect = [4, 2]  # logical, physical
        mock_psutil.cpu_freq.return_value = CpuFreq(current=3600.0, min=800.0, max=4200.0)

        result = get_cpu_metrics()

        assert result["percent_per_core"] == [10.0, 20.0, 30.0, 40.0]
        assert result["percent_aggregate"] == 25.0
        assert result["load_avg"] == (1.5, 2.0, 2.5)
        assert result["core_count_logical"] == 4
        assert result["core_count_physical"] == 2
        assert result["freq_current_mhz"] == 3600.0

    @patch("src.system_monitor.psutil")
    def test_no_load_avg_on_windows(self, mock_psutil: MagicMock) -> None:
        """Simulate Windows where getloadavg raises AttributeError."""
        mock_psutil.cpu_percent.side_effect = [[50.0], 50.0]
        mock_psutil.getloadavg.side_effect = AttributeError
        mock_psutil.cpu_count.side_effect = [1, 1]
        mock_psutil.cpu_freq.return_value = None

        result = get_cpu_metrics()

        assert result["load_avg"] is None
        assert result["freq_current_mhz"] is None


# ---------------------------------------------------------------------------
# Tests: Memory metrics
# ---------------------------------------------------------------------------

class TestGetMemoryMetrics:
    @patch("src.system_monitor.psutil")
    def test_memory_metrics(self, mock_psutil: MagicMock) -> None:
        mock_psutil.virtual_memory.return_value = VirtualMemory(
            total=16 * 1024 ** 3,
            available=8 * 1024 ** 3,
            used=8 * 1024 ** 3,
            percent=50.0,
        )
        mock_psutil.swap_memory.return_value = SwapMemory(
            total=4 * 1024 ** 3,
            used=1 * 1024 ** 3,
            free=3 * 1024 ** 3,
            percent=25.0,
            sin=0,
            sout=0,
        )

        result = get_memory_metrics()

        assert result["total_bytes"] == 16 * 1024 ** 3
        assert result["percent"] == 50.0
        assert result["swap"]["percent"] == 25.0


# ---------------------------------------------------------------------------
# Tests: Disk metrics
# ---------------------------------------------------------------------------

class TestGetDiskMetrics:
    @patch("src.system_monitor.psutil")
    def test_disk_metrics_filters_tmpfs(self, mock_psutil: MagicMock) -> None:
        mock_psutil.disk_partitions.return_value = [
            DiskPartition("/dev/sda1", "/", "ext4", "rw"),
            DiskPartition("tmpfs", "/tmp", "tmpfs", "rw"),
        ]
        mock_psutil.disk_usage.return_value = DiskUsage(
            total=500 * 1024 ** 3,
            used=250 * 1024 ** 3,
            free=250 * 1024 ** 3,
            percent=50.0,
        )

        result = get_disk_metrics()

        # Should only include ext4, not tmpfs
        assert len(result) == 1
        assert result[0]["mountpoint"] == "/"
        assert result[0]["fstype"] == "ext4"
        assert result[0]["percent"] == 50.0

    @patch("src.system_monitor.psutil")
    def test_permission_error_skipped(self, mock_psutil: MagicMock) -> None:
        mock_psutil.disk_partitions.return_value = [
            DiskPartition("/dev/sda1", "/mnt/protected", "ext4", "rw"),
        ]
        mock_psutil.disk_usage.side_effect = PermissionError("Access denied")

        result = get_disk_metrics()
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Tests: Network I/O
# ---------------------------------------------------------------------------

class TestGetNetworkIO:
    @patch("src.system_monitor.psutil")
    def test_network_io(self, mock_psutil: MagicMock) -> None:
        mock_psutil.net_io_counters.return_value = NetIOCounters(
            bytes_sent=1000,
            bytes_recv=2000,
            packets_sent=10,
            packets_recv=20,
            errin=0,
            errout=1,
            dropin=0,
            dropout=0,
        )

        result = get_network_io()

        assert result["bytes_sent"] == 1000
        assert result["bytes_recv"] == 2000
        assert result["errout"] == 1


class TestGetPerInterfaceIO:
    @patch("src.system_monitor.psutil")
    def test_per_interface(self, mock_psutil: MagicMock) -> None:
        mock_psutil.net_io_counters.return_value = {
            "eth0": NetIOCounters(100, 200, 5, 10, 0, 0, 0, 0),
            "lo": NetIOCounters(50, 50, 3, 3, 0, 0, 0, 0),
        }

        result = get_per_interface_io()

        assert "eth0" in result
        assert "lo" in result
        assert result["eth0"]["bytes_sent"] == 100


# ---------------------------------------------------------------------------
# Tests: Uptime
# ---------------------------------------------------------------------------

class TestGetUptimeInfo:
    @patch("src.system_monitor.time")
    @patch("src.system_monitor.psutil")
    def test_uptime(self, mock_psutil: MagicMock, mock_time: MagicMock) -> None:
        boot_time = 1700000000.0
        current_time = 1700086400.0  # boot + 1 day
        mock_psutil.boot_time.return_value = boot_time
        mock_time.time.return_value = current_time

        result = get_uptime_info()

        assert result["uptime_seconds"] == pytest.approx(86400.0, abs=1.0)
        assert "1d" in result["uptime_human"]
        assert "boot_time_iso" in result


# ---------------------------------------------------------------------------
# Tests: Temperature sensors
# ---------------------------------------------------------------------------

class TestGetTemperatureSensors:
    @patch("src.system_monitor.psutil")
    def test_no_sensor_support(self, mock_psutil: MagicMock) -> None:
        """Simulate a platform without sensors_temperatures."""
        if hasattr(mock_psutil, "sensors_temperatures"):
            del mock_psutil.sensors_temperatures

        result = get_temperature_sensors()
        assert result is None

    @patch("src.system_monitor.psutil")
    def test_with_sensors(self, mock_psutil: MagicMock) -> None:
        SensorEntry = namedtuple("shwtemp", ["label", "current", "high", "critical"])
        mock_psutil.sensors_temperatures.return_value = {
            "coretemp": [
                SensorEntry("Core 0", 55.0, 80.0, 100.0),
                SensorEntry("Core 1", 57.0, 80.0, 100.0),
            ]
        }

        result = get_temperature_sensors()

        assert result is not None
        assert "coretemp" in result
        assert len(result["coretemp"]) == 2
        assert result["coretemp"][0]["current"] == 55.0


# ---------------------------------------------------------------------------
# Tests: Aggregate collection
# ---------------------------------------------------------------------------

class TestCollectAllMetrics:
    @patch("src.system_monitor.get_temperature_sensors", return_value=None)
    @patch("src.system_monitor.get_uptime_info", return_value={"boot_time_iso": "x", "uptime_seconds": 100, "uptime_human": "0d 0h 1m 40s"})
    @patch("src.system_monitor.get_per_interface_io", return_value={})
    @patch("src.system_monitor.get_network_io", return_value={"bytes_sent": 0, "bytes_recv": 0, "packets_sent": 0, "packets_recv": 0, "errin": 0, "errout": 0, "dropin": 0, "dropout": 0})
    @patch("src.system_monitor.get_disk_metrics", return_value=[])
    @patch("src.system_monitor.get_memory_metrics", return_value={"total_bytes": 0, "available_bytes": 0, "used_bytes": 0, "percent": 0.0, "swap": {"total_bytes": 0, "used_bytes": 0, "percent": 0.0}})
    @patch("src.system_monitor.get_cpu_metrics", return_value={"percent_aggregate": 10.0, "percent_per_core": [10.0], "load_avg": None, "core_count_logical": 1, "core_count_physical": 1, "freq_current_mhz": None})
    def test_collect_all_has_expected_keys(self, *mocks: MagicMock) -> None:
        result = collect_all_metrics()

        expected_keys = {
            "timestamp", "platform", "cpu", "memory", "disks",
            "network_io", "per_interface_io", "uptime", "temperatures",
        }
        assert expected_keys.issubset(set(result.keys()))
        assert result["cpu"]["percent_aggregate"] == 10.0
