import json
import os
import shlex
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
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
    print(f"Sent magic packet to {mac_address} via {broadcast_ip}:{broadcast_port}")


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
        else:
            raise ValueError("Unknown OS type and no custom command provided")

    ssh_parts = [SSH_BIN]
    if SSH_EXTRA_ARGS.strip():
        ssh_parts.extend(shlex.split(SSH_EXTRA_ARGS))
    ssh_parts.append(host)
    ssh_parts.append(command)

    print(f"Executing sleep command on {host}: {command}")
    subprocess.run(ssh_parts, check=True)
    print(f"Succeeded sleeping host {host}")


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "WOLRelay/0.1"

    def _send_json(self, status_code: int, body: Dict[str, Any]) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
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
            if self.path == "/wake":
                self._handle_wake(data)
            elif self.path == "/sleep":
                self._handle_sleep(data)
            else:
                self.send_error(404, "Not Found")
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except subprocess.CalledProcessError as exc:
            print(f"Sleep command failed: {exc}")
            self._send_json(502, {"error": "Sleep command failed", "details": str(exc)})
        except Exception as exc:
            print(f"Error: {exc}")
            self._send_json(500, {"error": str(exc)})

    def _handle_wake(self, data: Dict[str, Any]) -> None:
        mac_address = data.get("mac")
        if not mac_address:
            raise ValueError("Missing 'mac' parameter")

        send_magic_packet(mac_address)
        self._send_json(200, {"status": "success"})

    def _handle_sleep(self, data: Dict[str, Any]) -> None:
        host = data.get("host")
        if not host:
            raise ValueError("Missing 'host' parameter")

        command = data.get("command")
        os_type = data.get("os")
        trigger_sleep(host, os_type=os_type, custom_command=command)
        self._send_json(200, {"status": "success"})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {format % args}")


def run(
    server_class=HTTPServer,
    handler_class=RequestHandler,
    port: int = PORT,
    bind_address: str = BIND_ADDRESS,
) -> None:
    server_address = (bind_address, port)
    httpd = server_class(server_address, handler_class)
    print(f"Starting WoL Relay Server on {bind_address}:{port}...")
    httpd.serve_forever()


def main() -> None:
    run()


if __name__ == "__main__":
    main()
