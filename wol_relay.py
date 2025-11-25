import json
import logging
import os
import shlex
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

# Server configuration (override via environment variables if needed)
PORT = int(os.environ.get("WOL_RELAY_PORT", "5000"))
BIND_ADDRESS = os.environ.get("WOL_RELAY_BIND", "0.0.0.0")
BROADCAST_IP = os.environ.get("WOL_BROADCAST_IP", "<broadcast>")
BROADCAST_PORT = int(os.environ.get("WOL_BROADCAST_PORT", "9"))
SSH_BIN = os.environ.get("WOL_SSH_BIN", "ssh")
SSH_EXTRA_ARGS = os.environ.get("WOL_SSH_EXTRA_ARGS", "")
DEFAULT_SLEEP_CMD_LINUX = os.environ.get("WOL_SLEEP_CMD_LINUX", "systemctl suspend")
DEFAULT_SLEEP_CMD_WINDOWS = os.environ.get(
    "WOL_SLEEP_CMD_WINDOWS",
    "powershell.exe -Command \"Start-Sleep -Seconds 1; Add-Type -AssemblyName System.Windows.Forms; "
    "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)\"",
)
DEFAULT_SLEEP_CMD_MACOS = os.environ.get("WOL_SLEEP_CMD_MACOS", "pmset sleepnow")
LOG_LEVEL = os.environ.get("WOL_LOG_LEVEL", "INFO").upper()
LOG_FILE = os.environ.get("WOL_LOG_FILE", "logs/wol_relay.log")
LOG_MAX_BYTES = int(os.environ.get("WOL_LOG_MAX_BYTES", str(1_000_000)))
LOG_BACKUP_COUNT = int(os.environ.get("WOL_LOG_BACKUP_COUNT", "5"))


def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("wol_relay")
    if logger.handlers:
        return logger

    logger.setLevel(LOG_LEVEL)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    log_file = LOG_FILE.strip()
    if log_file:
        log_path = Path(log_file)
        if log_path.parent:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


LOGGER = _setup_logger()


def create_magic_packet(mac_address: str) -> bytes:
    """Create a Wake-on-LAN magic packet payload."""
    mac_clean = mac_address.replace(":", "").replace("-", "").lower()
    if len(mac_clean) != 12:
        raise ValueError("Invalid MAC address format")

    return b"\xFF" * 6 + bytes.fromhex(mac_clean) * 16


def send_magic_packet(
    mac_address: str,
    broadcast_ip: str = BROADCAST_IP,
    broadcast_port: int = BROADCAST_PORT,
) -> None:
    """Broadcast the magic packet to the configured network."""
    packet = create_magic_packet(mac_address)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast_ip, broadcast_port))
    LOGGER.info("Sent magic packet to %s via %s:%s", mac_address, broadcast_ip, broadcast_port)


def trigger_sleep(
    host: str,
    *,
    os_type: Optional[str] = None,
    custom_command: Optional[str] = None,
) -> None:
    """Send an SSH command that puts the remote host to sleep."""
    if custom_command:
        command = custom_command
    else:
        normalized = (os_type or "").lower()
        if normalized in ("linux", "unix"):
            command = DEFAULT_SLEEP_CMD_LINUX
        elif normalized in ("windows", "win"):
            command = DEFAULT_SLEEP_CMD_WINDOWS
        elif normalized in ("macos", "mac", "darwin"):
            command = DEFAULT_SLEEP_CMD_MACOS
        else:
            raise ValueError("Unknown OS type and no custom command provided")

    ssh_parts = [SSH_BIN]
    if SSH_EXTRA_ARGS.strip():
        ssh_parts.extend(shlex.split(SSH_EXTRA_ARGS))
    ssh_parts.append(host)
    ssh_parts.append(command)

    LOGGER.info("Executing sleep command on %s: %s", host, command)
    subprocess.run(ssh_parts, check=True)
    LOGGER.info("Succeeded sleeping host %s", host)


def ping_host(host: str) -> bool:
    """Ping a host and return True if online."""
    param = "-n" if os.name == "nt" else "-c"
    timeout_param = "-w" if os.name == "nt" else "-W"
    # Timeout 1000ms (1s)
    command = ["ping", param, "1", timeout_param, "1000", host]

    try:
        subprocess.check_output(command, stderr=subprocess.STDOUT)
        return True
    except subprocess.CalledProcessError:
        return False
    except Exception:
        return False


