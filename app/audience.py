"""Suivi d'audience et d'usage (D67) : les données du tableau de bord d'exploitation.

Deux tables, en ajout seul, écrites hors du chemin de la réponse :

* `page_visits` : une visite d'une page publique (celles du plan du site, D65) :
  l'adresse de la page, le pays de connexion, le site d'où vient le visiteur (son
  nom de domaine, lu dans `Referer`) et, pour un robot, sa famille (Googlebot,
  GPTBot…) ;
* `api_calls` : un appel de `/validate`, de `/mcp` ou de l'essai gratuit : la
  route, la méthode MCP, le mode de paiement, le statut, la durée, le pays de
  connexion, le pays de l'adresse (deux lettres), cache, résultat dégradé, montant
  encaissé en USDC (réel, hors recette, D40) et le compte quand l'appel passe par
  une clé.

Ce qui n'est **jamais** gardé : l'adresse IP (elle sert à trouver le pays dans la
base DB-IP installée sur le serveur, puis elle est oubliée), l'adresse postale, le
résultat, le `User-Agent` complet, un cookie. Aucun tiers n'est appelé.

Trois règles :

* **Jamais de panne à cause du suivi.** Les lignes attendent en mémoire et un fil
  à part les écrit par lots toutes les `INTERVALLE` secondes ; une base injoignable
  fait perdre les lignes du lot, pas une page. Sans la base des pays (fichier ou
  paquet `maxminddb` absent), le pays vaut `None`.
* **Ce qui est compté est ce qui a été servi** : une visite est une réponse `200`
  en HTML ; un appel garde son vrai statut (`402` compris : un agent qui a trouvé
  le service sans payer).
* **Conservation bornée** : `AUDIENCE_RETENTION_DAYS` jours, purge une fois par jour
  par le même fil.

Les routes complètent la ligne de l'appel en cours par `noter(...)` (cache, mode
de paiement, montant, compte) : une variable de contexte porte la ligne de la
requête jusqu'au code qui sait.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import urlsplit

from fastapi import Request
from sqlalchemy import delete

from app import i18n, seo
from app.config import get_settings
from app.models import ApiCall, PageVisit
from app.observability import client_ip
from app.routes.metrics import metrics

logger = logging.getLogger(__name__)

#: Secondes entre deux écritures en base.
INTERVALLE = 5
# ponytail: file bornée en mémoire, les plus vieilles lignes tombent au-delà de
# 10 000 et un redémarrage perd au plus `INTERVALLE` secondes ; une vraie file si
# le trafic l'exige un jour.
_FILE: deque = deque(maxlen=10_000)
_VERROU = threading.Lock()
_FIL: threading.Thread | None = None
_APPEL: ContextVar[dict | None] = ContextVar("audience_appel", default=None)

#: Les pages comptées : celles du plan du site, dans les six langues.
PAGES = frozenset(i18n.chemin(lang, page) for page in seo.PUBLIQUES for lang in i18n.LANGUES)
#: Les routes d'appel comptées (`POST`) : l'API, le serveur MCP, l'essai gratuit.
APPELS = {
    "/validate": "validate",
    "/mcp": "mcp",
    **{i18n.chemin(lang, "validate-form"): "validate-form" for lang in i18n.LANGUES},
}
#: Ce que les routes peuvent noter sur la ligne de l'appel.
CHAMPS = ("method", "mode", "cache_hit", "degraded", "address_country", "amount_usdc", "customer_id")

#: Familles de robots, cherchées dans cet ordre dans le `User-Agent`.
ROBOTS = (
    ("googlebot", "Googlebot"), ("google-extended", "Google-Extended"), ("bingbot", "Bingbot"),
    ("gptbot", "GPTBot"), ("oai-searchbot", "OAI-SearchBot"), ("chatgpt-user", "ChatGPT-User"),
    ("claudebot", "ClaudeBot"), ("claude-searchbot", "Claude-SearchBot"), ("claude-user", "Claude-User"),
    ("perplexity", "Perplexity"), ("applebot", "Applebot"), ("duckduckbot", "DuckDuckBot"),
    ("yandex", "Yandex"), ("baiduspider", "Baidu"), ("ahrefsbot", "AhrefsBot"),
    ("semrushbot", "SemrushBot"), ("facebookexternalhit", "Facebook"),
)


def _maintenant() -> datetime:
    # Convention des colonnes `TIMESTAMP` du dépôt : UTC, sans fuseau.
    return datetime.now(timezone.utc).replace(tzinfo=None)


@lru_cache(maxsize=1)
def _lecteur(chemin: str, version: int):
    import maxminddb  # facultatif : absent, le pays vaut None

    return maxminddb.open_database(chemin)


def pays(ip: str) -> str | None:
    """Code pays (deux lettres) de l'adresse IP, lu dans la base DB-IP du serveur."""
    chemin = get_settings().geoip_db_path
    try:
        # La date du fichier fait partie de la clé : la base du mois remplacée est relue.
        fiche = _lecteur(chemin, os.stat(chemin).st_mtime_ns).get(ip)
    except Exception:  # noqa: BLE001 - fichier ou paquet absent, IP illisible : pas de pays
        return None
    return code_pays(((fiche or {}).get("country") or {}).get("iso_code"))


