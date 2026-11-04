"""D102 : une validation lente ne fige plus le reste du site.

Le serveur tourne en un seul processus. Une route `async` qui attend un
fournisseur sans rendre la main bloque toutes les autres requetes : pendant
qu'une adresse attend SmartyStreets, plus aucune page ne repond.
"""

import asyncio
import time

import httpx
import pytest

from app.main import app
from app.routes import validate as route
from app.x402 import PaymentDecision
from tests.test_onchain_payment_route import ADDRESS

LENTEUR = 1.0

REQUETES = {
    "/validate": {"json": {"address": ADDRESS}},
    "/validate-form": {"data": {"address": "1600 amphitheatre pkwy, mountain view ca 94043"}},
}


@pytest.fixture(autouse=True)
def _fournisseur_lent(monkeypatch):
    def lent(raw):
        time.sleep(LENTEUR)
        return {"validated": True, "standardized": dict(ADDRESS), "latitude": None, "longitude": None,
                "deliverability": "residential", "confidence": 1.0, "degraded": False}

    monkeypatch.setattr(route, "validate_full", lent)
    monkeypatch.setattr(route, "get_cached_result", lambda key: None)
    monkeypatch.setattr(route, "store_in_cache", lambda key, value: None)
    monkeypatch.setattr(route, "payment_result",
                        lambda request: PaymentDecision(True, "paye", protocol="x402", transaction="0x" + "ab" * 32))
    monkeypatch.setattr(route, "record_transaction", lambda *args, **kwargs: True)


@pytest.mark.parametrize("chemin", sorted(REQUETES))
def test_une_validation_lente_ne_fige_pas_les_autres_pages(chemin):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Le chrono part avant la requete lente : une boucle figee retarderait aussi
            # le `sleep` ci-dessous, et un chrono lance apres lui ne verrait rien.
            debut = time.monotonic()
            lente = asyncio.create_task(client.post(chemin, **REQUETES[chemin]))
            await asyncio.sleep(0.2)
            page = await client.get("/robots.txt")
            return page, time.monotonic() - debut, await lente

    page, attente, reponse = asyncio.run(scenario())
    assert reponse.status_code == 200, reponse.text[:300]
    assert page.status_code == 200
    assert attente < 0.2 + LENTEUR / 2, f"/robots.txt a attendu {attente:.2f} s derriere {chemin}"
