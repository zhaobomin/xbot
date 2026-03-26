"""File share tool for creating temporary download links.

This is a Python skill plugin that provides the file_share tool.
It is automatically loaded by SkillManager without modifying xbot core code.
"""

import asyncio
import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from xbot.agent.tools.base import Tool


class FileShareTool(Tool):
    """Tool to create temporary HTTP download links for files."""

    # Public IP for generating URLs
    DEFAULT_PUBLIC_IP = "121.40.69.126"

    # Port range for servers
    PORT_RANGE = (18000, 18999)

    # Default timeout in seconds
    DEFAULT_TIMEOUT = 600

    # Server script path (relative to this file's directory)
    @property
    def SERVER_SCRIPT(self) -> Path:
        return Path(__file__).parent / "serve.py"

    def __init__(
        self,
        public_ip: str | None = None,
        default_timeout: int | None = None,
    ):
        """Initialize the file share tool.

        Args:
            public_ip: Public IP for URL generation (default: 121.40.69.126)
            default_timeout: Default timeout in seconds (default: 600 = 10 minutes)
        """
        self._public_ip = public_ip or os.environ.get("XBOT_PUBLIC_IP", self.DEFAULT_PUBLIC_IP)
        self._default_timeout = default_timeout or self.DEFAULT_TIMEOUT
        # Track running servers: {port: {"pid": int, "path": str, "started": float}}
        # Instance variable to avoid shared state across multiple skill reloads
        self._servers: dict[int, dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "file_share"

    @property
    def description(self) -> str:
        return (
            "Start a temporary HTTP server to share files with download links. "
            "Returns a URL that can be used to download the file. "
            "Server auto-stops after timeout. "
            "Use when user wants to download PDFs, images, Markdown files, or any generated content."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to file or directory to share"
                },
                "timeout_minutes": {
                    "type": "integer",
                    "description": "Minutes before server auto-stops (default 10, max 60)",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 60
                },
                "port": {
                    "type": "integer",
                    "description": "Specific port to use (default: random port in 18000-18999)",
                    "default": 0
                }
            },
            "required": ["file_path"]
        }

    def _find_available_port(self, start: int = 18000, end: int = 18999) -> int:
        """Find an available port in the specified range."""
        ports_to_try = list(range(start, end + 1))
        random.shuffle(ports_to_try)  # Randomize to avoid collisions

        for port in ports_to_try:
            if port in self._servers:
                continue  # Already in use by us
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('', port))
                    return port
            except OSError:
                continue

        raise RuntimeError(f"No available port in range {start}-{end}")

    def _cleanup_old_servers(self):
        """Remove entries for servers that have stopped."""
        for port, info in list(self._servers.items()):
            pid = info.get("pid")
            if pid:
                try:
                    # Check if process is still running
                    os.kill(pid, 0)
                except OSError:
                    # Process has stopped, remove from tracking
                    del self._servers[port]

    async def execute(
        self,
        file_path: str,
        timeout_minutes: int = 10,
        port: int = 0,
        **kwargs: Any
    ) -> str:
        """Start a file share server.

        Args:
            file_path: Absolute path to file or directory to share
            timeout_minutes: Minutes before server auto-stops
            port: Specific port (0 for random)

        Returns:
            Download URL and status message
        """
        # Validate path
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            return f"Error: Path does not exist: {file_path}"

        # Cleanup old servers
        self._cleanup_old_servers()

        # Find port
        if port == 0:
            try:
                port = self._find_available_port()
            except RuntimeError as e:
                return f"Error: {str(e)}"
        elif port in self._servers:
            return f"Error: Port {port} is already in use"

        # Validate timeout
        timeout_seconds = min(max(timeout_minutes, 1) * 60, 3600)  # 1 min to 60 min

        # Get the server script path
        server_script = self.SERVER_SCRIPT
        if not server_script.exists():
            return f"Error: Server script not found: {server_script}"

        # Start the server process
        try:
            cmd = [
                sys.executable,
                str(server_script),
                "--path", str(path),
                "--port", str(port),
                "--timeout", str(timeout_seconds),
                "--ip", self._public_ip,
            ]

            # Start as background process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,  # Detach from current process
            )

            # Wait a bit for server to start
            await asyncio.sleep(0.5)

            # Check if process started successfully
            if process.poll() is not None:
                stderr = process.stderr.read().decode() if process.stderr else "Unknown error"
                return f"Error: Server failed to start: {stderr}"

            # Track the server
            self._servers[port] = {
                "pid": process.pid,
                "path": str(path),
                "started": time.time(),
                "timeout": timeout_seconds,
            }

            # Build URL
            if path.is_file():
                url = f"http://{self._public_ip}:{port}/{path.name}"
            else:
                url = f"http://{self._public_ip}:{port}/"

            # Format response
            response = self._format_response(url, timeout_minutes, path)
            return response

        except Exception as e:
            return f"Error starting file share server: {str(e)}"

    def _format_response(self, url: str, timeout_minutes: int, path: Path) -> str:
        """Format the response message."""
        file_info = f" ({path.name})" if path.is_file() else " (directory)"
        return (
            f"📥 文件分享服务已启动\n\n"
            f"下载链接: {url}\n\n"
            f"📁 文件: {path}{file_info}\n"
            f"⏰ 有效期: {timeout_minutes} 分钟\n"
            f"🌐 端口: {url.split(':')[-1].split('/')[0]}\n\n"
            f"提示: 请在有效期内下载，服务器将自动关闭。"
        )

    def stop_server(self, port: int) -> str:
        """Stop a specific server by port."""
        if port not in self._servers:
            return f"No server running on port {port}"

        info = self._servers[port]
        pid = info.get("pid")

        if pid:
            try:
                os.kill(pid, 15)  # SIGTERM
                del self._servers[port]
                return f"Server on port {port} stopped"
            except OSError:
                del self._servers[port]
                return f"Server on port {port} was already stopped"

        return f"Server on port {port} has no PID tracked"

    def stop_all_servers(self) -> str:
        """Stop all running file share servers."""
        stopped = []
        for port in list(self._servers.keys()):
            result = self.stop_server(port)
            stopped.append(f"Port {port}: stopped")

        return f"Stopped {len(stopped)} server(s)\n" + "\n".join(stopped) if stopped else "No servers running"

    def list_servers(self) -> str:
        """List all running file share servers."""
        if not self._servers:
            return "No file share servers running"

        lines = ["Active file share servers:"]
        for port, info in self._servers.items():
            age = int(time.time() - info.get("started", time.time()))
            remaining = info.get("timeout", 600) - age
            lines.append(
                f"  Port {port}: {info.get('path', '?')} "
                f"(started {age}s ago, {remaining}s remaining)"
            )

        return "\n".join(lines)


# Factory function for SkillManager to discover tools
def create_tools(**kwargs) -> list[Tool]:
    """Create and return the file share tool instance."""
    return [FileShareTool()]