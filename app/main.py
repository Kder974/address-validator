"""Point d'entrée FastAPI.

Conforme à la spec (Phase 3). Ajout (D16) : un `lifespan` qui tente
`init_db()` au démarrage (création idempotente des tables). L'échec est
non bloquant : l'API démarre et `/health` remonte l'état réel de la base.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.observability import configure_logging
from app.routes import dashboard, health, manifest, mcp, metrics, revenue, validate

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
# D37 : serveur MCP (JSON-RPC 2.0 sur POST /mcp) + carte /.well-known/mcp-server.
app.include_router(mcp.router, tags=["mcp"])


@app.get("/")
async def root():
    return {
        "service": "Address Validator API",
        "version": "1.0.0",
        "documentation": "/docs",
        "manifest": "/manifest",
        "health": "/health",
        "mcp": "/mcp",
    }
