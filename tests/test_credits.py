"""Tests des crédits prépayés (D88) : achat idempotent, quota, webhook, sécurité.

La base est un SQLite jetable : les deux tables D88 n'ont aucun type propriétaire
PostgreSQL et peuvent être créées telles quelles, comme pour les tests de comptes
(D44). Aucun appel réseau : Stripe est remplacé par une fonction locale.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.responses import Response

from app import credits
from app.models import (
    CreditBalance,
    CreditTransaction,
    Customer,
    CustomerValidation,
)

SECRET_WEBHOOK = "whsec_TEST_VOTRE_SECRET"


def _entete_signature(payload: bytes, secret: str = SECRET_WEBHOOK, horodatage: int | None = None) -> str:
    """Construit un en-tête `Stripe-Signature` valide (même calcul que Stripe)."""
    moment = int(horodatage if horodatage is not None else time.time())
    signe = hmac.new(
        secret.encode(), f"{moment}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={moment},v1={signe}"


def _evenement_achat(
    customer_id: int,
    pack_id: str = "pack_5",
    *,
    identifiant: str = "evt_TEST_1",
    session_id: str = "cs_TEST_1",
    paiement: str = "pi_TEST_1",
    montant: int | None = None,
    devise: str = "eur",
    statut: str = "paid",
    type_evenement: str = "checkout.session.completed",
) -> dict:
    pack = credits.PACKS_PAR_ID[pack_id]
    return {
        "id": identifiant,
        "type": type_evenement,
        "data": {
            "object": {
                "id": session_id,
                "payment_status": statut,
                "payment_intent": paiement,
                "amount_total": pack["prix_eur_cents"] if montant is None else montant,
                "currency": devise,
                "metadata": {
                    "customer_id": str(customer_id),
                    "pack_id": pack_id,
                    "validations": str(pack["validations"]),
                },
            }
        },
    }


@pytest.fixture
def session(tmp_path):
    """Session SQLite jetable avec les tables nécessaires aux crédits."""
    moteur = create_engine(f"sqlite:///{tmp_path / 'credits.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    CreditBalance.__table__.create(moteur)
    CreditTransaction.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)
    yield fabrique()
    fabrique().close()


@pytest.fixture
def compte(session):
    """Un compte client minimal, sur le plan d'essai."""
    client = Customer(
        email="test@exemple.re",
        password_hash="$2b$12$empreinte-inutilisable-dans-ce-test",
        shop_name="Boutique test",
        platform="autre",
        api_key_hash="a" * 64,
        api_key_prefix="sk_live_abcd",
        plan="trial",
    )
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #


def test_catalogue_cinq_packs_prix_entiers_et_ids_uniques():
    assert len(credits.PACKS) == 5
    identifiants = [pack["id"] for pack in credits.PACKS]
    assert identifiants == ["pack_5", "pack_25", "pack_100", "pack_500", "pack_2000"]
    assert len(set(identifiants)) == 5
    for pack in credits.packs_publics():
        assert isinstance(pack["prix_eur_cents"], int)
        assert pack["prix_eur_cents"] > 0
        assert pack["validations"] > 0
        # Le public ne voit jamais l'identifiant de prix Stripe (c'est serveur).
        assert "stripe" not in json.dumps(pack).lower()
    assert credits.pack("pack_100")["validations"] == 100
    with pytest.raises(credits.PaiementInvalide):
        credits.pack("pack_magique")


def test_schema_absent_garde_le_comportement_d_avant(session, tmp_path):
    """Une base sans les tables D88 (tests/avant migration) ne devient pas une panne."""
    ancien = create_engine(f"sqlite:///{tmp_path / 'sans-credits.sqlite'}")
    Customer.__table__.create(ancien)
    ancien_session = sessionmaker(bind=ancien)()
    assert credits.credits_disponibles(ancien_session) is False
    # Avec les tables (fixture), la fonctionnalité est disponible.
    assert credits.credits_disponibles(session) is True


