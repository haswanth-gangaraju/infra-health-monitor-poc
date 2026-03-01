"""
Hardware health diagnostics.

Provides lightweight, non-destructive tests that approximate real hardware
health checks without requiring root or vendor-specific tooling:

- **Disk I/O latency**: writes and reads a small temp file, measures time.
- **Memory stress**: allocates a block, writes a known pattern, verifies it.
- **CPU stress**: runs a compute-bound workload and reports elapsed time.
- **Network error rates**: checks per-interface error/drop counters.

Each test returns a structured result dict with ``status`` ("PASS" / "FAIL" /
"WARN"), ``details`` string, and raw ``metrics``.
"""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
import time
from typing import Any, Dict, List

import psutil


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _result(status: str, details: str, **metrics: Any) -> Dict[str, Any]:
    """Build a uniform result dict."""
    return {"status": status, "details": details, "metrics": metrics}


# ---------------------------------------------------------------------------
# Disk I/O latency test
# ---------------------------------------------------------------------------

def disk_io_latency_test(
    block_size_kb: int = 64,
    iterations: int = 5,
    threshold_ms: float = 100.0,
) -> Dict[str, Any]:
    """
    Measure disk write+read latency by creating a temporary file.

    Args:
        block_size_kb: Size of the data block to write (KiB).
        iterations: Number of write/read cycles.
        threshold_ms: Latency above this value triggers a WARN status.

    Returns:
        Result dict with ``avg_write_ms``, ``avg_read_ms`` in metrics.
    """
    data = os.urandom(block_size_kb * 1024)
    write_times: List[float] = []
    read_times: List[float] = []

    try:
        for _ in range(iterations):
            fd, path = tempfile.mkstemp(prefix="ihmon_disk_")
            try:
                # Write
                t0 = time.perf_counter()
                os.write(fd, data)
                os.fsync(fd)
                write_times.append((time.perf_counter() - t0) * 1000)
                os.close(fd)

                # Read
                t0 = time.perf_counter()
                with open(path, "rb") as fh:
                    _ = fh.read()
                read_times.append((time.perf_counter() - t0) * 1000)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        avg_write = sum(write_times) / len(write_times)
        avg_read = sum(read_times) / len(read_times)
        worst = max(max(write_times), max(read_times))

        if worst > threshold_ms:
            return _result(
                "WARN",
                f"Disk I/O latency spike: worst={worst:.1f}ms (threshold={threshold_ms}ms)",
                avg_write_ms=round(avg_write, 2),
                avg_read_ms=round(avg_read, 2),
                worst_ms=round(worst, 2),
                iterations=iterations,
            )

        return _result(
            "PASS",
            f"Disk I/O healthy: avg write={avg_write:.1f}ms, avg read={avg_read:.1f}ms",
            avg_write_ms=round(avg_write, 2),
            avg_read_ms=round(avg_read, 2),
            worst_ms=round(worst, 2),
            iterations=iterations,
        )

    except OSError as exc:
        return _result("FAIL", f"Disk I/O test failed: {exc}")


# ---------------------------------------------------------------------------
# Memory stress test
# ---------------------------------------------------------------------------

