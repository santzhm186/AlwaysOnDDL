# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in, certificate-pinned HTTPS relay for a trusted local network.

The relay carries an explicitly shared goal, oath and small device status only.
It does not collect window titles, cloud-sync archives, or issue lock commands.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import ssl
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 16384
MAX_CONNECTIONS = 16
_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_ACTIONS = {"BACK", "WORTH", "BREATHE", "PAUSE", "RESUME"}


class _BoundedHTTPServer(ThreadingHTTPServer):
    """Bound TLS handshakes and HTTP workers, including unauthenticated peers."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        self._request_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def get_request(self):
        sock, address = self.socket.accept()
        sock.settimeout(5)
        return sock, address

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def handle_error(self, request, client_address):
        # Malformed/abandoned connections must not dump tracebacks or peer data.
        pass


def _atomic_text(path: Path, value: str) -> None:
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class RelayServer:
    def __init__(self, data_dir, host="0.0.0.0", port=8765):
        self.data_dir = Path(data_dir) / "pairing"
        self.host, self.port = host, int(port)
        self._guard = threading.RLock()
        self._server = None
        self._thread = None
        self._token = ""
        self._fingerprint = ""
        self._desktop = {}
        self._peers = {}
        self._commands = deque(maxlen=256)
        self._command_ids = OrderedDict()
        self._claims = OrderedDict()
        self._lease_until = 0.0
        self._error = ""

    def _credentials(self):
        # Optional dependency: monitoring works even if this package is absent.
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        self.data_dir.mkdir(parents=True, exist_ok=True)
        cert_path, key_path = self.data_dir / "server.crt", self.data_dir / "server.key"
        cert = None
        if cert_path.exists() and key_path.exists():
            try:
                cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
                if cert.not_valid_after_utc <= datetime.now(timezone.utc):
                    cert = None
            except (ValueError, OSError):
                cert = None
        if cert is None:
            key = ec.generate_private_key(ec.SECP256R1())
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "IronWill Local")])
            now = datetime.now(timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                    .public_key(key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - timedelta(minutes=5))
                    .not_valid_after(now + timedelta(days=365))
                    .add_extension(x509.SubjectAlternativeName([
                        x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
                    ]), critical=False).sign(key, hashes.SHA256()))
            _atomic_text(key_path, key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("ascii"))
            _atomic_text(cert_path, cert.public_bytes(serialization.Encoding.PEM).decode("ascii"))
        self._fingerprint = cert.fingerprint(hashes.SHA256()).hex()
        token_path = self.data_dir / "token.txt"
        if token_path.exists():
            self._token = token_path.read_text(encoding="utf-8").strip()
        if len(self._token) < 32:
            self._token = secrets.token_urlsafe(32)
            _atomic_text(token_path, self._token)
        return cert_path, key_path

    def start(self):
        if self._server is not None:
            return self.get_info()
        server = None
        try:
            cert, key = self._credentials()
            owner = self

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.0"
                server_version = "IronWillLocal/0.1"

                def setup(self):
                    super().setup()
                    self.connection.settimeout(5)

                def log_message(self, *_):
                    pass  # No tokens, user content or request paths in console logs.

                def reply(self, code, payload):
                    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(data)

                def authorized(self):
                    header = self.headers.get("Authorization", "")
                    expected = "Bearer " + owner._token
                    if not hmac.compare_digest(header.encode("utf-8"), expected.encode("utf-8")):
                        self.reply(401, {"error": "unauthorized"})
                        return False
                    return True

                def do_GET(self):
                    if not self.authorized():
                        return
                    if self.path != "/v1/state":
                        self.reply(404, {"error": "not_found"})
                        return
                    with owner._guard:
                        state = dict(owner._desktop)
                    self.reply(200, {"protocol_version": 1, "desktop": state,
                                     "server_time_ms": int(time.time() * 1000)})

                def do_POST(self):
                    if not self.authorized():
                        return
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        if length <= 0 or length > MAX_BODY:
                            self.reply(413, {"error": "invalid_body_size"})
                            return
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                        if not isinstance(body, dict):
                            raise ValueError("object required")
                        code, result = owner._dispatch(self.path, body)
                        self.reply(code, result)
                    except (ValueError, UnicodeError, TimeoutError):
                        self.reply(400, {"error": "invalid_request"})

            # TLS handshakes happen in bounded, timed workers, never the accept loop.
            server = _BoundedHTTPServer((self.host, self.port), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(cert), str(key))
            server.socket = context.wrap_socket(server.socket, server_side=True,
                                              do_handshake_on_connect=False)
            self._server = server
            self.port = server.server_address[1]
            self._thread = threading.Thread(target=server.serve_forever, name="ironwill-relay", daemon=True)
            self._thread.start()
            self._error = ""
        except Exception as exc:
            if server is not None:
                server.server_close()
            self._server = None
            self._error = "无法启动局域网同步：" + type(exc).__name__
            raise RuntimeError(self._error) from exc
        return self.get_info()

    def stop(self):
        server, self._server = self._server, None
        if server:
            server.shutdown()
            server.server_close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        with self._guard:
            self._commands.clear()
            self._peers.clear()
            self._claims.clear()
            self._lease_until = 0

    def set_desktop_state(self, state):
        cleaned = {}
        for key in ("status", "id", "goal", "oath", "stage"):
            value = state.get(key)
            if key == "stage":
                cleaned[key] = max(0, min(3, int(value or 0)))
            elif isinstance(value, str):
                cleaned[key] = value[:4000]
        with self._guard:
            self._desktop = cleaned

    def drain_commands(self):
        with self._guard:
            now = time.monotonic()
            commands = [dict(item) for item in self._commands if now - item["_received"] <= 30]
            self._commands.clear()
        for item in commands:
            item.pop("_received", None)
        return commands

    def claim_reminder(self, device_id, event_id):
        if not _ID.fullmatch(str(device_id)) or not _ID.fullmatch(str(event_id)):
            return False
        with self._guard:
            now = time.monotonic()
            for identifier, ts in list(self._claims.items()):
                if now - ts > 900:
                    self._claims.pop(identifier, None)
            if event_id in self._claims or now < self._lease_until:
                return False
            self._lease_until = now + 30
            self._claims[event_id] = now
            while len(self._claims) > 512:
                self._claims.popitem(last=False)
            return True

    def _dispatch(self, path, body):
        device_id = body.get("device_id", "")
        if not isinstance(device_id, str) or not _ID.fullmatch(device_id):
            return 400, {"error": "invalid_device_id"}
        with self._guard:
            if path == "/v1/device":
                now = int(time.time() * 1000)
                self._peers = {k: v for k, v in self._peers.items() if now - v["last_seen_ms"] < 300000}
                if device_id not in self._peers and len(self._peers) >= 16:
                    return 429, {"error": "peer_limit"}
                stage = body.get("stage", 0)
                if not isinstance(stage, int) or isinstance(stage, bool) or not 0 <= stage <= 3:
                    return 400, {"error": "invalid_stage"}
                self._peers[device_id] = {
                    "device_id": device_id,
                    "device_name": str(body.get("device_name", "Android"))[:80],
                    "status": str(body.get("status", "unknown"))[:24],
                    "stage": stage, "active": body.get("active") is True,
                    "last_seen_ms": now,
                }
                return 200, {"ok": True}
            if path == "/v1/action":
                action, command_id = body.get("action"), body.get("command_id", "")
                if not isinstance(action, str) or action not in _ACTIONS or not isinstance(command_id, str) or not _ID.fullmatch(command_id):
                    return 400, {"error": "invalid_action"}
                session_id = body.get("session_id")
                if not isinstance(session_id, str) or not session_id or session_id != self._desktop.get("id"):
                    return 409, {"error": "session_changed"}
                now = time.monotonic()
                for key, ts in list(self._command_ids.items()):
                    if now - ts > 900:
                        self._command_ids.pop(key, None)
                if command_id in self._command_ids:
                    return 200, {"ok": True, "duplicate": True}
                if len(self._commands) >= self._commands.maxlen:
                    return 429, {"error": "queue_full"}
                self._commands.append({"device_id": device_id, "command_id": command_id,
                                       "action": action, "session_id": session_id, "_received": now})
                self._command_ids[command_id] = now
                while len(self._command_ids) > 1024:
                    self._command_ids.popitem(last=False)
                return 202, {"ok": True, "duplicate": False}
            if path == "/v1/claim":
                granted = self.claim_reminder(device_id, body.get("event_id", ""))
                return 200, {"granted": granted, "lease_seconds": 30}
        return 404, {"error": "not_found"}

    def get_info(self):
        addresses = {"127.0.0.1"}
        try:
            for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                address = item[4][0]
                if not address.startswith("169.254."):
                    addresses.add(address)
        except OSError:
            pass
        with self._guard:
            peers = [dict(v) for v in self._peers.values()]
        return {"running": self._server is not None,
                "urls": [f"https://{address}:{self.port}" for address in sorted(addresses,
                         key=lambda value: (value.startswith("127."), value))],
                "token": self._token, "fingerprint": self._fingerprint,
                "peers": peers, "error": self._error}
