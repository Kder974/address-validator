"""Journal des tentatives non payées — décision D30.

But : savoir si l'API est **trouvée**. Sans ce journal, un agent qui découvre
`/validate` et repart avec un `402` est invisible ; avec lui, on mesure le
fossé entre « découvre » et « paie », qui est exactement le sujet du chantier
de découvrabilité.

Deux principes non négociables :

1. **Minimisation (RGPD).** La ligne ne contient jamais l'adresse validée :
   uniquement des métadonnées de requête. L'IP est pseudonymisée par défaut
   (SHA-256 + sel) ; `DISCOVERY_LOG_RAW_IP=true` la met en clair, ce qui doit
   rester une mesure d'enquête datée, pas un réglage de confort.
2. **Foi dans `X-Forwarded-For` seulement derrière le proxy.** L'application
   écoute sur `127.0.0.1` (D21) et Nginx pose lui-même `X-Forwarded-For` : la
   valeur est donc fiable ici. Si le service était exposé directement, cet
   en-tête serait falsifiable par le client et la pseudonymisation porterait
   sur une valeur choisie par l'attaquant.

Le journal est un fichier **JSONL** (une ligne = un objet JSON) : il se lit
avec `grep`/`jq`, se compte par jour, et se purge sans réécriture partielle.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from fastapi import Request

from app.config import get_settings

logger = logging.getLogger("address_validator.discovery")

#: Handlers déjà posés, par chemin de fichier (idempotence entre appels).
_ATTACHED: set[str] = set()


def _salt() -> bytes:
    """Sel de pseudonymisation : dédié s'il est fourni, dérivé du secret sinon."""
    settings = get_settings()
    if settings.discovery_log_salt:
        return settings.discovery_log_salt.encode()
    return hashlib.sha256(f"ip-salt:{settings.api_secret_key}".encode()).digest()


def client_ip(request: Request) -> str:
    """IP du client : première entrée de `X-Forwarded-For`, sinon la socket."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def pseudonymise(ip: str) -> str:
    """Pseudonyme stable d'une IP (32 premiers caractères hex du SHA-256 salé)."""
    digest = hashlib.sha256(_salt() + b"|" + ip.encode()).hexdigest()
    return digest[:32]


def configure_logging() -> None:
    """Pose le handler fichier si `DISCOVERY_LOG_PATH` est défini (idempotent)."""
    settings = get_settings()
    if not settings.discovery_log_enabled or not settings.discovery_log_path:
        return
    target = str(Path(settings.discovery_log_path))
    if target in _ATTACHED:
        return
    try:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        _ATTACHED.add(target)
    except OSError as exc:  # pragma: no cover - dépend du systeme de fichiers
        # Un journal impossible a ecrire ne doit JAMAIS faire echouer une
        # reponse payante ou un 402 : on degrade, on ne casse pas.
        logger.warning("journal de decouverte non activable (%s) : %s", target, exc)


def log_unpaid_attempt(
    request: Request, price: float, reason: str = "missing_or_invalid_proof"
) -> dict:
    """Trace une tentative refusée en 402 et renvoie l'entrée écrite."""
    settings = get_settings()
    raw_ip = client_ip(request)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": "unpaid_request",
        "endpoint": request.url.path,
        "method": request.method,
        "price_usdc": price,
        "reason": reason,
        "user_agent": request.headers.get("user-agent", "")[:256],
        "referer": request.headers.get("referer", "")[:256],
    }
    entry["ip"] = raw_ip if settings.discovery_log_raw_ip else pseudonymise(raw_ip)
    entry["ip_pseudonymised"] = not settings.discovery_log_raw_ip

    if not settings.discovery_log_enabled:
        return entry

    configure_logging()
    logger.info(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    return entry


def read_events(path: str | Path) -> Iterator[dict]:
    """Lit un journal JSONL en ignorant les lignes illisibles."""
    file = Path(path)
    if not file.is_file():
        return
    with file.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def count_since(path: str | Path, since: datetime) -> int:
    """Nombre d'événements postérieurs à `since` (comparaison sur `ts`)."""
    total = 0
    for event in read_events(path):
        stamp = _parse_ts(event.get("ts", ""))
        if stamp is not None and stamp >= since:
            total += 1
    return total


def prune(path: str | Path, retention_days: int) -> int:
    """Supprime les entrées plus vieilles que `retention_days`. Renvoie le reste.

    Conservation annoncée = conservation appliquée (PRIVACY.md §3) : le rapport
    quotidien appelle cette fonction, la rétention n'est donc pas une intention.
    """
    file = Path(path)
    if not file.is_file():
        return 0
    limit = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept: list[str] = []
    for event in read_events(file):
        stamp = _parse_ts(event.get("ts", ""))
        if stamp is None or stamp >= limit:
            kept.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
    file.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return len(kept)


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