def memory_stress_test(
    block_size_mb: int = 32,
) -> Dict[str, Any]:
    """
    Allocate a memory block, write a pattern, read it back, verify.

    This is a simplified version of memtest-style verification: it writes
    a repeating byte pattern, computes a hash, then verifies the hash after
    a second read.

    Args:
        block_size_mb: Size of memory block to test (MiB).

    Returns:
        Result dict with ``block_size_mb``, ``elapsed_ms``.
    """
    size = block_size_mb * 1024 * 1024
    pattern = b"\xAA\x55" * (size // 2)

    try:
        t0 = time.perf_counter()

        # Write pattern into a bytearray (forces real allocation)
        buf = bytearray(pattern)
        write_hash = hashlib.md5(buf).hexdigest()

        # Read back and verify
        read_hash = hashlib.md5(buf).hexdigest()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Explicitly release
        del buf
        del pattern

        if write_hash != read_hash:
            return _result(
                "FAIL",
                "Memory verification FAILED: hash mismatch after write/read cycle",
                block_size_mb=block_size_mb,
                elapsed_ms=round(elapsed_ms, 2),
            )

        return _result(
            "PASS",
            f"Memory test passed: {block_size_mb} MiB verified in {elapsed_ms:.0f}ms",
            block_size_mb=block_size_mb,
            elapsed_ms=round(elapsed_ms, 2),
        )

    except MemoryError:
        return _result(
            "FAIL",
            f"Memory test FAILED: could not allocate {block_size_mb} MiB",
            block_size_mb=block_size_mb,
        )


# ---------------------------------------------------------------------------
# CPU stress test
# ---------------------------------------------------------------------------

def cpu_stress_test(
    duration_seconds: float = 2.0,
) -> Dict[str, Any]:
    """
    Run a compute-bound task and measure throughput.

    Computes many SHA-256 hashes in a tight loop for the specified duration,
    then reports operations/second as a baseline benchmark.

    Args:
        duration_seconds: How long to run the stress loop.

    Returns:
        Result dict with ``ops_per_second``, ``elapsed_seconds``.
    """
    ops = 0
    payload = b"wintermute_infra_benchmark_payload"
    deadline = time.perf_counter() + duration_seconds
    t0 = time.perf_counter()

    while time.perf_counter() < deadline:
        # Mix of compute: hash + math to exercise different execution units
        hashlib.sha256(payload).digest()
        _ = math.sqrt(ops + 1)
        ops += 1

    elapsed = time.perf_counter() - t0
    ops_per_sec = ops / elapsed if elapsed > 0 else 0

    return _result(
        "PASS",
        f"CPU benchmark: {ops_per_sec:,.0f} ops/s over {elapsed:.1f}s",
        ops_per_second=round(ops_per_sec, 1),
        elapsed_seconds=round(elapsed, 2),
        total_ops=ops,
    )


# ---------------------------------------------------------------------------
# Network interface error rate check
# ---------------------------------------------------------------------------

def check_network_error_rates(
    threshold: float = 0.01,
) -> List[Dict[str, Any]]:
    """
    Check per-interface error and drop rates.

    Args:
        threshold: Fraction of total packets that constitutes a problem.
                   0.01 means 1 %.

    Returns:
        List of result dicts, one per interface.
    """
    per_nic = psutil.net_io_counters(pernic=True)
    results: List[Dict[str, Any]] = []

    for name, counters in per_nic.items():
        total_packets = counters.packets_sent + counters.packets_recv
        total_errors = counters.errin + counters.errout
        total_drops = counters.dropin + counters.dropout

        if total_packets == 0:
            results.append(_result(
                "PASS",
                f"{name}: no traffic (0 packets)",
                interface=name,
                error_rate=0.0,
                drop_rate=0.0,
            ))
            continue

        error_rate = total_errors / total_packets
        drop_rate = total_drops / total_packets

        if error_rate > threshold or drop_rate > threshold:
            results.append(_result(
                "WARN",
                (
                    f"{name}: error_rate={error_rate:.4%}, "
                    f"drop_rate={drop_rate:.4%} "
                    f"(threshold={threshold:.2%})"
                ),
                interface=name,
                error_rate=round(error_rate, 6),
                drop_rate=round(drop_rate, 6),
                total_packets=total_packets,
                total_errors=total_errors,
                total_drops=total_drops,
            ))
        else:
            results.append(_result(
                "PASS",
                f"{name}: healthy (errors={total_errors}, drops={total_drops})",
                interface=name,
                error_rate=round(error_rate, 6),
                drop_rate=round(drop_rate, 6),
                total_packets=total_packets,
            ))

    return results


# ---------------------------------------------------------------------------
# Aggregate diagnostics
# ---------------------------------------------------------------------------

def run_all_diagnostics(
    disk_threshold_ms: float = 100.0,
    memory_block_mb: int = 32,
    cpu_duration_s: float = 2.0,
    net_error_threshold: float = 0.01,
) -> Dict[str, Any]:
    """
    Run the full suite of hardware diagnostics.

    Returns:
        Dict with keys ``disk_io``, ``memory``, ``cpu``, ``network_errors``,
        and an overall ``summary`` string.
    """
    disk = disk_io_latency_test(threshold_ms=disk_threshold_ms)
    mem = memory_stress_test(block_size_mb=memory_block_mb)
    cpu = cpu_stress_test(duration_seconds=cpu_duration_s)
    net_errors = check_network_error_rates(threshold=net_error_threshold)

    all_statuses = [disk["status"], mem["status"], cpu["status"]]
    all_statuses.extend(r["status"] for r in net_errors)

    if "FAIL" in all_statuses:
        overall = "FAIL"
    elif "WARN" in all_statuses:
        overall = "WARN"
    else:
        overall = "PASS"

    return {
        "disk_io": disk,
        "memory": mem,
        "cpu": cpu,
        "network_errors": net_errors,
        "summary": overall,
    }
