"""
Tests para monitoring.heartbeat — el deadman de v2.18.

No se mockea `requests`: se levanta un servidor HTTP real local y se verifica
comportamiento observable. Los modos de falla (timeout, 500, puerto cerrado)
se reproducen de verdad, no se simulan.

La garantía que estos tests protegen: `heartbeat()` NUNCA propaga una excepción.
Si lo hiciera, una feature de monitoreo podría tumbar el trading — exactamente
lo contrario de su propósito.
"""

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from monitoring import heartbeat


class _Recorder(BaseHTTPRequestHandler):
    """Registra los paths recibidos; responde según el modo del servidor."""

    def do_GET(self):
        self.server.received.append(self.path)

        if self.server.mode == "slow":
            time.sleep(self.server.slow_seconds)

        status = 500 if self.server.mode == "error" else 200
        self.send_response(status)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass  # silencia el log a stderr


@pytest.fixture
def server():
    """Servidor HTTP real en un puerto libre. `mode` controla su respuesta."""
    httpd = HTTPServer(("127.0.0.1", 0), _Recorder)
    httpd.received = []
    httpd.mode = "ok"
    httpd.slow_seconds = 0

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    httpd.url = f"http://127.0.0.1:{httpd.server_port}/ping-token"
    yield httpd

    httpd.shutdown()
    httpd.server_close()


def test_sends_get_request_to_configured_url(server):
    """El ping llega al servidor, en la URL configurada."""
    heartbeat(server.url)

    assert server.received == ["/ping-token"]


def test_returns_true_when_ping_delivered(server):
    """Un ping entregado se reporta como éxito."""
    assert heartbeat(server.url) is True


def test_empty_url_sends_nothing(server):
    """Sin URL configurada el heartbeat es un no-op: no hace ninguna request."""
    result = heartbeat("")

    assert result is False
    assert server.received == []


def test_server_error_does_not_raise(server):
    """Un 500 del servicio externo no puede romper el bot."""
    server.mode = "error"

    assert heartbeat(server.url) is False
    assert server.received == ["/ping-token"]


def test_timeout_does_not_raise(server):
    """Un servicio colgado no puede bloquear ni romper el scan cycle."""
    server.mode = "slow"
    server.slow_seconds = 3

    started = time.monotonic()
    result = heartbeat(server.url, timeout=0.5)
    elapsed = time.monotonic() - started

    assert result is False
    assert elapsed < 2, f"heartbeat bloqueó {elapsed:.1f}s — debe respetar el timeout"


def test_unreachable_host_does_not_raise():
    """Puerto cerrado (droplet sin red, DNS caído): falla en silencio."""
    # Puerto 9 = 'discard', cerrado en la práctica; sin listener → connection refused
    assert heartbeat("http://127.0.0.1:9/nope", timeout=0.5) is False