# --------------------------------------------------------------------------- #
# Achat et idempotence
# --------------------------------------------------------------------------- #


def test_achat_credite_et_rejeu_ne_credite_pas_deux_fois(session, compte):
    code, _ = credits.crediter_achat(
        session,
        customer_id=compte.id,
        pack_id="pack_5",
        montant_cents=149,
        devise="eur",
        stripe_event_id="evt_A",
        stripe_session_id="cs_A",
        payment_intent_id="pi_A",
    )
    assert code == "credite"
    assert credits.solde(session, compte.id) == 5

    # Stripe peut réessayer le même événement : on ne crédite qu'une fois.
    code, _ = credits.crediter_achat(
        session,
        customer_id=compte.id,
        pack_id="pack_5",
        montant_cents=149,
        devise="eur",
        stripe_event_id="evt_A",
        stripe_session_id="cs_A",
        payment_intent_id="pi_A",
    )
    assert code == "deja_traite"
    assert credits.solde(session, compte.id) == 5

    mouvements = session.query(CreditTransaction).filter_by(customer_id=compte.id).all()
    assert [m.reason for m in mouvements] == ["achat"]


def test_montant_ou_devise_falsifies_ne_creditant_rien(session, compte):
    code, message = credits.crediter_achat(
        session,
        customer_id=compte.id,
        pack_id="pack_25",
        montant_cents=1,  # le client ne fixe pas le prix
        devise="eur",
        stripe_event_id="evt_low",
    )
    assert code == "montant_invalide"
    assert "montant" in message
    assert credits.solde(session, compte.id) == 0

    code, _ = credits.crediter_achat(
        session,
        customer_id=compte.id,
        pack_id="pack_25",
        montant_cents=590,
        devise="usd",
        stripe_event_id="evt_usd",
    )
    assert code == "devise_invalide"
    assert credits.solde(session, compte.id) == 0


# --------------------------------------------------------------------------- #
# Débit, compensation, remboursement
# --------------------------------------------------------------------------- #


def test_debit_atomique_jusqu_a_zero_puis_refus(session, compte):
    credits.crediter_achat(
        session,
        customer_id=compte.id,
        pack_id="pack_5",
        montant_cents=149,
        devise="eur",
        stripe_event_id="evt_debit",
        payment_intent_id="pi_debit",
    )
    assert credits.debiter_credit(session, compte.id) is True
    assert credits.debiter_credit(session, compte.id) is True
    assert credits.solde(session, compte.id) == 3
    for _ in range(3):
        assert credits.debiter_credit(session, compte.id) is True
    assert credits.solde(session, compte.id) == 0
    assert credits.debiter_credit(session, compte.id) is False
    assert credits.solde(session, compte.id) == 0


def test_compensation_rend_le_credit_reserve(session, compte):
    credits.crediter_achat(
        session,
        customer_id=compte.id,
        pack_id="pack_5",
        montant_cents=149,
        devise="eur",
        stripe_event_id="evt_correction",
        payment_intent_id="pi_correction",
    )
    assert credits.debiter_credit(session, compte.id)
    credits.crediter_correction(session, compte.id)
    assert credits.solde(session, compte.id) == 5
    assert [m.reason for m in session.query(CreditTransaction).all()] == [
        "achat",
        "validation",
        "correction",
    ]


def test_remboursement_apres_consommation_peut_rendre_le_solde_negatif(session, compte):
    credits.crediter_achat(
        session,
        customer_id=compte.id,
        pack_id="pack_5",
        montant_cents=149,
        devise="eur",
        stripe_event_id="evt_refund",
        payment_intent_id="pi_refund",
    )
    assert credits.debiter_credit(session, compte.id)
    code, _ = credits.rembourser(
        session,
        customer_id=compte.id,
        credits_rendus=5,
        montant_cents=149,
        stripe_event_id="evt_refund_1",
        pack_id="pack_5",
    )
    assert code == "rembourse"
    assert credits.solde(session, compte.id) == -1


