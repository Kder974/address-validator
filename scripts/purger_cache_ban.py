#!/usr/bin/env python
"""Purge les résultats de cache France / outre-mer calculés avant D79.

Pourquoi ce script existe : avant D79, une adresse française était interrogée
auprès de SmartyStreets (service américain), qui renvoyait la saisie **sans code
DPV** ; le pipeline la déclarait pourtant `validated: true` et mettait ce
résultat en cache Redis pour 30 jours. Mesuré en production le 17/10/2026 :
4 entrées `addr:*` France/outre-mer, toutes `validated: true`, dont
« Lyon 75005 » (l'adresse incohérente signalée). Déployer le correctif ne suffit
pas : ces entrées continueraient de répondre faux jusqu'à leur expiration.

Le script lit le cache de la configuration (`REDIS_URL`, comme l'application),
ne touche qu'aux clés `addr:*` dont le résultat standardisé porte un pays de
`app.validation.ban.PAYS_BAN`, et **simule par défaut** : `--apply` supprime.

Usage :
    venv/bin/python scripts/purger_cache_ban.py            # liste, ne supprime rien
    venv/bin/python scripts/purger_cache_ban.py --apply    # supprime
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Iterable, Iterator

from redis import Redis

from app.config import get_settings
from app.validation.ban import PAYS_BAN


def entrees_francaises(redis: Redis) -> Iterator[tuple[str, dict]]:
    """Entrées de cache `addr:*` dont l'adresse standardisée est en France."""
    for cle in redis.scan_iter("addr:*", count=500):
        try:
            valeur = json.loads(redis.get(cle))
        except (TypeError, ValueError):
            continue
        if not isinstance(valeur, dict):
            continue
        pays = str((valeur.get("standardized") or {}).get("country") or "").upper()
        if pays in PAYS_BAN:
            yield cle, valeur


def purger(redis: Redis, appliquer: bool = False) -> list[tuple[str, dict]]:
    """Liste (et supprime si `appliquer`) les entrées France/outre-mer."""
    trouvees = list(entrees_francaises(redis))
    if appliquer and trouvees:
        redis.delete(*(cle for cle, _valeur in trouvees))
    return trouvees


def main(argv: Iterable[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parseur.add_argument(
        "--apply",
        action="store_true",
        help="supprime réellement les entrées (par défaut : simple simulation)",
    )
    arguments = parseur.parse_args(list(argv) if argv is not None else None)

    reglages = get_settings()
    redis = Redis.from_url(reglages.redis_url, decode_responses=True)
    try:
        redis.ping()
    except Exception as exc:  # noqa: BLE001 - cache injoignable = rien à purger, code 2
        print(f"CACHE INJOIGNABLE : {exc}", file=sys.stderr)
        return 2

    entrees = purger(redis, appliquer=arguments.apply)
    for cle, valeur in entrees:
        standard = valeur.get("standardized") or {}
        print(
            f"{cle} | pays={standard.get('country')} | {standard.get('city')} "
            f"{standard.get('zip')} | validated={valeur.get('validated')} "
            f"| deliverability={valeur.get('deliverability')}"
        )
    if arguments.apply:
        print(f"PURGE TERMINEE : {len(entrees)} entree(s) France/outre-mer supprimee(s)")
    else:
        print(
            f"SIMULATION : {len(entrees)} entree(s) France/outre-mer seraient supprimee(s) "
            "(relancer avec --apply)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
