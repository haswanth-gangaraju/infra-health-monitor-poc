"""
Terminal dashboard using ``rich``.

Renders a live, auto-refreshing terminal UI that displays:
- System metrics (CPU, memory, disk)
- Network status summary
- Active alerts with colour coding
- Hardware health indicators

The dashboard is built with ``rich.live.Live`` and ``rich.table.Table``
for a clean, readable layout that works over SSH and in standard terminals.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.alert_engine import Alert, AlertEngine, Severity
from src.config import AppConfig
from src.network_health import run_all_network_checks
from src.system_monitor import collect_all_metrics


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _bytes_human(b: int) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024  # type: ignore[assignment]
    return f"{b:.1f} PB"


def _pct_colour(value: float, warn: float = 70.0, crit: float = 90.0) -> str:
    """Return a rich colour name based on percentage thresholds."""
    if value >= crit:
        return "bold red"
    elif value >= warn:
        return "yellow"
    return "green"


def _status_colour(status: str) -> str:
    """Map a status string to a rich colour."""
    mapping = {
        "PASS": "green",
        "HEALTHY": "green",
        "WARN": "yellow",
        "WARNING": "yellow",
        "FAIL": "bold red",
        "DEGRADED": "bold red",
        "CRITICAL": "bold red",
    }
    return mapping.get(status.upper(), "white")


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------

def build_system_panel(metrics: Dict[str, Any]) -> Panel:
    """Build the System Metrics panel."""
    cpu = metrics.get("cpu", {})
    mem = metrics.get("memory", {})
    uptime = metrics.get("uptime", {})

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    # CPU
    cpu_pct = cpu.get("percent_aggregate", 0.0)
    table.add_row(
        "CPU Usage",
        Text(f"{cpu_pct:.1f}%", style=_pct_colour(cpu_pct)),
    )

    cores = cpu.get("percent_per_core", [])
    if cores:
        core_str = "  ".join(f"C{i}:{v:.0f}%" for i, v in enumerate(cores))
        table.add_row("Per-Core", Text(core_str, style="dim"))

    load = cpu.get("load_avg")
    if load:
        table.add_row(
            "Load Avg",
            f"{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}",
        )

    freq = cpu.get("freq_current_mhz")
    if freq:
        table.add_row("CPU Freq", f"{freq:.0f} MHz")

    # Memory
    mem_pct = mem.get("percent", 0.0)
    total = mem.get("total_bytes", 0)
    used = mem.get("used_bytes", 0)
    table.add_row(
        "Memory",
        Text(
            f"{mem_pct:.1f}%  ({_bytes_human(used)} / {_bytes_human(total)})",
            style=_pct_colour(mem_pct),
        ),
    )

    swap = mem.get("swap", {})
    swap_pct = swap.get("percent", 0.0)
    table.add_row("Swap", Text(f"{swap_pct:.1f}%", style=_pct_colour(swap_pct, 50, 80)))

    # Uptime
    table.add_row("Uptime", uptime.get("uptime_human", "N/A"))

    return Panel(table, title="[bold cyan]System Metrics[/bold cyan]", border_style="cyan")


def build_disk_panel(metrics: Dict[str, Any]) -> Panel:
    """Build the Disk Usage panel."""
    disks = metrics.get("disks", [])

    table = Table(box=None, padding=(0, 1))
    table.add_column("Mount", style="bold")
    table.add_column("Used%", justify="right")
    table.add_column("Used", justify="right")
    table.add_column("Free", justify="right")
    table.add_column("Total", justify="right")

    for disk in disks:
        pct = disk.get("percent", 0.0)
        table.add_row(
            disk.get("mountpoint", "?"),
            Text(f"{pct:.1f}%", style=_pct_colour(pct)),
            f"{disk.get('used_gb', 0):.1f} GB",
            f"{disk.get('free_gb', 0):.1f} GB",
            f"{disk.get('total_gb', 0):.1f} GB",
        )

    return Panel(table, title="[bold cyan]Disk Usage[/bold cyan]", border_style="cyan")


def build_network_panel(
    metrics: Dict[str, Any],
    net_results: Optional[Dict[str, Any]] = None,
) -> Panel:
    """Build the Network Status panel."""
    net_io = metrics.get("network_io", {})

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    table.add_row("Bytes Sent", _bytes_human(net_io.get("bytes_sent", 0)))
    table.add_row("Bytes Recv", _bytes_human(net_io.get("bytes_recv", 0)))
    table.add_row("Packets Sent", f"{net_io.get('packets_sent', 0):,}")
    table.add_row("Packets Recv", f"{net_io.get('packets_recv', 0):,}")
    table.add_row(
        "Errors",
        f"In: {net_io.get('errin', 0)}  Out: {net_io.get('errout', 0)}",
    )
    table.add_row(
        "Drops",
        f"In: {net_io.get('dropin', 0)}  Out: {net_io.get('dropout', 0)}",
    )

    if net_results:
        summary = net_results.get("summary", "UNKNOWN")
        table.add_row(
            "Network Health",
            Text(summary, style=_status_colour(summary)),
        )

        # DNS summary
        dns_results = net_results.get("dns", [])
        if dns_results:
            dns_ok = sum(1 for d in dns_results if d["status"] == "PASS")
            table.add_row("DNS Checks", f"{dns_ok}/{len(dns_results)} passed")

        # TCP summary
        tcp_results = net_results.get("tcp", [])
        if tcp_results:
            tcp_ok = sum(1 for t in tcp_results if t["status"] == "PASS")
            table.add_row("TCP Checks", f"{tcp_ok}/{len(tcp_results)} connected")

    return Panel(table, title="[bold cyan]Network Status[/bold cyan]", border_style="cyan")


def build_alert_panel(alerts: List[Alert]) -> Panel:
    """Build the Alerts panel showing recent alerts."""
    if not alerts:
        content = Text("  No active alerts", style="green")
        return Panel(content, title="[bold cyan]Alerts[/bold cyan]", border_style="cyan")

    table = Table(box=None, padding=(0, 1))
    table.add_column("Severity", justify="center")
    table.add_column("Source")
    table.add_column("Message")
    table.add_column("Time", style="dim")

    for alert in reversed(alerts[-10:]):
        sev_colour = {
            Severity.INFO: "cyan",
            Severity.WARNING: "yellow",
            Severity.CRITICAL: "bold red",
        }.get(alert.severity, "white")

        # Extract just the time portion
        ts = alert.timestamp
        if "T" in ts:
            ts = ts.split("T")[1][:8]

        table.add_row(
            Text(alert.severity.value, style=sev_colour),
            alert.source,
            alert.message,
            ts,
        )

    return Panel(table, title="[bold cyan]Alerts[/bold cyan]", border_style="cyan")


# ---------------------------------------------------------------------------
# Dashboard composition
# ---------------------------------------------------------------------------

def build_dashboard(
    metrics: Dict[str, Any],
    net_results: Optional[Dict[str, Any]],
    alerts: List[Alert],
) -> Layout:
    """Compose all panels into a full-screen layout."""
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=1),
    )

    # Header
    header_text = Text(
        "  Infrastructure Health Monitor  ",
        style="bold white on blue",
        justify="center",
    )
    layout["header"].update(Panel(header_text, style="blue"))

    # Body: split into left (system + disk) and right (network + alerts)
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )

    layout["left"].split_column(
        Layout(build_system_panel(metrics), name="system"),
        Layout(build_disk_panel(metrics), name="disk"),
    )

    layout["right"].split_column(
        Layout(build_network_panel(metrics, net_results), name="network"),
        Layout(build_alert_panel(alerts), name="alerts"),
    )

    # Footer
    ts = metrics.get("timestamp", "")
    footer = Text(
        f"  Last updated: {ts}  |  Press Ctrl+C to exit",
        style="dim",
    )
    layout["footer"].update(footer)

    return layout


# ---------------------------------------------------------------------------
# Main dashboard loop
# ---------------------------------------------------------------------------

def run_dashboard(config: AppConfig) -> None:
    """
    Launch the live terminal dashboard.

    Refreshes metrics and network checks every ``config.dashboard_refresh_seconds``
    seconds. Press Ctrl+C to exit.

    Args:
        config: Application configuration.
    """
    console = Console()
    engine = AlertEngine(config.thresholds, dedup_window_s=30.0)

    # Prepare TCP targets from config
    tcp_targets = [
        {"host": h.host, "port": h.port, "label": h.label}
        for h in config.monitored_hosts
    ]

    console.print("[bold blue]Starting Infrastructure Health Monitor Dashboard...[/bold blue]")
    console.print(f"[dim]Refresh interval: {config.dashboard_refresh_seconds}s  |  Press Ctrl+C to stop[/dim]\n")

    try:
        with Live(console=console, refresh_per_second=1, screen=True) as live:
            while True:
                # Collect data
                metrics = collect_all_metrics()
                net_results = run_all_network_checks(
                    dns_domains=config.dns_check_domains,
                    tcp_targets=tcp_targets,
                    ping_targets=config.ping_targets,
                    dns_timeout_ms=config.thresholds.dns_timeout_ms,
                    tcp_timeout_s=config.thresholds.tcp_connect_timeout_s,
                )

                # Evaluate alerts
                sys_alerts = engine.evaluate(metrics)
                net_alerts = engine.evaluate_network(net_results)
                all_recent = engine.get_recent_alerts(count=15)

                # Render
                layout = build_dashboard(metrics, net_results, all_recent)
                live.update(layout)

                time.sleep(config.dashboard_refresh_seconds)

    except KeyboardInterrupt:
        console.print("\n[bold blue]Dashboard stopped.[/bold blue]")