# --------------------------------------------------------------------------- #
# Webhook : signature et traitement
# --------------------------------------------------------------------------- #


def test_signature_webhook_valide_invalide_expiree(monkeypatch):
    monkeypatch.setattr(credits.settings, "stripe_webhook_secret", SECRET_WEBHOOK)
    payload = b'{"id":"evt_1","type":"checkout.session.completed","data":{"object":{}}}'
    assert credits.verifier_signature(payload, _entete_signature(payload)) is True
    # Mauvais secret, en-tête absent, horodatage trop vieux : refus.
    assert credits.verifier_signature(payload, _entete_signature(payload, "mauvais")) is False
    assert credits.verifier_signature(payload, None) is False
    assert credits.verifier_signature(payload, _entete_signature(payload, horodatage=int(time.time()) - 3600)) is False
    # Le corps modifié ne passe plus.
    assert credits.verifier_signature(payload + b" ", _entete_signature(payload)) is False


def test_webhook_credite_puis_ignore_le_rejeu(session, compte, monkeypatch):
    monkeypatch.setattr(credits.settings, "stripe_webhook_secret", SECRET_WEBHOOK)
    evenement = _evenement_achat(compte.id, identifiant="evt_webhook")
    payload = json.dumps(evenement).encode()
    entete = _entete_signature(payload)

    statut, corps = credits.traiter_webhook(session, payload, entete)
    assert statut == 200 and corps["resultat"] == "credite"
    assert credits.solde(session, compte.id) == 5

    statut, corps = credits.traiter_webhook(session, payload, entete)
    assert statut == 200 and corps["resultat"] == "deja_traite"
    assert credits.solde(session, compte.id) == 5

    # Signature invalide : 400, aucun crédit.
    statut, corps = credits.traiter_webhook(session, payload, "t=1,v1=0" * 1)
    assert statut == 400 and corps["error"] == "signature_invalide"
    assert credits.solde(session, compte.id) == 5


def test_webhook_rembourse_un_achat_connu(session, compte, monkeypatch):
    monkeypatch.setattr(credits.settings, "stripe_webhook_secret", SECRET_WEBHOOK)
    achat = _evenement_achat(compte.id, identifiant="evt_achat_refund", paiement="pi_refund_webhook")
    payload = json.dumps(achat).encode()
    credits.traiter_webhook(session, payload, _entete_signature(payload))

    remboursement = {
        "id": "evt_remboursement",
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": "pi_refund_webhook", "amount_refunded": 149, "currency": "eur"}},
    }
    payload = json.dumps(remboursement).encode()
    statut, corps = credits.traiter_webhook(session, payload, _entete_signature(payload))
    assert statut == 200 and corps["resultat"] == "rembourse"
    assert credits.solde(session, compte.id) == 0

    # Remboursement inconnu du grand livre : ignoré, rien ne bouge.
    inconnu = dict(remboursement, id="evt_remboursement_inconnu", data={"object": {"payment_intent": "pi_inconnu", "amount_refunded": 100}})
    payload = json.dumps(inconnu).encode()
    statut, corps = credits.traiter_webhook(session, payload, _entete_signature(payload))
    assert statut == 200 and corps["resultat"] == "ignore"


# --------------------------------------------------------------------------- #
# Stripe Checkout (le HTML/boutons sont de Claude ; ici le contrat serveur)
# --------------------------------------------------------------------------- #


class _ReponseFactice:
    status_code = 200

    def __init__(self, document):
        self._document = document

    def json(self):
        return self._document


def test_checkout_refuse_sans_configuration(monkeypatch):
    monkeypatch.setattr(credits.settings, "stripe_secret_key", "")
    with pytest.raises(credits.StripeIndisponible):
        credits.creer_session_checkout(
            client_email="client@exemple.re",
            customer_id=1,
            pack_id="pack_5",
            base_url="https://kder-ai.com",
        )


