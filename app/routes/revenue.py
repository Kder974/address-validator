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

Ajout D40 — ce qui est encaissé n'est pas ce qui a été essayé. Une transaction
marquée `test_mode` (recette de l'exploitant) reste au registre : la tracer est
utile. Mais elle est exclue des nombres publiables, préfixés `real_`. Les
`total_*` incluent tout, les `test_*` chiffrent l'écart, et les alias de D31
(`validations_paid`, `revenue_usdc`) valent le réel : un lecteur qui ne connaît
pas D40 ne peut donc pas lire un chiffre d'affaires fictif.
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
    # `test_mode` est lu avec les autres colonnes (D40) : l'agrégat doit voir la
    # distinction, pas la deviner.
    sql = (
        f"SELECT tx_hash, amount, address, created_at, test_mode FROM {LEDGER_TABLE} "
        f"ORDER BY created_at DESC LIMIT {LEDGER_LIMIT}"
    )
    with target.connect() as conn:
        return [dict(row) for row in conn.execute(text(sql)).mappings()]


#: Valeurs textuelles considérées comme vraies par `_is_test`. PostgreSQL rend
#: un booléen, SQLite peut rendre 0/1 ou 'true' : la lecture doit rester la même
#: quel que soit le moteur (tests sur SQLite, production sur PostgreSQL).
VALEURS_VRAIES = {"1", "t", "true", "vrai", "oui", "yes", "y"}


def _is_test(row: dict) -> bool:
    """Vrai si la ligne est marquée comme essai de recette (D40).

    Une colonne absente ou NULL vaut « non marquée » : une ancienne ligne, ou
    une base antérieure à la migration, ne doit pas basculer en recette par
    accident — c'est `scripts/migrate_test_mode.py` qui marque, explicitement.
    """
    valeur = row.get("test_mode")
    if valeur is None:
        return False
    if isinstance(valeur, bool):
        return valeur
    if isinstance(valeur, (int, float)):
        return valeur != 0
    return str(valeur).strip().lower() in VALEURS_VRAIES


def aggregate(rows: list[dict], now: datetime) -> dict:
    """Compte les paiements et additionne le revenu (bornes : `now - delta`).

    Chaque compteur existe en trois exemplaires (D40) : `real_*` hors recette,
    `total_*` tout le registre, `test_*` l'écart. Les alias `validations_paid` et
    `revenue_usdc` de D31 valent le réel.
    """
    enriched = []
    for row in rows:
        stamp = _as_datetime(row.get("created_at"))
        enriched.append((stamp, row))

    def _bucket(subset) -> dict:
        # D40 : deux populations dans une même table. `real` est publiable (un
        # client a payé), `total` est le registre entier.
        real = [(stamp, row) for stamp, row in subset if not _is_test(row)]
        essais = [(stamp, row) for stamp, row in subset if _is_test(row)]

        def _somme(pairs) -> float:
            return round(sum(float(row.get("amount") or 0) for _, row in pairs), 6)

        return {
            # Nombres publiables : la recette de l'exploitant est exclue.
            "real_validations_paid": len(real),
            "real_revenue_usdc": _somme(real),
            # Registre entier, recette comprise : pour l'audit, pas la vitrine.
            "total_validations_paid": len(subset),
            "total_revenue_usdc": _somme(subset),
            # Ce qui est exclu, chiffré : `real_*` + `test_*` = `total_*`.
            "test_validations_paid": len(essais),
            "test_revenue_usdc": _somme(essais),
            # Alias historiques (D31) : ils pointent sur le réel — seule lecture
            # qui ne trompe pas un consommateur qui ignore D40.
            "validations_paid": len(real),
            "revenue_usdc": _somme(real),
            # Preuves et clés restent comptées sur tout le registre, comme avant
            # D40 : aucun changement silencieux de ces deux compteurs.
            "unique_proofs": len(
                {row.get("tx_hash") for _, row in subset if row.get("tx_hash")}
            ),
            "unique_address_keys": len(
                {row.get("address") for _, row in subset if row.get("address")}
            ),
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
        # La règle de lecture voyage avec les chiffres : un écart entre `real_*`
        # et `total_*` doit toujours pouvoir s'expliquer, jamais se deviner (D40).
        "test_mode": {
            "policy": (
                "real_* exclut les paiements marques test_mode (recette de "
                "l'exploitant) ; total_* les inclut ; test_* chiffre l'ecart."
            ),
            "recording_now": bool(settings.test_mode),
            "cutoff_utc": settings.test_mode_cutoff_utc,
        },
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
                "test_mode": _is_test(row),
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
            "test_mode": {
                "policy": (
                    "real_* exclut les paiements marques test_mode (recette de "
                    "l'exploitant) ; total_* les inclut ; test_* chiffre l'ecart."
                ),
                "recording_now": bool(settings.test_mode),
                "cutoff_utc": settings.test_mode_cutoff_utc,
            },
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
