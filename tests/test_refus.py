"""Tests des motifs de refus de paiement (décision D82).

Ce qui est mesuré, et pourquoi :

  * chaque motif interne d'un refus devient un code court ; « aucun paiement présenté »
    (`absent`) se distingue d'un paiement présenté puis refusé, qui dit ce qui a manqué ;
  * le motif est noté avec l'appel sur les trois chemins : `/validate` (402), l'outil MCP
    (qui répond 200 avec `isError` depuis D78) et l'abonné au plafond ;
  * le tableau de bord compte un refus MCP comme un refus, plus comme une validation servie,
    sépare les prospects des paiements ratés, et alerte quand un paiement présenté est refusé.
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import audience, exploitation
from app.config import get_settings
from app.main import app
from app.models import ApiCall, Customer, CustomerValidation, PageVisit, PaymentTransaction
from app.payment import motif_refus
from app.x402 import PaymentDecision

client = TestClient(app)
MAINTENANT = datetime(2026, 10, 18, 12)
ADRESSE = {"address": {"street": "1600 Amphitheatre Parkway", "city": "Mountain View", "state": "CA", "zip": "94043", "country": "US"}}


@pytest.mark.parametrize("raison, protocole, code", [
    ("aucune preuve de paiement fournie", "none", "absent"),
    ("en-tete X-PAYMENT illisible : Only base64 data is allowed", "x402", "illisible"),
    ("payload.signature ou payload.authorization absent", "x402", "illisible"),
    ("champ _meta['x402/payment'] illisible : objet JSON attendu", "x402", "illisible"),
    ("x402Version non supportee : 9", "x402", "offre"),
    ("reseau non supporte : 'solana'", "x402", "offre"),
    ("offre acceptee differente de l'offre publiee (amount)", "x402", "offre"),
    ("destinataire incorrect : 0xabc != 0xdef", "x402", "offre"),
    ("le transport MCP attend un PaymentPayload x402 v2", "x402", "offre"),
    ("montant insuffisant : 0.000001 USDC < 0.020000 USDC", "x402", "montant"),
    ("autorisation expiree (validBefore=1)", "x402", "delai"),
    ("autorisation pas encore valide (validAfter=9)", "x402", "delai"),
    ("signature non attribuable a authorization.from (0x12)", "x402", "signature"),
    ("autorisation deja reglee (nonce 0xabab...)", "x402", "rejeu"),
    ("facilitateur : paiement invalide (insufficient_funds)", "x402", "facilitateur"),
    ("verification du facilitateur impossible : ConnectTimeout", "x402", "facilitateur"),
    ("reglement refuse par le facilitateur (transaction_failed)", "x402", "reglement"),
    ("reglement annonce reussi sans hash de transaction", "x402", "reglement"),
    ("aucune adresse d'encaissement utilisable configuree", "x402", "config"),
    ("preuve HMAC invalide", "hmac", "preuve"),
])
def test_chaque_motif_devient_un_code_court(raison, protocole, code):
    assert motif_refus(PaymentDecision(paid=False, reason=raison, protocol=protocole)) == code


def _dernier_appel():
    lignes = [l for l in audience._FILE if isinstance(l, ApiCall)]
    return lignes[-1]


def test_le_motif_est_note_sur_validate_et_sur_mcp():
    audience._FILE.clear()
    assert client.post("/validate", json=ADRESSE).status_code == 402
    appel = _dernier_appel()
    assert (appel.status, appel.mode, appel.refus) == (402, "none", "absent")

    client.post("/validate", json=ADRESSE, headers={"X-PAYMENT": "pas-du-base64"})
    assert (_dernier_appel().refus, _dernier_appel().mode) == ("illisible", "x402")

    mcp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "validate_address", "arguments": ADRESSE}})
    assert mcp.status_code == 200 and mcp.json()["result"]["isError"] is True
    appel = _dernier_appel()
    assert (appel.endpoint, appel.status, appel.refus) == ("mcp", 200, "absent"), "un refus MCP v2 doit porter son motif"
    audience._FILE.clear()


@pytest.fixture
def session():
    moteur = create_engine(f"sqlite:///{Path(tempfile.mkdtemp()) / 'r.sqlite'}")
    for modele in (Customer, CustomerValidation, PaymentTransaction, ApiCall, PageVisit):
        modele.__table__.create(moteur)
    with sessionmaker(bind=moteur)() as s:
        yield s


def test_le_tableau_de_bord_separe_prospects_et_paiements_rates(session):
    il_y_a = lambda minutes: MAINTENANT - timedelta(minutes=minutes)  # noqa: E731
    session.add_all([
        ApiCall(endpoint="validate", status=200, mode="x402", duration_ms=300, created_at=il_y_a(5)),
        ApiCall(endpoint="validate", status=402, mode="none", refus="absent", duration_ms=4, created_at=il_y_a(6)),
        ApiCall(endpoint="validate", status=402, duration_ms=4, created_at=il_y_a(7)),  # avant D82 : sans motif
        ApiCall(endpoint="mcp", method="tools/call", status=200, mode="none", refus="absent", duration_ms=3, created_at=il_y_a(8)),
        ApiCall(endpoint="validate", status=402, mode="x402", refus="montant", duration_ms=9, created_at=il_y_a(9)),
        ApiCall(endpoint="mcp", method="tools/call", status=200, mode="x402", refus="signature", duration_ms=9, created_at=il_y_a(10)),
        ApiCall(endpoint="validate", status=402, mode="cle", refus="quota", duration_ms=5, created_at=il_y_a(11)),
    ])
    session.commit()
    d = exploitation.collecter(session, MAINTENANT, "jour", get_settings())
    api = d["api"]
    assert api["statuts"] == {"ok": 1, "paiement": 6}, "le refus MCP (200 + isError) n'est pas une validation servie"
    assert api["taux_reussite"] == 1.0 and api["duree_moyenne_ms"] == 300
    assert api["sans_paiement"] == 3 and dict(api["paiements_refuses"]) == {"montant": 1, "signature": 1}
    assert dict(api["canaux"])["Paiements refusés"] == 2 and dict(api["canaux"])["Abonnés au plafond"] == 1
    alerte = next(a for a in exploitation.alertes(d, MAINTENANT, session) if "paiements présentés" in a)
    assert "montant trop bas (1)" in alerte and "signature invalide (1)" in alerte
    genres = [e for e in d["direct"]["evenements"] if "Paiement refusé" in str(e)]
    assert genres, "le fil en direct doit montrer le paiement refusé et son motif"


def test_la_carte_dit_les_paiements_refuses(session):
    from app.routes.dashboard import _page

    session.add(ApiCall(endpoint="validate", status=402, mode="x402", refus="delai", duration_ms=9, created_at=MAINTENANT - timedelta(minutes=3)))
    session.commit()
    d = exploitation.collecter(session, MAINTENANT, "jour", get_settings())
    page = _page({"ledger": {"available": True}, "windows": {}, "recent": []}, d, {"Base de données": True}, "api")
    assert "Sans paiement</p>" in page
    assert 'class="refus ko"' in page and "1 paiement présenté refusé" in page and "autorisation expirée (1)" in page
    session.query(ApiCall).delete()
    session.commit()
    d = exploitation.collecter(session, MAINTENANT, "jour", get_settings())
    assert "Aucun paiement présenté n'a été refusé." in _page({"ledger": {"available": True}, "windows": {}, "recent": []}, d, {"Base de données": True}, "api")