def check_tcp_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Try connecting to a TCP port to determine availability."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "WOLRelay/0.1"

    def _send_json(self, status_code: int, body: Dict[str, Any]) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self) -> None:
        if self.path in {"/health", "/healthz"}:
            # Allow both /health and /healthz for compatibility with external monitors
            self._send_json(200, {"status": "ok"})
        elif self.path.startswith("/api/status"):
            self._handle_status()
        else:
            self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        try:
            data = json.loads(post_data)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON body"})
            return

        try:
            if self.path == "/api/control":
                self._handle_control(data)
            elif self.path == "/wake":
                self._handle_wake(data)
            elif self.path == "/sleep":
                self._handle_sleep(data)
            else:
                self.send_error(404, "Not Found")
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except subprocess.CalledProcessError as exc:
            LOGGER.exception("Sleep command failed")
            self._send_json(502, {
                "error": "Sleep command failed",
                "details": str(exc),
                "returncode": exc.returncode,
                "command": " ".join(exc.cmd) if exc.cmd else None
            })
        except Exception as exc:
            LOGGER.exception("Unhandled error while processing %s", self.path)
            self._send_json(500, {"error": str(exc)})

    def _handle_control(self, data: Dict[str, Any]) -> None:
        action = (data.get("action") or "").lower()
        if action not in {"wake", "sleep"}:
            raise ValueError("Unsupported 'action' value. Use 'wake' or 'sleep'.")

        if action == "wake":
            mac_address = data.get("mac_address") or data.get("mac")
            if not mac_address:
                raise ValueError("Missing 'mac_address' parameter for wake action")
            send_magic_packet(mac_address)
            self._send_json(200, {"status": "success", "action": "wake"})
            return

        # action == "sleep"
        host = (
            data.get("host")
            or data.get("ip_address")
            or data.get("ip")
        )
        if not host:
            raise ValueError("Missing 'ip_address' or 'host' parameter for sleep action")

        command = data.get("command")
        os_type = data.get("os")
        LOGGER.info("Control sleep request: host=%s, os=%s, command=%s", host, os_type, command)
        trigger_sleep(host, os_type=os_type, custom_command=command)
        self._send_json(200, {"status": "success", "action": "sleep"})

    def _handle_wake(self, data: Dict[str, Any]) -> None:
        mac_address = data.get("mac") or data.get("mac_address")
        if not mac_address:
            raise ValueError("Missing 'mac' parameter")

        send_magic_packet(mac_address)
        self._send_json(200, {"status": "success"})

    def _handle_sleep(self, data: Dict[str, Any]) -> None:
        host = data.get("host") or data.get("ip_address") or data.get("ip")
        if not host:
            raise ValueError("Missing 'host' or 'ip_address' parameter")

        command = data.get("command")
        os_type = data.get("os")
        LOGGER.info("Sleep request: host=%s, os=%s, command=%s", host, os_type, command)
        trigger_sleep(host, os_type=os_type, custom_command=command)
        self._send_json(200, {"status": "success"})

    def _handle_status(self) -> None:
        from urllib.parse import urlparse, parse_qs

        query = parse_qs(urlparse(self.path).query)
        ip = query.get("ip", [None])[0]

        if not ip:
            self._send_json(400, {"error": "IP address required"})
            return

        port_raw = query.get("port", [None])[0]
        if port_raw in (None, "", []):
            port = 22
        else:
            try:
                port = int(port_raw)
            except ValueError:
                self._send_json(400, {"error": "Invalid port value"})
                return

        ping_ok = ping_host(ip)
        if not ping_ok:
            status = "offline"
        else:
            tcp_ok = check_tcp_port(ip, port)
            status = "online" if tcp_ok else "sleeping"

        self._send_json(
            200,
            {
                "ip": ip,
                "port": port,
                "status": status,
                "ping": ping_ok,
                "tcp": ping_ok and (status == "online"),
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("[%s] %s %s", self.log_date_time_string(), self.address_string(), format % args)


def run(
    server_class=HTTPServer,
    handler_class=RequestHandler,
    port: int = PORT,
    bind_address: str = BIND_ADDRESS,
) -> None:
    server_address = (bind_address, port)
    httpd = server_class(server_address, handler_class)
    LOGGER.info("Starting WoL Relay Server on %s:%s", bind_address, port)
    httpd.serve_forever()


def main() -> None:
    run()


if __name__ == "__main__":
    main()
