"""Decks im WLAN (ab Firmware 12): suchen, verbinden, anmelden.

Über TCP läuft genau dasselbe zeilenbasierte Protokoll wie über USB. NetConn bietet dafür dieselbe
Schnittstelle wie pyserial (write, readline mit .timeout, close), damit DeckLink unverändert bleibt.
Gefunden werden Decks per UDP-Rundruf "PCDECK?" (Antwort "PCDECK <id> <port> <fw> <belegt>").
Anmeldung gegenseitig per HMAC-SHA256 mit dem Schlüssel aus der Kopplung per USB; der Schlüssel
selbst geht nie übers Netz, und die App gibt einem fremden Gerät, das sich als Deck meldet, nichts preis.
"""

import hashlib
import hmac
import secrets
import socket
import time

DISC_PORT = 5556
BROADCAST = "255.255.255.255"


def proof(key, text):
    """HMAC-SHA256(schlüssel, text) als Hex – so beweisen sich App und Deck den Schlüssel."""
    return hmac.new(key.encode("ascii"), text.encode("ascii"), hashlib.sha256).hexdigest()


class NetConn:
    """TCP-Verbindung zum Deck mit der Schnittstelle von serial.Serial (soweit DeckLink sie braucht)."""

    def __init__(self, host, port, timeout=3.0):
        self.host = host
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""
        self.timeout = timeout

    def write(self, data):
        self.sock.settimeout(10)
        self.sock.sendall(data)

    def readline(self):
        """Eine Zeile (mit \\n) oder b"" nach self.timeout Sekunden."""
        end = time.time() + (self.timeout or 0)
        while b"\n" not in self.buf:
            rest = end - time.time()
            if rest <= 0:
                return b""
            self.sock.settimeout(rest)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return b""
            if not chunk:
                raise OSError("Verbindung vom Deck beendet")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line + b"\n"

    def reset_input_buffer(self):
        self.buf = b""

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def discover(wait=0.6):
    """Decks im Netz: {deck_id: (ip, port, fw, belegt)}."""
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("", 0))
        s.sendto(b"PCDECK?\n", (BROADCAST, DISC_PORT))
        end = time.time() + wait
        while time.time() < end:
            s.settimeout(max(0.05, end - time.time()))
            try:
                data, (ip, _) = s.recvfrom(256)
            except socket.timeout:
                break
            parts = data.decode("ascii", "replace").split()
            if len(parts) >= 5 and parts[0] == "PCDECK":
                try:
                    found[parts[1]] = (ip, int(parts[2]), int(parts[3]), parts[4] == "1")
                except ValueError:
                    pass
    except OSError:
        pass
    finally:
        s.close()
    return found


def connect(ip, port, key):
    """Anmelden und HELLO: (NetConn, deck_id, layout_hash, fw, design_hash) oder None."""
    try:
        conn = NetConn(ip, port)
    except OSError:
        return None
    try:
        # gegenseitige Anmeldung: das Deck beweist zuerst, dass es den Schlüssel kennt
        nonce = secrets.token_hex(16)
        conn.write(f"AUTH1 {nonce}\n".encode("ascii"))
        conn.timeout = 3.0
        parts = conn.readline().decode("ascii", "replace").split()
        if len(parts) != 3 or parts[0] != "CHAL" or not hmac.compare_digest(parts[2], proof(key, "D" + nonce)):
            conn.close()
            return None  # kein echtes (gekoppeltes) Deck – App gibt nichts preis
        conn.write(f"AUTH2 {proof(key, 'A' + parts[1])}\n".encode("ascii"))
        if conn.readline().decode("ascii", "replace").strip() != "ACK":
            conn.close()
            return None
        conn.write(b"HELLO\n")
        end = time.time() + 3.0
        while time.time() < end:
            line = conn.readline().decode("ascii", "replace").strip()
            if line.startswith("DECK "):
                parts = line.split()
                if len(parts) >= 6:
                    try:
                        fw = int(parts[1])
                    except ValueError:
                        fw = 1
                    return conn, parts[4], parts[5], fw, (parts[7] if len(parts) >= 8 else "none")
    except OSError:
        pass
    conn.close()
    return None
