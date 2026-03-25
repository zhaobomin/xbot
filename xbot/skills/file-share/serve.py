#!/usr/bin/env python3
"""
File Share Server - Temporary HTTP server for file sharing

Usage:
    python serve.py --path /path/to/file.pdf --port 18080 --timeout 600

The server will:
1. Start on the specified port (or random port if 0)
2. Serve files from the specified directory
3. Auto-stop after timeout seconds
"""

import argparse
import http.server
import mimetypes
import os
import random
import signal
import socket
import socketserver
import sys
import threading
import time


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler with minimal logging and proper UTF-8 encoding"""

    def log_message(self, format, *args):
        # Suppress default logging, but print download info
        if "GET" in str(args):
            print(f"[File-Share] {args[0]}")

    def guess_type(self, path):
        """Override to ensure UTF-8 charset for text files"""
        base, ext = os.path.splitext(path)
        if ext in ('.md', '.markdown'):
            return 'text/markdown; charset=utf-8'
        elif ext in ('.txt', '.log'):
            return 'text/plain; charset=utf-8'
        elif ext in ('.html', '.htm'):
            return 'text/html; charset=utf-8'
        elif ext in ('.css',):
            return 'text/css; charset=utf-8'
        elif ext in ('.js',):
            return 'application/javascript; charset=utf-8'
        elif ext in ('.json',):
            return 'application/json; charset=utf-8'
        elif ext in ('.xml',):
            return 'application/xml; charset=utf-8'
        elif ext in ('.svg',):
            return 'image/svg+xml; charset=utf-8'
        else:
            # Use default mimetypes
            mime_type = mimetypes.guess_type(path)[0]
            if mime_type and mime_type.startswith('text/'):
                return f'{mime_type}; charset=utf-8'
            return mime_type or 'application/octet-stream'

    def end_headers(self):
        # Add CORS headers for browser access
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        super().end_headers()


class ReuseAddrServer(socketserver.TCPServer):
    """Server that allows address reuse"""
    allow_reuse_address = True


def find_available_port(start_port: int = 18000, end_port: int = 18999) -> int:
    """Find an available port in the specified range"""
    for port in range(start_port, end_port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No available port in range {start_port}-{end_port}")


def start_server(path: str, port: int, timeout: int):
    """Start the HTTP server with timeout"""

    # Change to the directory containing the file/folder
    if os.path.isfile(path):
        serve_dir = os.path.dirname(path) or '.'
        filename = os.path.basename(path)
    else:
        serve_dir = path
        filename = None

    os.chdir(serve_dir)

    # Create server
    server = ReuseAddrServer(("", port), QuietHandler)

    # Get the actual port (in case port was 0)
    actual_port = server.server_address[1]

    # Get public IP from environment or use default
    public_ip = os.environ.get('XBOT_PUBLIC_IP', '121.40.69.126')

    # Print URL info for the caller
    if filename:
        print(f"URL: http://{public_ip}:{actual_port}/{filename}")
    else:
        print(f"URL: http://{public_ip}:{actual_port}/")
    print(f"PORT: {actual_port}")
    print(f"TIMEOUT: {timeout}s")
    print(f"SERVING: {serve_dir}")
    print("SERVER_STARTED")
    sys.stdout.flush()

    # Setup timeout handler
    def timeout_handler():
        time.sleep(timeout)
        print(f"[File-Share] Timeout reached, shutting down server on port {actual_port}")
        server.shutdown()

    timeout_thread = threading.Thread(target=timeout_handler, daemon=True)
    timeout_thread.start()

    # Handle SIGTERM gracefully
    def sigterm_handler(signum, frame):
        print(f"[File-Share] Received signal, shutting down server on port {actual_port}")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGINT, sigterm_handler)

    # Start serving
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print(f"[File-Share] Server stopped on port {actual_port}")


def main():
    parser = argparse.ArgumentParser(description='Start a temporary file sharing server')
    parser.add_argument('--path', '-p', required=True, help='File or directory path to share')
    parser.add_argument('--port', '-P', type=int, default=0, help='Port to use (0 for random)')
    parser.add_argument('--timeout', '-t', type=int, default=600, help='Timeout in seconds (default 600)')
    parser.add_argument('--ip', '-i', default='121.40.69.126', help='Public IP for URL generation')

    args = parser.parse_args()

    # Validate path
    if not os.path.exists(args.path):
        print(f"Error: Path does not exist: {args.path}", file=sys.stderr)
        sys.exit(1)

    # Find port
    if args.port == 0:
        port = find_available_port()
    else:
        port = args.port

    # Set public IP
    os.environ['XBOT_PUBLIC_IP'] = args.ip

    # Start server
    start_server(args.path, port, args.timeout)


if __name__ == '__main__':
    main()