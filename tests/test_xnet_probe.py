"""La sonde répond à une question binaire, et il faut qu'elle y réponde bien.

Un XNADDR fait 36 octets et seuls deux champs du milieu sont réécrits. Les
vingt derniers -- `abOnline`, remplis par la passerelle Xbox LIVE -- sont
précisément la matière qu'on ne sait pas lire, et le seul résultat qui vaut
quelque chose est celui obtenu en n'y touchant pas.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))
sys.path.insert(0, str(REPO / "tools"))

# Le vrai XNADDR envoyé par la console le 21 août.
REAL = bytes.fromhex(
    "C0A80119020B639A0C027CED8D19694F0ADF727600F4E8FD858C6104000000FA01000000"
)


def relayed():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "blaze_server_relay", REPO / "server" / "fifa14_blaze_server.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Les dataclasses du module cherchent leur propre module dans sys.modules
    # au moment d'être définies ; sans cette ligne, l'import échoue.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.relayed_address


def test_only_the_public_address_and_port_move() -> None:
    out = relayed()(REAL, ("87.106.7.87", 3074))
    assert len(out) == len(REAL) == 36
    assert out[0:4] == REAL[0:4]                      # l'adresse LAN, intacte
    assert out[4:8] == bytes([87, 106, 7, 87])        # l'adresse publique
    assert out[8:10] == (3074).to_bytes(2, "big")     # le port
    assert out[10:16] == REAL[10:16]                  # la MAC de la console
    assert out[16:] == REAL[16:]                      # abOnline, jamais touché


def test_a_malformed_address_is_left_alone() -> None:
    """Mieux vaut relayer une adresse qu'on n'a pas su lire que d'en fabriquer
    une fausse."""
    rewrite = relayed()
    assert rewrite(b"\x01\x02", ("87.106.7.87", 3074)) == b"\x01\x02"
    assert rewrite(REAL, ("pas.une.adresse", 3074)) == REAL
    assert rewrite(REAL, ("1.2.3", 3074)) == REAL


def test_the_probe_records_what_arrives() -> None:
    """Un seul paquet suffit à répondre, donc le premier doit être visible."""
    journal = Path(__file__).parent / "_probe.jsonl"
    if journal.exists():
        journal.unlink()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    probe = subprocess.Popen(
        [sys.executable, str(REPO / "tools" / "xnet_probe.py"),
         "--listen", "127.0.0.1", "--port", str(port), "--journal", str(journal)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.time() + 5
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while time.time() < deadline and not journal.exists():
            sender.sendto(b"\xDE\xAD\xBE\xEF", ("127.0.0.1", port))
            time.sleep(0.1)
        assert journal.exists(), "la sonde n'a rien écrit"
        import json
        record = json.loads(journal.read_text().splitlines()[0])
        assert record["event"] == "xnet_packet"
        assert record["bytes"] == 4
        assert record["first_from_peer"] is True
        assert record["head"] == "DEADBEEF"
    finally:
        probe.kill()
        probe.wait(timeout=5)
        if journal.exists():
            journal.unlink()
