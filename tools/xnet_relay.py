#!/usr/bin/env python3
"""Faire passer le trafic de match entre deux consoles qui ne se joignent pas.

    tools/xnet_relay.py --port 3074 --pairs runtime/relay-pairs.json

Pourquoi ceci existe
--------------------
Le match ne passe pas par le serveur : les deux consoles se parlent
directement, à l'adresse que le serveur leur a donnée l'une pour l'autre. Le
22 août, deux consoles se sont trouvées, sont entrées en match, et ne se sont
jamais vues -- deux NAT domestiques, la France et l'Algérie, et plus aucun
service d'EA pour aider à la traversée. Aucun des deux propriétaires ne peut
reconfigurer sa box.

`FIFA14_PEER_RELAY` réécrit l'adresse publique dans le XNADDR que chaque
console reçoit pour l'autre, et une sonde a montré que **le noyau honore la
réécriture** : 25 paquets de 122 octets sont arrivés, dix de chaque console,
depuis leurs ports 3074 respectifs. Il ne manquait qu'un intermédiaire pour
croiser les flux. C'est lui.

Ce qu'il ne fait pas
--------------------
Rien déchiffrer. Le trafic est chiffré de bout en bout entre les deux consoles
avec des clés qui voyagent dans `XSES` et qu'elles ont fabriquées elles-mêmes.
Ce relais transporte des octets opaques d'un bout à l'autre, et c'est tout ce
qu'il a besoin de faire.

Comment il sait qui va avec qui
-------------------------------
Le serveur Blaze le sait -- c'est lui qui apparie -- et il l'écrit dans un
petit fichier que ce relais relit dès qu'il change. Deviner à partir du seul
trafic marcherait à deux joueurs et casserait au troisième.

L'adresse publique et le port de chaque console sont appris de son premier
paquet, jamais supposés : c'est le NAT qui décide du port, pas nous.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import datetime
from pathlib import Path


class Pairs:
    """Qui joue contre qui, tel que le serveur Blaze l'a décidé."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.partner: dict[str, str] = {}
        self._stamp: tuple = ()

    def refresh(self) -> None:
        if self.path is None:
            return
        try:
            stat = self.path.stat()
        except OSError:
            self.partner = {}
            return
        key = (stat.st_size, stat.st_mtime)
        if key == self._stamp:
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        table: dict[str, str] = {}
        for pair in document.get("pairs", []):
            if len(pair) == 2 and pair[0] != pair[1]:
                table[str(pair[0])] = str(pair[1])
                table[str(pair[1])] = str(pair[0])
        self.partner = table
        self._stamp = key

    def of(self, address: str) -> str | None:
        return self.partner.get(address)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3074)
    parser.add_argument("--pairs", type=Path, default=None,
                        help="le fichier où le serveur Blaze écrit les paires")
    parser.add_argument("--journal", type=Path, default=None)
    arguments = parser.parse_args(argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((arguments.listen, arguments.port))
    sock.settimeout(1.0)
    print(f"relais: UDP {arguments.listen}:{arguments.port}", flush=True)

    pairs = Pairs(arguments.pairs)
    # L'endroit d'où chaque console parle réellement, appris de ses paquets.
    endpoint: dict[str, tuple[str, int]] = {}
    counts: dict[str, int] = {}
    dropped: dict[str, int] = {}
    # Ce qu'un joueur a envoyé avant que son partenaire n'ait parlé.
    #
    # La première version jetait ces paquets-là : on ne savait pas encore où
    # joindre l'autre. Le 22 août, le tout premier paquet d'une console est
    # parti à la poubelle pour cette raison -- et s'il portait l'ouverture de
    # l'échange de clés, tout ce qui a suivi était des relances sans espoir.
    # Les deux consoles se sont parlé pendant dix paquets sans jamais
    # s'entendre.
    #
    # Ils attendent maintenant. Une poignée suffit : ce qui compte est le
    # début de la conversation, pas son milieu.
    waiting: dict[str, list[bytes]] = {}
    HELD = 16

    def note(kind: str, **values: object) -> None:
        record = {
            "time": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": kind,
            **values,
        }
        line = json.dumps(record, sort_keys=True)
        print(line, flush=True)
        if arguments.journal is not None:
            arguments.journal.parent.mkdir(parents=True, exist_ok=True)
            with arguments.journal.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")

    while True:
        pairs.refresh()
        try:
            payload, peer = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            continue
        except KeyboardInterrupt:
            break

        source, port = peer
        if endpoint.get(source) != (source, port):
            endpoint[source] = (source, port)
            note("relay_endpoint", peer=f"{source}:{port}",
                 partner=pairs.of(source))
        counts[source] = counts.get(source, 0) + 1

        partner = pairs.of(source)
        if partner is None:
            # Personne à qui la donner. Compté, pas jeté en silence.
            dropped[source] = dropped.get(source, 0) + 1
            if dropped[source] in (1, 100, 1000):
                note("relay_unpaired", peer=source, packets=dropped[source])
            continue
        target = endpoint.get(partner)
        if target is None:
            # L'autre n'a pas encore parlé : on garde, on ne jette pas.
            held = waiting.setdefault(source, [])
            if len(held) < HELD:
                held.append(payload)
                if len(held) == 1:
                    note("relay_holding", peer=source, partner=partner)
            else:
                dropped[source] = dropped.get(source, 0) + 1
                if dropped[source] in (1, 100, 1000):
                    note("relay_hold_full", peer=source, partner=partner,
                         packets=dropped[source])
            continue

        # Le partenaire vient d'être localisé : on délivre d'abord ce qu'on
        # gardait, dans l'ordre, avant le paquet du moment.
        held = waiting.pop(partner, None)
        if held:
            note("relay_flushed", peer=partner, target=f"{source}:{port}",
                 packets=len(held))
            for kept in held:
                try:
                    sock.sendto(kept, (source, port))
                except OSError:
                    break
        try:
            sock.sendto(payload, target)
        except OSError as error:
            note("relay_send_failed", peer=source, target=f"{target[0]}:{target[1]}",
                 error=str(error))
            continue
        if counts[source] in (1, 10, 100, 1000, 10000):
            note("relay_forwarded", peer=f"{source}:{port}",
                 target=f"{target[0]}:{target[1]}", bytes=len(payload),
                 packets=counts[source])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