def test_checkout_utilise_le_prix_serveur_et_les_metadonnees(monkeypatch):
    monkeypatch.setattr(credits.settings, "stripe_secret_key", "sk_test_VOTRE_CLE")
    monkeypatch.setattr(credits.settings, "stripe_price_pack_25", "price_TEST_PACK_25")
    appels = {}

    def faux_poster(donnees):
        appels["donnees"] = dict(donnees)
        return _ReponseFactice({"id": "cs_TEST_1", "url": "https://checkout.stripe.test/cs_TEST_1"})

    monkeypatch.setattr(credits, "_poster_stripe", faux_poster)
    resultat = credits.creer_session_checkout(
        client_email="client@exemple.re",
        customer_id=42,
        pack_id="pack_25",
        base_url="https://kder-ai.com",
    )
    assert resultat["checkout_url"].startswith("https://checkout.stripe.test/")
    assert resultat["session_id"] == "cs_TEST_1"
    assert resultat["amount_cents"] == 590
    donnees = appels["donnees"]
    assert donnees["line_items[0][price]"] == "price_TEST_PACK_25"
    assert donnees["metadata[customer_id]"] == "42"
    assert donnees["metadata[pack_id]"] == "pack_25"
    assert donnees["mode"] == "payment"
    assert "success_url" in donnees


# --------------------------------------------------------------------------- #
# Intégration quota : le crédit sert l'appel, puis épuisé -> 402
# --------------------------------------------------------------------------- #


def test_quota_epuise_puis_credit_puis_402(session, compte, monkeypatch):
    from app.routes import validate as route_validate

    # Un client au quota épuisé : on l'affirme pour isoler la règle D88.
    monkeypatch.setattr(
        route_validate.accounts,
        "authorize_api_key",
        lambda _session, _cle: (compte, route_validate.accounts.REFUS_QUOTA_ATTEINT),
    )
    monkeypatch.setattr(route_validate, "_session", lambda: session)
    monkeypatch.setattr(
        route_validate,
        "perform_validation",
        lambda raw: (
            {"validated": True, "standardized": raw, "latitude": None, "longitude": None,
             "deliverability": "residential", "confidence": 1.0, "degraded": False},
            "cle-cache",
        ),
    )
    monkeypatch.setattr(route_validate.audience, "noter", lambda **_: None)

    # Sans crédit : 402 quota_atteint (comportement existant inchangé).
    reponse = Response()
    resultat = route_validate.serve_with_api_key({"street": "1 Main St"}, reponse, "sk_live_test")
    assert resultat.status_code == 402
    assert json.loads(resultat.body)["reason"] == "quota_atteint"

    # Un achat crédite 5 : l'appel est servi, et le solde baisse.
    credits.crediter_achat(
        session,
        customer_id=compte.id,
        pack_id="pack_5",
        montant_cents=149,
        devise="eur",
        stripe_event_id="evt_quota",
        payment_intent_id="pi_quota",
    )
    reponse = Response()
    resultat = route_validate.serve_with_api_key({"street": "1 Main St"}, reponse, "sk_live_test")
    # Le succès rend le modèle Pydantic de la route (le FastAPI l'enveloppe) ;
    # le refus de quota, lui, rend un JSONResponse.
    from app.schemas import ValidationResponse

    assert isinstance(resultat, ValidationResponse)
    assert resultat.result.validated is True
    assert reponse.headers["X-Credits-Remaining"] == "4"
    assert reponse.headers[route_validate.PAYMENT_MODE_HEADER] == "credit"
    assert credits.solde(session, compte.id) == 4

    # Épuisement : le refus de quota redevient la réponse.
    credits.debiter_credit(session, compte.id)
    credits.debiter_credit(session, compte.id)
    credits.debiter_credit(session, compte.id)
    credits.debiter_credit(session, compte.id)
    reponse = Response()
    resultat = route_validate.serve_with_api_key({"street": "1 Main St"}, reponse, "sk_live_test")
    assert resultat.status_code == 402
