"""Minimaler Client für obs-websocket 5 (in OBS Studio ab Version 28 eingebaut).

Nur Python-Standardbibliothek: WebSocket-Handshake, Rahmen (Frames) und die
Anmeldung von obs-websocket (SHA-256 aus Passwort, Salt und Challenge).
"""

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
import uuid


class OBSError(Exception):
    pass


class OBSRequestError(OBSError):
    """OBS ist erreichbar, lehnt aber die Anfrage ab (z. B. Szene gibt es nicht)."""


class OBS:
    def __init__(self, host="localhost", port=4455, password="", timeout=3.0):
        self.host, self.port, self.password, self.timeout = host, int(port), password, timeout
        self.sock = None
        self.buf = b""
        self.lock = threading.Lock()

    # ---------------------------------------------------------- WebSocket
    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OBSError("Verbindung zu OBS getrennt")
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def _send_frame(self, opcode, payload):
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)  # Client-Rahmen müssen maskiert sein
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _recv_message(self):
        parts = []
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b2 & 0x80 else None
            data = self._recv_exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x9:        # Ping -> Pong
                self._send_frame(0xA, data)
                continue
            if opcode == 0xA:        # Pong
                continue
            if opcode == 0x8:        # Close
                raise OBSError("OBS hat die Verbindung geschlossen")
            parts.append(data)
            if fin:
                return json.loads(b"".join(parts).decode("utf-8"))

    def _send(self, op, d):
        self._send_frame(0x1, json.dumps({"op": op, "d": d}).encode("utf-8"))

    # ---------------------------------------------------------- obs-websocket
    def connect(self):
        self.close()
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                           "Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OBSError("Kein WebSocket-Server (OBS-WebSocket aktiviert?)")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise OBSError("WebSocket-Handshake abgelehnt")
        hello = self._recv_message()
        if hello.get("op") != 0:
            raise OBSError("Unerwartete Antwort von OBS")
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = hello["d"].get("authentication")
        if auth:
            if not self.password:
                raise OBSError("OBS verlangt ein Passwort")
            secret = base64.b64encode(hashlib.sha256((self.password + auth["salt"]).encode()).digest())
            ident["authentication"] = base64.b64encode(
                hashlib.sha256(secret + auth["challenge"].encode()).digest()).decode()
        self._send(1, ident)
        msg = self._recv_message()
        if msg.get("op") != 2:
            raise OBSError("Anmeldung bei OBS fehlgeschlagen (Passwort?)")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock, self.buf = None, b""

    def request(self, request_type, data=None):
        """Anfrage stellen und responseData zurückgeben (verbindet bei Bedarf neu)."""
        with self.lock:
            if not self.sock:
                self.connect()
            rid = uuid.uuid4().hex
            d = {"requestType": request_type, "requestId": rid}
            if data:
                d["requestData"] = data
            try:
                self._send(6, d)
                while True:
                    msg = self._recv_message()
                    if msg.get("op") == 7 and msg["d"].get("requestId") == rid:
                        break
            except (OSError, OBSError):
                self.close()
                raise
            status = msg["d"].get("requestStatus", {})
            if not status.get("result"):
                raise OBSRequestError(status.get("comment") or f"Anfrage {request_type} fehlgeschlagen")
            return msg["d"].get("responseData") or {}


# ----------------------------------------------------------------- gemeinsame Verbindungen
_pool = {}
_pool_lock = threading.Lock()
_failed = {}  # key -> Zeitpunkt des letzten Fehlschlags (nicht jede Sekunde neu versuchen)
RETRY_AFTER = 5.0


def call(host, port, password, request_type, data=None):
    key = (host, int(port), password)
    with _pool_lock:
        if time.monotonic() - _failed.get(key, -RETRY_AFTER) < RETRY_AFTER:
            raise OBSError("OBS nicht erreichbar")
        obs = _pool.setdefault(key, OBS(host, port, password))
    try:
        return obs.request(request_type, data)
    except OBSRequestError:
        raise
    except (OSError, OBSError):
        with _pool_lock:
            _failed[key] = time.monotonic()
        raise
