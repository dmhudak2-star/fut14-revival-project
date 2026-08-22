#!/usr/bin/env python3
"""Écouter si une console envoie quoi que ce soit au relais.

    tools/xnet_probe.py --port 3074

Une sonde, pas un relais. Elle n'ouvre qu'une socket UDP et note ce qui
arrive : d'où, combien d'octets, et à quelle heure.

Ce qu'elle décide
-----------------
Le trafic de match ne passe pas par le serveur : les deux consoles se parlent
directement, en UDP, à l'adresse que ce serveur leur a donnée l'une pour
l'autre. Le 22 août, deux consoles se sont trouvées, sont entrées en match, et
ne se sont jamais vues -- l'une des deux a rapporté `STAT=0` sur l'autre. Deux
NAT domestiques, la France et les États-Unis, et plus aucun service d'EA pour
aider à la traversée.

`FIFA14_PEER_RELAY` réécrit l'adresse publique et le port dans le XNADDR que
chaque console reçoit pour l'autre, de sorte que le trafic parte vers cette
machine-ci. Reste à savoir si le noyau de la console honore cette réécriture :
un XNADDR porte aussi vingt octets d'`abOnline`, remplis par la passerelle
Xbox LIVE, et rien ne dit publiquement si l'association de sécurité s'en sert
pour router.

Si un seul paquet arrive ici, la réponse est oui et un relais vaut la peine
d'être écrit. S'il n'arrive rien, `abOnline` compte et il faudra chercher
ailleurs. Une demi-heure pour une réponse binaire, au lieu de plusieurs heures
pour découvrir le mur à la fin.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import datetime
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3074)
    parser.add_argument("--journal", type=Path, default=None,
                        help="où écrire ce qui arrive, en plus de l'écran")
    arguments = parser.parse_args(argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((arguments.listen, arguments.port))
    print(f"sonde: UDP {arguments.listen}:{arguments.port}", flush=True)
    print("en attente -- un seul paquet suffit à répondre", flush=True)

    seen: dict[str, int] = {}
    while True:
        try:
            payload, peer = sock.recvfrom(4096)
        except KeyboardInterrupt:
            break
        except OSError:
            continue
        where = f"{peer[0]}:{peer[1]}"
        first = where not in seen
        seen[where] = seen.get(where, 0) + 1
        record = {
            "time": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": "xnet_packet",
            "peer": where,
            "bytes": len(payload),
            "packets_from_peer": seen[where],
            "first_from_peer": first,
            # Les premiers octets seulement : le contenu est chiffré de bout en
            # bout entre les deux consoles et n'est pas à lire d'ici. Ce qui
            # compte est qu'il soit arrivé.
            "head": payload[:16].hex().upper(),
        }
        line = json.dumps(record, sort_keys=True)
        if first:
            print(f"\n*** PREMIER PAQUET de {where} -- la réécriture est honorée",
                  flush=True)
        print(line, flush=True)
        if arguments.journal is not None:
            arguments.journal.parent.mkdir(parents=True, exist_ok=True)
            with arguments.journal.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
