"""Tests du mode clé API sur `POST /validate` (décision D45).

Ce que ces tests mesurent, et pourquoi :

  * une clé valide sert la validation, **débite le compte** et n'écrit **rien** au
    registre USDC : un abonnement n'est pas un encaissement à l'appel, et l'y
    inscrire ferait mentir le revenu publié (D40) ;
  * une clé inconnue vaut `401` **sans calculer la validation** : aucun appel au
    fournisseur n'a lieu pour un compte non autorisé ;
  * un essai terminé vaut `403`, un quota du mois consommé vaut `402` — deux
    codes différents parce que ce sont deux situations différentes ;
  * sans clé ni paiement, la réponse reste le `402` x402 d'avant : le mode clé
    n'a rien retiré aux agents autonomes ;
  * quand les deux en-têtes sont présentés, **la clé tranche** ;
  * une base de comptes injoignable vaut `503`, **jamais** `402` : une clé qui
    n'a pas pu être vérifiée n'est pas une clé refusée.

La base est un SQLite jetable et le fournisseur est coupé : ce qui est mesuré est
la porte d'entrée, pas la disponibilité d'Internet.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app import accounts
from app.main import app
from app.models import Customer, CustomerValidation
from app.routes import validate as validate_route
from app.routes.metrics import metrics

client = TestClient(app)

MOT_DE_PASSE = "un-mot-de-passe-solide-12"
CORPS = {
    "address": {
        "street": "1 Rue des Tests",
        "city": "Saint-Denis",
        "state": "RE",
        "zip": "97400",
        "country": "RE",
    },
}
RESULTAT = {
    "validated": True,
    "standardized": {
        "line1": "1 Rue des Tests",
        "line2": "",
        "city": "Saint-Denis",
        "state": "RE",
        "zip": "97400",
        "country": "RE",
    },
    "latitude": -20.88,
    "longitude": 55.45,
    "deliverability": "residential",
    "confidence": 0.99,
    "degraded": False,
}


@pytest.fixture
def comptes(tmp_path, monkeypatch):
    """Base jetable, fournisseur et cache coupés : la route seule est mesurée."""
    moteur = create_engine(f"sqlite:///{tmp_path / 'cles.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)

    appels: list[dict] = []

    def _valide(address: dict) -> dict:
        appels.append(address)
        return dict(RESULTAT)

    monkeypatch.setattr(validate_route, "SessionLocal", fabrique)
    monkeypatch.setattr(validate_route, "validate_full", _valide)
    monkeypatch.setattr(validate_route, "get_cached_result", lambda key: None)
    monkeypatch.setattr(validate_route, "store_in_cache", lambda key, value: None)
    return fabrique, appels


def _compte(fabrique, *, plan="trial", email="client@example.re", fin_essai=None):
    """Compte réel en base, et sa clé en clair (la seule fois où elle existe)."""
    with fabrique() as session:
        compte, cle = accounts.create_customer(
            session,
            email=email,
            password=MOT_DE_PASSE,
            shop_name="Boutique Test",
            platform="shopify",
            plan=plan,
        )
        if fin_essai is not None:
            compte.trial_ends_at = fin_essai
            session.commit()
        return cle, compte.id


def _appel(cle: str):
    return client.post("/validate", json=CORPS, headers={"X-API-Key": cle})


def test_cle_valide_sert_et_debite_le_compte(comptes, ledger):
    fabrique, appels = comptes
    cle, identifiant = _compte(fabrique)
    avant = metrics.snapshot()

    reponse = _appel(cle)

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["result"]["validated"] is True
    # Aucun reglement on-chain : cet appel est couvert par l'abonnement.
    assert corps["transaction_id"] is None
    assert corps["price_paid"] == 0.0
    assert reponse.headers["x-payment-mode"] == "api_key"
    assert reponse.headers["x-plan"] == "trial"
    assert reponse.headers["x-quota-remaining"] == "99"
    # Rien au registre USDC : le revenu publie (D40) ne compte pas un abonnement
    # comme un encaissement, et l'y inscrire le ferait mentir.
    assert ledger == []

    assert len(appels) == 1
    with fabrique() as session:
        compte = session.get(Customer, identifiant)
        assert accounts.consumption(session, compte)["validations"] == 1

    apres = metrics.snapshot()
    assert apres["api_key_requests"] - avant["api_key_requests"] == 1
    assert apres["api_key_validations"] - avant["api_key_validations"] == 1


def test_cle_inconnue_refusee_sans_calculer(comptes, ledger):
    fabrique, appels = comptes
    _compte(fabrique)
    avant = metrics.snapshot()

    reponse = _appel("sk_live_" + "z" * 32)

    assert reponse.status_code == 401
    corps = reponse.json()
    assert corps["reason"] == "cle_inconnue" and corps["mode"] == "api_key"
    # Le corps ne rend que le prefixe (ce que `/compte` affiche deja) : jamais la
    # valeur presentee.
    assert corps["prefix"] == "sk_live_zzzz" and len(corps["prefix"]) == 12
    # Aucun compte connu : ni calcul, ni ecriture, ni encaissement.
    assert appels == [] and ledger == []
    assert metrics.snapshot()["api_key_denied"] - avant["api_key_denied"] == 1


def test_essai_termine_vaut_403(comptes, ledger):
    fabrique, appels = comptes
    cle, _ = _compte(
        fabrique,
        email="expire@example.re",
        fin_essai=datetime.now(timezone.utc) - timedelta(days=1),
    )

    reponse = _appel(cle)

    assert reponse.status_code == 403
    corps = reponse.json()
    assert corps["reason"] == "compte_expire" and corps["plan"] == "trial"
    assert corps["upgrade"].startswith("https://")
    assert appels == [] and ledger == []


def test_quota_du_mois_atteint_vaut_402(comptes, ledger):
    fabrique, appels = comptes
    cle, identifiant = _compte(fabrique, email="quota@example.re")
    with fabrique() as session:
        for _ in range(accounts.quota_for("trial")):
            accounts.record_validation(session, identifiant, "cle", True)

    reponse = _appel(cle)

    assert reponse.status_code == 402
    corps = reponse.json()
    assert corps["reason"] == "quota_atteint"
    assert corps["quota"] == 100 and corps["validations_this_month"] == 100
    # 402 pour un plafond d'usage, 401 pour une cle inconnue : deux situations,
    # deux codes — sinon le client cherche une faute de cle qui n'existe pas.
    assert _appel("sk_live_" + "z" * 32).status_code == 401
    assert appels == [] and ledger == []


def test_un_plan_payant_reste_servi_apres_la_date_d_essai(comptes, ledger):
    fabrique, _ = comptes
    cle, _ = _compte(
        fabrique,
        plan="starter",
        email="payant@example.re",
        fin_essai=datetime.now(timezone.utc) - timedelta(days=1),
    )

    reponse = _appel(cle)

    assert reponse.status_code == 200
    assert reponse.headers["x-plan"] == "starter"
    assert ledger == []


def test_sans_aucun_entete_le_402_x402_demeure(comptes, ledger):
    """Le mode cle n'a rien retire aux agents autonomes."""
    fabrique, appels = comptes
    _compte(fabrique)

    reponse = client.post("/validate", json=CORPS)

    assert reponse.status_code == 402
    corps = reponse.json()
    assert corps["x402Version"] == 1 and corps["accepts"]
    assert "mode" not in corps
    assert appels == [] and ledger == []


