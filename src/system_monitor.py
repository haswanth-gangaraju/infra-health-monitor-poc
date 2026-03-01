"""
Core system metrics collector.

Uses ``psutil`` to gather CPU, memory, disk, network I/O, uptime, and
temperature data. All public functions return plain dicts so the data
can be serialized, displayed, or fed into the alert engine without
coupling to any particular output format.

Cross-platform: works on Linux, Windows, and macOS. Linux-only features
(e.g. temperature sensors) degrade gracefully on other platforms.
"""

from __future__ import annotations

import platform
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psutil


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

def get_cpu_metrics() -> Dict[str, Any]:
    """
    Collect CPU utilisation metrics.

    Returns:
        Dict containing:
        - ``percent_aggregate``: overall CPU usage (%)
        - ``percent_per_core``: list of per-core usage (%)
        - ``load_avg``: 1/5/15-min load averages (Linux/macOS) or ``None``
        - ``core_count_logical``: number of logical CPUs
        - ``core_count_physical``: number of physical CPUs
        - ``freq_current_mhz``: current clock frequency (MHz) or ``None``
    """
    per_core: List[float] = psutil.cpu_percent(interval=1, percpu=True)
    aggregate: float = psutil.cpu_percent(interval=0)

    # Load averages are not available on Windows
    try:
        load_avg: Optional[tuple] = psutil.getloadavg()
    except (AttributeError, OSError):
        load_avg = None

    freq = psutil.cpu_freq()
    freq_current: Optional[float] = freq.current if freq else None

    return {
        "percent_aggregate": aggregate,
        "percent_per_core": per_core,
        "load_avg": load_avg,
        "core_count_logical": psutil.cpu_count(logical=True),
        "core_count_physical": psutil.cpu_count(logical=False),
        "freq_current_mhz": freq_current,
    }


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

def get_memory_metrics() -> Dict[str, Any]:
    """
    Collect RAM usage metrics.

    Returns:
        Dict with ``total_bytes``, ``available_bytes``, ``used_bytes``,
        ``percent``, and ``swap`` sub-dict.
    """
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()

    return {
        "total_bytes": vm.total,
        "available_bytes": vm.available,
        "used_bytes": vm.used,
        "percent": vm.percent,
        "swap": {
            "total_bytes": sw.total,
            "used_bytes": sw.used,
            "percent": sw.percent,
        },
    }


# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------

def _bytes_to_gb(b: int) -> float:
    """Convert bytes to GiB, rounded to two decimals."""
    return round(b / (1024 ** 3), 2)


def get_disk_metrics() -> List[Dict[str, Any]]:
    """
    Collect disk usage for every mounted partition.

    Filters out pseudo-filesystems (e.g. ``squashfs``, ``tmpfs``) on Linux
    to reduce noise.

    Returns:
        List of dicts, one per partition, with ``device``, ``mountpoint``,
        ``fstype``, ``total_gb``, ``used_gb``, ``free_gb``, ``percent``.
    """
    skip_fstypes = {"squashfs", "tmpfs", "devtmpfs", "overlay"}
    partitions = psutil.disk_partitions(all=False)
    results: List[Dict[str, Any]] = []

    for part in partitions:
        if part.fstype in skip_fstypes:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue

        results.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total_gb": _bytes_to_gb(usage.total),
            "used_gb": _bytes_to_gb(usage.used),
            "free_gb": _bytes_to_gb(usage.free),
            "percent": usage.percent,
        })

    return results


# ---------------------------------------------------------------------------
# Network I/O
# ---------------------------------------------------------------------------

def get_network_io() -> Dict[str, Any]:
    """
    Collect aggregate network I/O counters.

    Returns:
        Dict with ``bytes_sent``, ``bytes_recv``, ``packets_sent``,
        ``packets_recv``, ``errin``, ``errout``, ``dropin``, ``dropout``.
    """
    counters = psutil.net_io_counters()
    return {
        "bytes_sent": counters.bytes_sent,
        "bytes_recv": counters.bytes_recv,
        "packets_sent": counters.packets_sent,
        "packets_recv": counters.packets_recv,
        "errin": counters.errin,
        "errout": counters.errout,
        "dropin": counters.dropin,
        "dropout": counters.dropout,
    }


def get_per_interface_io() -> Dict[str, Dict[str, int]]:
    """
    Collect per-interface network I/O counters.

    Returns:
        Dict keyed by interface name, values are counter dicts.
    """
    per_nic = psutil.net_io_counters(pernic=True)
    result: Dict[str, Dict[str, int]] = {}
    for name, counters in per_nic.items():
        result[name] = {
            "bytes_sent": counters.bytes_sent,
            "bytes_recv": counters.bytes_recv,
            "packets_sent": counters.packets_sent,
            "packets_recv": counters.packets_recv,
            "errin": counters.errin,
            "errout": counters.errout,
            "dropin": counters.dropin,
            "dropout": counters.dropout,
        }
    return result


# ---------------------------------------------------------------------------
# Uptime / boot time
# ---------------------------------------------------------------------------

def get_uptime_info() -> Dict[str, Any]:
    """
    Get system boot time and uptime.

    Returns:
        Dict with ``boot_time_iso``, ``uptime_seconds``, ``uptime_human``.
    """
    boot_ts = psutil.boot_time()
    boot_dt = datetime.fromtimestamp(boot_ts, tz=timezone.utc)
    uptime_s = time.time() - boot_ts

    days, remainder = divmod(int(uptime_s), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    human = f"{days}d {hours}h {minutes}m {seconds}s"

    return {
        "boot_time_iso": boot_dt.isoformat(),
        "uptime_seconds": round(uptime_s, 1),
        "uptime_human": human,
    }


# ---------------------------------------------------------------------------
# Temperature sensors
# ---------------------------------------------------------------------------

def get_temperature_sensors() -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """
    Read hardware temperature sensors.

    Only available on Linux (and some macOS builds). Returns ``None`` when
    sensors are not accessible.

    Returns:
        Dict keyed by sensor group name, values are lists of readings with
        ``label``, ``current``, ``high``, ``critical`` (all in Celsius).
    """
    if not hasattr(psutil, "sensors_temperatures"):
        return None

    try:
        temps = psutil.sensors_temperatures()
    except (AttributeError, RuntimeError, OSError):
        return None

    if not temps:
        return None

    result: Dict[str, List[Dict[str, Any]]] = {}
    for group_name, entries in temps.items():
        readings: List[Dict[str, Any]] = []
        for entry in entries:
            readings.append({
                "label": entry.label or "unlabelled",
                "current": entry.current,
                "high": entry.high,
                "critical": entry.critical,
            })
        result[group_name] = readings

    return result


# ---------------------------------------------------------------------------
# Aggregate snapshot
# ---------------------------------------------------------------------------

def collect_all_metrics() -> Dict[str, Any]:
    """
    Convenience function: collect every category of metrics in one call.

    Returns:
        Dict with keys ``cpu``, ``memory``, ``disks``, ``network_io``,
        ``per_interface_io``, ``uptime``, ``temperatures``, ``platform``,
        ``timestamp``.
    """
    return {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        },
        "cpu": get_cpu_metrics(),
        "memory": get_memory_metrics(),
        "disks": get_disk_metrics(),
        "network_io": get_network_io(),
        "per_interface_io": get_per_interface_io(),
        "uptime": get_uptime_info(),
        "temperatures": get_temperature_sensors(),
    }
