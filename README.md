# Infrastructure Health Monitor

A Python-based infrastructure health monitoring tool designed for real-time system observability, hardware diagnostics, and network health validation. Built to demonstrate core competencies in **Linux system administration**, **hardware monitoring**, **network troubleshooting**, and **Python automation** -- skills essential for infrastructure support in high-frequency trading environments.

---

## Why This Project

In algorithmic trading, infrastructure reliability is non-negotiable. A single disk latency spike, a saturated network interface, or runaway CPU usage can translate directly into missed trades and financial loss. This tool provides:

- **Proactive monitoring** of CPU, memory, disk, and network metrics
- **Hardware diagnostics** including disk I/O latency testing, memory pattern verification, and CPU stress benchmarking
- **Network health validation** covering DNS resolution, TCP connectivity, ping latency, and interface status
- **Threshold-based alerting** with configurable severity levels (INFO, WARNING, CRITICAL)
- **A live terminal dashboard** for at-a-glance infrastructure status

---

## Architecture

```
infra-health-monitor-poc/
|-- main.py                  # CLI entry point (argparse)
|-- config.yaml              # Default configuration
|-- requirements.txt
|-- src/
|   |-- __init__.py
|   |-- config.py            # Configuration management (YAML / defaults)
|   |-- system_monitor.py    # CPU, memory, disk, network I/O, uptime, temps
|   |-- hardware_diagnostics.py  # Disk latency, memory stress, CPU stress
|   |-- network_health.py    # DNS, DHCP, TCP connect, ping, interface status
|   |-- alert_engine.py      # Threshold evaluation, severity levels, history
|   |-- dashboard.py         # Rich-based live terminal dashboard
|-- tests/
|   |-- __init__.py
|   |-- test_system_monitor.py
|   |-- test_network_health.py
|   |-- test_alert_engine.py
```

### Data Flow

```
System / OS APIs
      |
      v
system_monitor.py  ---+
hardware_diagnostics.py --+--> alert_engine.py --> Console Alerts
network_health.py  ---+          |
      |                          v
      +--------------------> dashboard.py (Rich Live Display)
```

---

## Setup

### Prerequisites

- Python 3.9+
- pip

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd infra-health-monitor-poc

# Create a virtual environment (recommended)
python -m venv venv
source venv/bin/activate      # Linux/macOS
venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

| Package      | Purpose                              |
|-------------|--------------------------------------|
| `psutil`    | Cross-platform system metrics        |
| `rich`      | Terminal UI, tables, live dashboard  |
| `ping3`     | ICMP ping with latency measurement   |
| `dnspython` | DNS resolution and query timing      |
| `requests`  | HTTP connectivity checks             |
| `pyyaml`    | Configuration file parsing           |

---

## Usage

### Live Dashboard

```bash
python main.py --mode dashboard
```

Launches a real-time terminal dashboard that auto-refreshes every 5 seconds, showing system metrics, network status, and active alerts.

### One-Time Health Check

```bash
python main.py --mode check
```

Runs a single pass of all health checks and prints results to stdout. Ideal for cron jobs or quick manual inspections.

### Health Report

```bash
python main.py --mode report
```

Generates a comprehensive health report with system metrics, hardware diagnostics, network status, and alert summary.

### Custom Configuration

```bash
python main.py --mode dashboard --config /path/to/custom-config.yaml
```

---

## Configuration

Edit `config.yaml` to customize thresholds, monitored hosts, and check intervals:

```yaml
thresholds:
  cpu_percent: 90.0
  memory_percent: 85.0
  disk_percent: 90.0
  network_error_rate: 0.01

monitored_hosts:
  - host: 8.8.8.8
    port: 53
    label: "Google DNS"
  - host: 1.1.1.1
    port: 53
    label: "Cloudflare DNS"

dns_check_domains:
  - google.com
  - cloudflare.com

check_interval_seconds: 5
```

---

## Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test module
python -m pytest tests/test_system_monitor.py -v

# Run with coverage
python -m pytest tests/ --cov=src --cov-report=term-missing
```

---

## Screenshots

> Terminal dashboard and health check output screenshots can be added here after running the tool.

```
+------------------------------------------------------------+
|              Infrastructure Health Monitor                   |
+------------------------------------------------------------+
| CPU: 23.4%  | Mem: 61.2%  | Disk: 54.8%  | Net: OK        |
+------------------------------------------------------------+
| ALERTS                                                      |
| [WARNING] Memory usage at 85.3% on /dev/sda1               |
| [INFO]    DNS resolution: google.com resolved in 12ms       |
+------------------------------------------------------------+
```

---

## Cross-Platform Support

The tool is designed to run on both **Linux** and **Windows**:

- Linux-specific features (temperature sensors, DHCP lease parsing, `/proc` filesystem) include graceful fallbacks
- Windows users get full CPU, memory, disk, and network monitoring via `psutil`
- Network checks (DNS, TCP, ping) work cross-platform

---

## License

MIT License -- see [LICENSE](LICENSE) for details.