def test_la_cle_a_la_priorite_sur_le_paiement(comptes, monkeypatch, ledger):
    """Les deux en-tetes ensemble : la cle tranche, le paiement n'est pas examine."""
    fabrique, _ = comptes
    cle, _ = _compte(fabrique, email="priorite@example.re")

    def _interdit(request):  # pragma: no cover - ne doit jamais etre appele
        raise AssertionError(
            "le paiement est examine alors qu'une cle API est presentee : la "
            "priorite annoncee n'est plus respectee"
        )

    monkeypatch.setattr(validate_route, "payment_result", _interdit)
    reponse = client.post(
        "/validate",
        json=CORPS,
        headers={"X-API-Key": cle, "X-PAYMENT": "nimportequoi"},
    )

    assert reponse.status_code == 200
    assert reponse.headers["x-payment-mode"] == "api_key"
    assert ledger == []


def test_base_des_comptes_injoignable_vaut_503(comptes, monkeypatch, ledger):
    """Une cle non verifiee n'est pas une cle refusee : jamais un 402."""
    fabrique, appels = comptes
    cle, _ = _compte(fabrique, email="panne@example.re")
    avant = metrics.snapshot()

    def _en_panne():
        raise OperationalError("SELECT 1", {}, Exception("panne (tests D45)"))

    monkeypatch.setattr(validate_route, "SessionLocal", _en_panne)
    reponse = _appel(cle)

    assert reponse.status_code == 503
    corps = reponse.json()
    assert corps["mode"] == "api_key" and "accepts" not in corps
    assert appels == [] and ledger == []
    assert metrics.snapshot()["api_key_unavailable"] - avant["api_key_unavailable"] == 1
