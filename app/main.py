"""Point d'entrée FastAPI.

Conforme à la spec (Phase 3). Ajout (D16) : un `lifespan` qui tente
`init_db()` au démarrage (création idempotente des tables). L'échec est
non bloquant : l'API démarre et `/health` remonte l'état réel de la base.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db
from app.observability import configure_logging
from app.web import public_headers
from app.routes import (
    accueil,
    connexion,
    dashboard,
    guide,
    health,
    manifest,
    mcp,
    metrics,
    pricing,
    prospection,
    revenue,
    signup,
    validate,
    validate_form,
)

settings = get_settings()

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # D30 : le journal de découverte est ouvert une fois au démarrage quand un
    # chemin est configuré ; sans chemin, rien n'est écrit sur disque.
    configure_logging()
    if init_db():
        logger.info("Schéma de base de données initialisé.")
    else:
        logger.warning(
            "Initialisation de la base impossible (démarrage non bloquant). "
            "Voir GET /health."
        )
    yield


app = FastAPI(
    title="Address Validator API",
    description="API de validation d'adresses de livraison avec paiement à la requête",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(validate.router, tags=["validation"])
app.include_router(health.router, tags=["health"])
app.include_router(manifest.router, tags=["discovery"])
app.include_router(metrics.router, tags=["metrics"])
app.include_router(revenue.router, tags=["metrics"])
app.include_router(dashboard.router, tags=["dashboard"])
# /prospection : CRM de prospection (Tâche 7) — même garde que /dashboard
# (fermé par défaut), base SQLite locale lue en lecture seule par les vues, et
# `503` explicite si la base de prospection n'est pas déployée sur la machine.
app.include_router(prospection.router, tags=["prospection"])
# D37 : serveur MCP (JSON-RPC 2.0 sur POST /mcp) + carte /.well-known/mcp-server.
app.include_router(mcp.router, tags=["mcp"])
# /pricing : page publique de l'offre commerciale (D43). Seule route sans garde
# et sans paiement : elle vend, donc elle doit être lisible par un inconnu, mais
# elle reste autonome (aucune ressource externe) et cachable quelques minutes.
app.include_router(pricing.router, tags=["commercial"])
# /signup et /compte : inscription self-service et espace client (D44). Publiques
# en écriture (l'inscription est ouverte), fermées en lecture (`/compte` exige le
# couple email/mot de passe) et plafonnées par IP (`app/ratelimit.py`).
app.include_router(signup.router, tags=["commercial"])
# /compte/connexion et /compte/deconnexion : la page de connexion et la session
# de l'espace client (D62), à la place de la fenêtre grise du défi Basic.
app.include_router(connexion.router, tags=["commercial"])
# /validate-form : l'essai gratuit public (D46, élément 3 de la campagne). Même
# pipeline que la route payante, mais aucune écriture : ni base des comptes, ni
# registre des paiements. Plafonné par IP (app/ratelimit.py), et le refus parle
# (429 + Retry-After exact + « créez un compte »).
app.include_router(validate_form.router, tags=["commercial"])
# /guide : guide d'integration public (D48, element 4 de la campagne). Public,
# indexable, cachable 5 min, sans secret et sans ressource externe : il repond a
# la question que se pose un client qui vient de recevoir sa cle.
app.include_router(guide.router, tags=["commercial"])
# /en/, /zh/, /hi/, /es/, /ar/ : la vitrine dans les autres langues (D57), chemins
# littéraux ; le français reste sur / (route `root` ci-dessous).
app.include_router(accueil.router, tags=["commercial"])


class _Images(StaticFiles):
    """Photos du site public (D57), servies par l'application : la CSP n'autorise
    que `img-src 'self'`. Même refus du reniflage de type que les pages."""

    def file_response(self, *args, **kwargs):
        reponse = super().file_response(*args, **kwargs)
        reponse.headers["X-Content-Type-Options"] = "nosniff"
        return reponse


app.mount(
    "/images",
    _Images(directory=Path(__file__).resolve().parent / "static" / "images"),
    name="images",
)


@app.get("/")
async def root(request: Request):
    # D53 : un navigateur reçoit la vitrine, un programme garde le JSON d'avant.
    # `Vary: Accept` sur les deux : un cache partagé ne doit jamais servir l'une
    # à la place de l'autre.
    if accueil.veut_html(request.headers.get("accept", "")):
        return HTMLResponse(
            accueil.page(settings),
            headers={**public_headers(form=True), "Vary": "Accept"},
        )
    return JSONResponse(
        {
            "service": "Address Validator API",
            "version": "1.0.0",
            "documentation": "/docs",
            "manifest": "/manifest",
            "health": "/health",
            "mcp": "/mcp",
            "pricing": "/pricing",
        },
        headers={"Vary": "Accept"},
    )
