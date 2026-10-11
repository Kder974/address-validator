"""GET /metrics/revenue — décision D31 (+ agrégation réutilisée par le rapport).

Pourquoi un module à part plutôt que `/metrics` : `/metrics` (D17) expose des
compteurs **en mémoire**, perdus au redémarrage du processus. Un revenu est une
donnée comptable : elle doit survivre au redémarrage. La source durable est la
table `payment_transactions` (D11), désormais alimentée par *chaque* paiement
accepté (D29) et non plus seulement par les appels portant l'en-tête maison
`X-Transaction-Id`.

Deux choix d'implémentation, tous deux pour rester vérifiable :

* **Agrégation en Python** (et non en SQL `NOW() - INTERVAL`, propre à
  PostgreSQL) : le même code sert l'endpoint, le rapport quotidien et les
  tests ; ces derniers tournent donc sur une base **SQLite jetable**, sans
  exiger un PostgreSQL local.
* **Appel bloquant exécuté hors boucle** avec une borne, exactement comme la
  leçon de D22 sur `/health` : une base lente ne doit pas geler toutes les
  requêtes concurrentes, encore moins la route payante située à côté.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routes.metrics import metrics

router = APIRouter()

WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

LEDGER_TABLE = "payment_transactions"
#: Borne de lecture : la table n'est pas censée grossir vite (0,02 $ par ligne),
#: mais une requête d'exploitation ne doit jamais balayer une table sans limite.
LEDGER_LIMIT = 5000
#: Borne de temps de la requête agrégée (même esprit que D22).
QUERY_TIMEOUT_SECONDS = 3.0


def _as_datetime(value) -> datetime | None:
    """Normalise un `created_at` (datetime Python ou texte SQLite) en UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def fetch_rows(engine=None) -> list[dict]:
    """Lit le registre des paiements, du plus récent au plus ancien.

    Lève si la base est injoignable : c'est `revenue_stats` qui décide comment
    dégrader, pas cette fonction.
    """
    from sqlalchemy import text

    from app import database

    target = engine if engine is not None else database.engine
    sql = (
        f"SELECT tx_hash, amount, address, created_at FROM {LEDGER_TABLE} "
        f"ORDER BY created_at DESC LIMIT {LEDGER_LIMIT}"
    )
    with target.connect() as conn:
        return [dict(row) for row in conn.execute(text(sql)).mappings()]


def aggregate(rows: list[dict], now: datetime) -> dict:
    """Compte les paiements et additionne le revenu (bornes : `now - delta`)."""
    enriched = []
    for row in rows:
        stamp = _as_datetime(row.get("created_at"))
        enriched.append((stamp, row))

    def _bucket(subset) -> dict:
        proofs = {row.get("tx_hash") for _, row in subset if row.get("tx_hash")}
        keys = {row.get("address") for _, row in subset if row.get("address")}
        total = sum(float(row.get("amount") or 0) for _, row in subset)
        return {
            "validations_paid": len(subset),
            "revenue_usdc": round(total, 6),
            "unique_proofs": len(proofs),
            "unique_address_keys": len(keys),
        }

    result = _bucket(enriched)
    stamped = [(stamp, row) for stamp, row in enriched if stamp is not None]
    result["first_observed_at"] = (
        min(stamp for stamp, _ in stamped).isoformat() if stamped else None
    )
    result["last_observed_at"] = (
        max(stamp for stamp, _ in stamped).isoformat() if stamped else None
    )
    result["windows"] = {
        name: _bucket([(s, r) for s, r in stamped if s >= now - delta])
        for name, delta in WINDOWS.items()
    }
    return result


def revenue_stats(
    engine=None, now: datetime | None = None, include_recent: bool = False
) -> dict:
    """Charge utile complète. Ne lève jamais : l'erreur est *dans* la réponse.

    `ledger.database` — plus exactement `ledger.error` — porte l'état réel : un
    tableau de bord qui afficherait 0 sans dire que la base est muette serait
    trompeur. `available: false` + `error` rendent la panne visible.
    """
    settings = get_settings()
    moment = now or datetime.now(timezone.utc)
    payload: dict = {
        "price_per_validation_usdc": settings.x402_price,
        "currency": "USDC",
        "generated_at": moment.isoformat(timespec="seconds"),
        "source": f"postgresql:{LEDGER_TABLE}",
        "ledger": {"available": False, "error": None},
        "windows": {},
        # Compteurs du processus en cours (D17) : utiles entre deux écritures en
        # base, explicitement étiquetés « session » pour ne pas être pris pour
        # un cumul historique.
        "session": {
            key: value
            for key, value in metrics.snapshot().items()
            if key in ("requests_total", "paid_requests", "unpaid_requests")
        },
        "metrics_endpoint": "/metrics",
    }
    try:
        rows = fetch_rows(engine=engine)
    except Exception as exc:  # noqa: BLE001 - toute panne devient une donnee
        payload["ledger"]["error"] = f"{type(exc).__name__}: {exc}"
        return payload

    stats = aggregate(rows, moment)
    payload["windows"] = stats.pop("windows")
    payload["ledger"] = {"available": True, "error": None, **stats}
    if include_recent:
        payload["recent"] = [
            {
                "tx_hash": row.get("tx_hash"),
                "amount_usdc": float(row.get("amount") or 0),
                "address_key": row.get("address"),
                "created_at": (
                    _as_datetime(row.get("created_at")).isoformat()
                    if _as_datetime(row.get("created_at"))
                    else None
                ),
            }
            for row in rows[:10]
        ]
    return payload


async def safe_revenue_stats(include_recent: bool = False) -> dict:
    """Version bornée pour un endpoint HTTP : jamais de requête qui pend."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(revenue_stats, None, None, include_recent),
            timeout=QUERY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        settings = get_settings()
        return {
            "price_per_validation_usdc": settings.x402_price,
            "currency": "USDC",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": f"postgresql:{LEDGER_TABLE}",
            "ledger": {
                "available": False,
                "error": f"timeout after {QUERY_TIMEOUT_SECONDS}s",
            },
            "windows": {},
            "session": {},
            "metrics_endpoint": "/metrics",
        }


@router.get("/metrics/revenue")
async def get_revenue() -> JSONResponse:
    """Revenu cumulé et fenêtres 24 h / 7 j / 30 j (agrégats seuls, sans PII)."""
    payload = await safe_revenue_stats(include_recent=False)
    return JSONResponse(status_code=200, content=payload)
