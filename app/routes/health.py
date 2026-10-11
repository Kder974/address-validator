"""GET /health — santé du service.

Conforme à la spec (Phase 2). Les sondes DB/Redis sont bornées dans le temps
(timeouts courts) pour que l'endpoint réponde vite même en cas de panne.

Correctif D22 (mesuré, voir TEST_REPORT.md) : `async def` + appels bloquants
gelait la boucle d'événements pour **toutes** les requêtes concurrentes, et
`localhost` étant résolu en ::1 *puis* 127.0.0.1, chaque sonde payait deux
connexions refusées (4,02 s pour Postgres + 2,02 s pour Redis = 6,03 s mesurés
sur un poste où les deux services sont éteints). Désormais :

  1. chaque sonde s'exécute hors boucle (`asyncio.to_thread`) ;
  2. les deux sondes partent en parallèle (`asyncio.gather`) ;
  3. une borne globale garantit un /health toujours rapide.

Les services étant co-localisés en production, une sonde saine répond en
quelques millisecondes ; la borne n'est là que pour le cas dégradé.
"""

import asyncio

from fastapi import APIRouter

from app.cache import check_redis
from app.database import check_db

router = APIRouter()

PROBE_TIMEOUT_SECONDS = 2.0


async def _probe(func) -> str:
    """Exécute une sonde bloquante de façon bornée, sans jamais lever."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(func), timeout=PROBE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return f"timeout after {PROBE_TIMEOUT_SECONDS}s"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


@router.get("/health")
async def health():
    database, redis_status = await asyncio.gather(_probe(check_db), _probe(check_redis))
    return {
        "status": "ok",
        "database": database,
        "redis": redis_status,
        "version": "1.0.0",
    }

