#!/usr/bin/env python3
"""
Infrastructure Health Monitor -- CLI entry point.

Modes:
    dashboard   Launch a live terminal dashboard (auto-refreshing).
    check       Run a one-time health check and print results.
    report      Generate a comprehensive health report.

Usage:
    python main.py --mode dashboard
    python main.py --mode check
    python main.py --mode report
    python main.py --mode check --config /path/to/config.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.alert_engine import AlertEngine
from src.config import AppConfig, load_config
from src.dashboard import run_dashboard
from src.hardware_diagnostics import run_all_diagnostics
from src.network_health import run_all_network_checks
from src.system_monitor import collect_all_metrics


console = Console()


# ---------------------------------------------------------------------------
# Helper formatters
# ---------------------------------------------------------------------------

def _bytes_human(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024  # type: ignore[assignment]
    return f"{b:.1f} PB"


def _status_style(status: str) -> str:
    return {
        "PASS": "green",
        "HEALTHY": "green",
        "WARN": "yellow",
        "WARNING": "yellow",
        "FAIL": "bold red",
        "DEGRADED": "bold red",
    }.get(status.upper(), "white")


# ---------------------------------------------------------------------------
# One-time health check
# ---------------------------------------------------------------------------

def run_check(config: AppConfig) -> None:
    """Run a single pass of all health checks and print results."""
    console.print(Panel(
        "[bold]Infrastructure Health Check[/bold]",
        style="blue",
    ))

    # 1. System metrics
    console.print("\n[bold cyan]== System Metrics ==[/bold cyan]")
    metrics = collect_all_metrics()
    cpu = metrics["cpu"]
    mem = metrics["memory"]

    console.print(f"  CPU Usage:    {cpu['percent_aggregate']:.1f}%")
    console.print(f"  Cores:        {cpu['core_count_physical']} physical / {cpu['core_count_logical']} logical")
    if cpu.get("load_avg"):
        la = cpu["load_avg"]
        console.print(f"  Load Avg:     {la[0]:.2f} / {la[1]:.2f} / {la[2]:.2f}")
    console.print(f"  Memory:       {mem['percent']:.1f}%  ({_bytes_human(mem['used_bytes'])} / {_bytes_human(mem['total_bytes'])})")
    console.print(f"  Uptime:       {metrics['uptime']['uptime_human']}")

    # Disks
    console.print("\n[bold cyan]== Disk Usage ==[/bold cyan]")
    disk_table = Table(box=None, padding=(0, 1))
    disk_table.add_column("Mount")
    disk_table.add_column("Used%", justify="right")
    disk_table.add_column("Free", justify="right")
    disk_table.add_column("Total", justify="right")

    for d in metrics["disks"]:
        pct = d["percent"]
        style = "green" if pct < 80 else ("yellow" if pct < 90 else "red")
        disk_table.add_row(
            d["mountpoint"],
            Text(f"{pct:.1f}%", style=style),
            f"{d['free_gb']:.1f} GB",
            f"{d['total_gb']:.1f} GB",
        )
    console.print(disk_table)

    # 2. Network health
    console.print("\n[bold cyan]== Network Health ==[/bold cyan]")
    tcp_targets = [
        {"host": h.host, "port": h.port, "label": h.label}
        for h in config.monitored_hosts
    ]
    net = run_all_network_checks(
        dns_domains=config.dns_check_domains,
        tcp_targets=tcp_targets,
        ping_targets=config.ping_targets,
        dns_timeout_ms=config.thresholds.dns_timeout_ms,
        tcp_timeout_s=config.thresholds.tcp_connect_timeout_s,
    )

    console.print(f"  Overall: ", Text(net["summary"], style=_status_style(net["summary"])))

    for dns in net["dns"]:
        s = Text(f"[{dns['status']}]", style=_status_style(dns["status"]))
        console.print("  DNS  ", s, f" {dns['details']}")

    for tcp in net["tcp"]:
        s = Text(f"[{tcp['status']}]", style=_status_style(tcp["status"]))
        console.print("  TCP  ", s, f" {tcp['details']}")

    for p in net["ping"]:
        s = Text(f"[{p['status']}]", style=_status_style(p["status"]))
        console.print("  PING ", s, f" {p['details']}")

    # 3. Alerts
    console.print("\n[bold cyan]== Alert Evaluation ==[/bold cyan]")
    engine = AlertEngine(config.thresholds)
    sys_alerts = engine.evaluate(metrics)
    net_alerts = engine.evaluate_network(net)

    all_alerts = sys_alerts + net_alerts
    if all_alerts:
        engine.print_alerts(all_alerts)
    else:
        console.print("  [green]No alerts triggered.[/green]")

    console.print()


# ---------------------------------------------------------------------------
# Comprehensive report
# ---------------------------------------------------------------------------

def run_report(config: AppConfig) -> None:
    """Generate a comprehensive health report."""
    console.print(Panel(
        "[bold]Infrastructure Health Report[/bold]\n"
        f"Generated: {datetime.now(tz=timezone.utc).isoformat()}",
        style="blue",
    ))

    # System metrics
    console.print("\n[bold cyan]1. SYSTEM METRICS[/bold cyan]")
    console.print("   " + "-" * 50)
    metrics = collect_all_metrics()

    cpu = metrics["cpu"]
    mem = metrics["memory"]
    console.print(f"   Platform:     {metrics['platform']['system']} {metrics['platform']['release']}")
    console.print(f"   Machine:      {metrics['platform']['machine']}")
    console.print(f"   Python:       {metrics['platform']['python_version']}")
    console.print(f"   CPU Usage:    {cpu['percent_aggregate']:.1f}%")
    console.print(f"   CPU Cores:    {cpu['core_count_physical']} physical / {cpu['core_count_logical']} logical")

    if cpu.get("freq_current_mhz"):
        console.print(f"   CPU Freq:     {cpu['freq_current_mhz']:.0f} MHz")
    if cpu.get("load_avg"):
        la = cpu["load_avg"]
        console.print(f"   Load Avg:     {la[0]:.2f} / {la[1]:.2f} / {la[2]:.2f}")

    console.print(f"   Memory:       {mem['percent']:.1f}% ({_bytes_human(mem['used_bytes'])} / {_bytes_human(mem['total_bytes'])})")
    console.print(f"   Swap:         {mem['swap']['percent']:.1f}%")
    console.print(f"   Uptime:       {metrics['uptime']['uptime_human']}")

    # Disk usage
    console.print(f"\n[bold cyan]2. DISK USAGE[/bold cyan]")
    console.print("   " + "-" * 50)
    for d in metrics["disks"]:
        pct = d["percent"]
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "#" * filled + "-" * (bar_len - filled)
        console.print(f"   {d['mountpoint']:<20s} [{bar}] {pct:.1f}%  ({d['free_gb']:.1f} GB free)")

    # Network I/O
    console.print(f"\n[bold cyan]3. NETWORK I/O[/bold cyan]")
    console.print("   " + "-" * 50)
    nio = metrics["network_io"]
    console.print(f"   Bytes Sent:     {_bytes_human(nio['bytes_sent'])}")
    console.print(f"   Bytes Received: {_bytes_human(nio['bytes_recv'])}")
    console.print(f"   Packets Sent:   {nio['packets_sent']:,}")
    console.print(f"   Packets Recv:   {nio['packets_recv']:,}")
    console.print(f"   Errors:         In={nio['errin']}  Out={nio['errout']}")
    console.print(f"   Drops:          In={nio['dropin']}  Out={nio['dropout']}")

    # Hardware diagnostics
    console.print(f"\n[bold cyan]4. HARDWARE DIAGNOSTICS[/bold cyan]")
    console.print("   " + "-" * 50)
    console.print("   Running diagnostics (this takes a few seconds)...")

    diag = run_all_diagnostics(
        disk_threshold_ms=config.thresholds.disk_latency_ms,
        net_error_threshold=config.thresholds.network_error_rate,
    )

    for key in ("disk_io", "memory", "cpu"):
        result = diag[key]
        s = Text(f"[{result['status']}]", style=_status_style(result["status"]))
        console.print("   ", s, f" {result['details']}")

    for nic in diag["network_errors"]:
        s = Text(f"[{nic['status']}]", style=_status_style(nic["status"]))
        console.print("   ", s, f" {nic['details']}")

    console.print(f"\n   Overall hardware: ", Text(diag["summary"], style=_status_style(diag["summary"])))

    # Network health
    console.print(f"\n[bold cyan]5. NETWORK HEALTH[/bold cyan]")
    console.print("   " + "-" * 50)

    tcp_targets = [
        {"host": h.host, "port": h.port, "label": h.label}
        for h in config.monitored_hosts
    ]
    net = run_all_network_checks(
        dns_domains=config.dns_check_domains,
        tcp_targets=tcp_targets,
        ping_targets=config.ping_targets,
    )

    for dns_r in net["dns"]:
        s = Text(f"[{dns_r['status']}]", style=_status_style(dns_r["status"]))
        console.print("   DNS  ", s, f" {dns_r['details']}")

    for tcp_r in net["tcp"]:
        s = Text(f"[{tcp_r['status']}]", style=_status_style(tcp_r["status"]))
        console.print("   TCP  ", s, f" {tcp_r['details']}")

    for ping_r in net["ping"]:
        s = Text(f"[{ping_r['status']}]", style=_status_style(ping_r["status"]))
        console.print("   PING ", s, f" {ping_r['details']}")

    dhcp = net["dhcp"]
    console.print(f"   DHCP: {dhcp['details']}")

    console.print(f"\n   Network overall: ", Text(net["summary"], style=_status_style(net["summary"])))

    # Alert evaluation
    console.print(f"\n[bold cyan]6. ALERT SUMMARY[/bold cyan]")
    console.print("   " + "-" * 50)

    engine = AlertEngine(config.thresholds)
    engine.evaluate(metrics)
    engine.evaluate_network(net)
    engine.evaluate_diagnostics(diag)

    summary = engine.get_alert_summary()
    console.print(f"   INFO:     {summary.get('INFO', 0)}")
    console.print(f"   WARNING:  {summary.get('WARNING', 0)}")
    console.print(f"   CRITICAL: {summary.get('CRITICAL', 0)}")

    recent = engine.get_recent_alerts(count=10)
    if recent:
        console.print("\n   Recent alerts:")
        engine.print_alerts(recent)
    else:
        console.print("   [green]No alerts triggered.[/green]")

    console.print(f"\n{'=' * 60}")
    console.print("[bold blue]Report complete.[/bold blue]\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Infrastructure Health Monitor -- monitor, diagnose, and alert on system health.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py --mode dashboard\n"
            "  python main.py --mode check\n"
            "  python main.py --mode report --config custom.yaml\n"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["dashboard", "check", "report"],
        default="check",
        help="Operating mode (default: check)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to YAML configuration file (default: config.yaml in project root)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    if args.mode == "dashboard":
        run_dashboard(config)
    elif args.mode == "check":
        run_check(config)
    elif args.mode == "report":
        run_report(config)
    else:
        console.print(f"[red]Unknown mode: {args.mode}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