def code_pays(valeur) -> str | None:
    texte = str(valeur or "").strip().upper()
    return texte if len(texte) == 2 and texte.isalpha() else None


def robot(agent: str) -> str | None:
    """Famille du robot (« Googlebot », « GPTBot »…), « Autre robot », ou `None` pour un humain."""
    bas = (agent or "").lower()
    for motif, nom in ROBOTS:
        if motif in bas:
            return nom
    if seo.est_robot(agent):
        return "Autre robot"
    # D71 : tous les navigateurs s'annoncent « Mozilla/5.0 » ; curl, python-requests ou un agent vide
    # sont des programmes, pas des visiteurs.
    return None if "mozilla" in bas else "Programme"


def source(referer: str) -> str | None:
    """Le site d'où vient le visiteur : son nom de domaine, rien d'autre de l'adresse."""
    try:
        hote = (urlsplit(referer or "").hostname or "").lower()
    except ValueError:
        return None
    return hote.removeprefix("www.")[:100] or None


def noter(**champs) -> None:
    """Complète la ligne de l'appel en cours ; sans effet hors d'un appel suivi."""
    appel = _APPEL.get()
    if appel is not None:
        appel.update(champs)


async def suivre(request: Request, call_next):
    """Middleware : une ligne par visite d'une page publique et par appel d'API."""
    chemin = request.url.path
    route = APPELS.get(chemin) if request.method == "POST" else None
    if route is None and not (request.method == "GET" and chemin in PAGES):
        return await call_next(request)

    appel: dict = {"mode": "essai"} if route == "validate-form" else {}
    jeton = _APPEL.set(appel)
    debut = time.perf_counter()
    try:
        reponse = await call_next(request)
    except Exception:
        # D71 : une route qui plante donne un 500 plus loin ; la ligne le compte, sinon l'alerte
        # « erreurs serveur » ne verrait jamais les vraies pannes.
        if route is not None:
            _noter_appel(request, route, appel, 500, debut)
        raise
    finally:
        _APPEL.reset(jeton)

    if route is None:
        if reponse.status_code == 200 and reponse.headers.get("content-type", "").startswith("text/html"):
            # Sous `Referrer-Policy: no-referrer`, la navigation interne n'a pas de `Referer` ;
            # `Sec-Fetch-Site: same-origin` la reconnaît quand même (D71).
            origine = source(request.headers.get("referer", ""))
            if origine is None and request.headers.get("sec-fetch-site") == "same-origin":
                origine = _hote_propre()
            _mettre(PageVisit(
                created_at=_maintenant(), path=chemin, country=pays(client_ip(request)),
                referrer_host=origine, robot=robot(request.headers.get("user-agent", "")),
            ))
    else:
        _noter_appel(request, route, appel, reponse.status_code, debut)
    return reponse


def _hote_propre() -> str:
    return get_settings().public_base_url.split("://", 1)[-1].split("/", 1)[0].lower().removeprefix("www.")


def _noter_appel(request: Request, route: str, appel: dict, statut: int, debut: float) -> None:
    champs = {cle: appel[cle] for cle in CHAMPS if cle in appel}
    champs["address_country"] = code_pays(champs.get("address_country"))
    _mettre(ApiCall(
        created_at=_maintenant(), endpoint=route, status=statut,
        duration_ms=round((time.perf_counter() - debut) * 1000), country=pays(client_ip(request)), **champs,
    ))


def _mettre(ligne) -> None:
    _FILE.append(ligne)
    _demarrer()


def _demarrer() -> None:
    global _FIL
    if _FIL is not None and _FIL.is_alive():
        return
    with _VERROU:
        if _FIL is None or not _FIL.is_alive():
            _FIL = threading.Thread(target=_boucle, name="audience", daemon=True)
            _FIL.start()


def _boucle() -> None:
    derniere_purge = None
    while True:
        time.sleep(INTERVALLE)
        vider()
        if derniere_purge is None or time.monotonic() - derniere_purge > 86_400:
            if purger():
                derniere_purge = time.monotonic()


def _fabrique():
    from app.database import SessionLocal

    return SessionLocal


def vider(fabrique=None) -> int:
    """Écrit les lignes en attente en un seul lot ; renvoie le nombre écrit (0 si la base refuse)."""
    lot = []
    while _FILE:
        lot.append(_FILE.popleft())
    if not lot:
        return 0
    try:
        with (fabrique or _fabrique())() as session:
            session.add_all(lot)
            session.commit()
        return len(lot)
    except Exception as exc:  # noqa: BLE001 - le suivi ne casse jamais rien
        metrics.incr("audience_lignes_perdues", len(lot))
        logger.warning("audience : %d lignes perdues (%s)", len(lot), exc)
        return 0


def purger(fabrique=None, maintenant: datetime | None = None) -> bool:
    """Efface les lignes plus vieilles que `AUDIENCE_RETENTION_DAYS` jours."""
    limite = (maintenant or _maintenant()) - timedelta(days=get_settings().audience_retention_days)
    try:
        with (fabrique or _fabrique())() as session:
            for modele in (PageVisit, ApiCall):
                session.execute(delete(modele).where(modele.created_at < limite))
            session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("audience : purge impossible (%s)", exc)
        return False
