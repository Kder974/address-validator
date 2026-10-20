"""Crédits prépayés achetés par carte (Stripe) — backend de D88.

Ce module ne contient **aucune** page, aucun bouton et aucun HTML : c'est la
couche métier que l'interface humaine (page `/compte/credits`, boutons Stripe
Checkout) et la route `/stripe/webhook` utilisent. Il est importé par
`app/routes/validate.py` pour servir un appel quand le quota mensuel est épuisé.

Règles arrêtées (voir `docs/commercial/INTERFACE-HUMAINE.md` et DECISIONS.md D88) :

* **deux tables** : `credit_balance` (solde courant, une ligne par compte,
  mise à jour atomique) et `credit_transactions` (grand livre, jamais réécrit) ;
* **source de vérité = webhook signé** : un achat n'est crédité que sur un
  événement Stripe dont la signature est vérifiée ; `stripe_event_id` et
  `payment_intent_id` sont UNIQUE en base, donc un rejeu ne crédite pas deux
  fois (Stripe peut réessayer) ;
* **montant recalculé côté serveur** : le pack est retrouvé par `metadata.pack_id`
  et le prix vient de `PACKS`, jamais du client ;
* **quota** : dans `/validate` par clé API, l'ordre est *quota du plan → crédit
  → refus* ; le débit d'un crédit est atomique (`UPDATE … WHERE balance >= 1`),
  et une panne après réservation est compensée par une ligne `correction` ;
* **remboursement** : un `charge.refunded` ajoute une transaction négative
  (le solde peut devenir négatif : c'est une dette du compte, affichée telle
  quelle) ;
* **aucune clé réelle** dans le dépôt : la clé secrète vient de `.env`
  (placeholder `sk_test_VOTRE_CLE` dans `.env.example`), et la clé publique
  (`pk_test_…`) ne sert qu'au navigateur, via l'interface écrite par Claude.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config import get_settings
from app.models import CreditBalance, CreditTransaction

logger = logging.getLogger(__name__)
settings = get_settings()

#: Catalogue des cinq packs de crédits (D88). Prix en **centimes d'euro**
#: (entiers : jamais de flottant sur de l'argent), montants à valider par Kder.
#: L'identifiant de prix Stripe correspondant vit dans la configuration
#: (`STRIPE_PRICE_PACK_5`…) : le pack et son prix sont ici, la fiche Stripe est
#: une donnée d'exploitation, et les deux sont confrontés au webhook.
PACKS: tuple[dict[str, Any], ...] = (
    {"id": "pack_5", "validations": 5, "prix_eur_cents": 149, "devise": "eur"},
    {"id": "pack_25", "validations": 25, "prix_eur_cents": 590, "devise": "eur"},
    {"id": "pack_100", "validations": 100, "prix_eur_cents": 1990, "devise": "eur"},
    {"id": "pack_500", "validations": 500, "prix_eur_cents": 7900, "devise": "eur"},
    {"id": "pack_2000", "validations": 2000, "prix_eur_cents": 24900, "devise": "eur"},
)
PACKS_PAR_ID: dict[str, dict[str, Any]] = {pack["id"]: pack for pack in PACKS}

#: Tolérance de l'horodatage d'un webhook Stripe (seconde d'émission).
TOLERANCE_WEBHOOK_SECONDES = 300

#: Motifs de transaction (grand livre). `correction` compense une réservation de
#: crédit dont la validation a échoué après le débit (jamais de crédit perdu).
MOTIFS = ("achat", "validation", "remboursement", "correction")


class CreditError(Exception):
    """Erreur métier des crédits (message publié, sans secret ni détail interne)."""


class StripeIndisponible(CreditError):
    """Stripe n'est pas configuré (clé/prix absent) ou ne répond pas : fail-closed."""


class PaiementInvalide(CreditError):
    """Pack inconnu ou montant qui ne correspond pas au catalogue."""


def packs_publics() -> list[dict[str, Any]]:
    """Les packs affichables (sans les identifiants Stripe, qui restent serveur)."""
    return [
        {
            "id": pack["id"],
            "validations": pack["validations"],
            "prix_eur_cents": pack["prix_eur_cents"],
            "devise": pack["devise"],
        }
        for pack in PACKS
    ]


def pack(pack_id: str) -> dict[str, Any]:
    """Le pack du catalogue, ou `PaiementInvalide` (whitelist, jamais le client)."""
    trouve = PACKS_PAR_ID.get(str(pack_id or ""))
    if trouve is None:
        raise PaiementInvalide(f"pack inconnu : {pack_id!r}")
    return trouve


def prix_stripe(pack_id: str) -> str:
    """L'identifiant de prix Stripe du pack, lu dans la configuration."""
    variable = f"stripe_price_{pack_id}"
    return str(getattr(settings, variable, "") or "")


def stripe_configure() -> bool:
    """Vrai si la clé secrète est là : sinon l'achat répond `503`, jamais un crash."""
    return bool(str(settings.stripe_secret_key or "").strip())


def credits_disponibles(session) -> bool:
    """Vrai si le schéma des crédits est présent dans la base.

    Une base d'avant D88 (ou un schéma de test minimal) n'a pas ces tables : dans
    ce cas l'appelant garde le comportement d'avant (refus de quota), au lieu de
    transformer un simple schéma absent en panne de base. Une vraie panne est
    journalisée et répond la même chose : refus fail-closed, jamais un crédit
    inventé.
    """
    try:
        session.execute(select(CreditBalance.customer_id).limit(1))
        return True
    except SQLAlchemyError:
        session.rollback()
        logger.warning("credits : schema absent ou illisible, comportement d'avant D88")
        return False


# --------------------------------------------------------------------------- #
# Lecture du solde et du grand livre
# --------------------------------------------------------------------------- #


def solde(session, customer_id: int) -> int:
    """Solde de crédits du compte (`0` s'il n'a jamais acheté)."""
    balance = session.scalar(
        select(CreditBalance.balance).where(CreditBalance.customer_id == customer_id)
    )
    return int(balance or 0)


def historique(session, customer_id: int, limite: int = 50) -> list[dict[str, Any]]:
    """Les derniers mouvements, du plus récent au plus ancien (API de la page)."""
    lignes = session.scalars(
        select(CreditTransaction)
        .where(CreditTransaction.customer_id == customer_id)
        .order_by(CreditTransaction.created_at.desc(), CreditTransaction.id.desc())
        .limit(max(1, min(int(limite), 200)))
    ).all()
    return [
        {
            "date": ligne.created_at.isoformat() if ligne.created_at else None,
            "delta": int(ligne.delta),
            "reason": ligne.reason,
            "pack_id": ligne.pack_id,
            "amount_cents": ligne.amount_cents,
            "currency": ligne.currency,
        }
        for ligne in lignes
    ]


# --------------------------------------------------------------------------- #
# Mouvements du solde (atomiques ; le grand livre porte l'idempotence)
# --------------------------------------------------------------------------- #


def _ajouter_au_solde(session, customer_id: int, delta: int) -> None:
    """Crée la ligne de solde si absente, sinon l'incrémente — atomiquement.

    `ON CONFLICT` est compris par PostgreSQL (production) et par SQLite (tests) :
    une même requête sert les deux, sans dialecte conditionnel.
    """
    session.execute(
        text(
            "INSERT INTO credit_balance (customer_id, balance, updated_at) "
            "VALUES (:customer_id, :delta, CURRENT_TIMESTAMP) "
            "ON CONFLICT (customer_id) DO UPDATE SET "
            "balance = credit_balance.balance + EXCLUDED.balance, "
            "updated_at = CURRENT_TIMESTAMP"
        ),
        {"customer_id": customer_id, "delta": int(delta)},
    )


def debiter_credit(session, customer_id: int) -> bool:
    """Réserve **un** crédit. Vrai si le compte avait un solde positif.

    L'`UPDATE` conditionnel est la seule garantie contre le double débit en
    concurrence ; le grand livre garde la trace (`delta = -1`). La validation est
    servie après cette réservation, et l'appelant compense si elle échoue
    (`crediter_correction`) : jamais de crédit perdu sur panne serveur.
    """
    resultat = session.execute(
        text(
            "UPDATE credit_balance SET balance = balance - 1, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE customer_id = :customer_id AND balance >= 1"
        ),
        {"customer_id": customer_id},
    )
    if resultat.rowcount != 1:
        session.rollback()
        return False
    session.add(
        CreditTransaction(customer_id=customer_id, delta=-1, reason="validation")
    )
    session.commit()
    return True


def crediter_correction(session, customer_id: int) -> None:
    """Rend un crédit réservé dont la validation a échoué (compensation)."""
    try:
        _ajouter_au_solde(session, customer_id, 1)
        session.add(
            CreditTransaction(customer_id=customer_id, delta=1, reason="correction")
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("compensation de credit impossible pour le compte %s", customer_id)


def _inscrire_mouvement(
    session,
    *,
    customer_id: int,
    delta: int,
    reason: str,
    pack_id: str | None = None,
    amount_cents: int | None = None,
    currency: str = "eur",
    stripe_event_id: str | None = None,
    stripe_session_id: str | None = None,
    payment_intent_id: str | None = None,
) -> bool:
    """Écrit une ligne du grand livre ; `False` si l'événement a déjà été traité.

    L'unicité (`stripe_event_id` / `payment_intent_id`) est la protection réelle
    contre le double crédit : elle vit en base, pas dans un test applicatif.
    """
    ligne = CreditTransaction(
        customer_id=customer_id,
        delta=int(delta),
        reason=reason,
        pack_id=pack_id,
        amount_cents=amount_cents,
        currency=currency,
        stripe_event_id=stripe_event_id,
        stripe_session_id=stripe_session_id,
        payment_intent_id=payment_intent_id,
    )
    session.add(ligne)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return False
    return True


def crediter_achat(
    session,
    *,
    customer_id: int,
    pack_id: str,
    montant_cents: int,
    devise: str,
    stripe_event_id: str,
    stripe_session_id: str | None = None,
    payment_intent_id: str | None = None,
) -> tuple[str, str]:
    """Crédite un achat vérifié, une seule fois. Renvoie `(code, message)`.

    Le prix est celui du catalogue : un montant qui ne correspond pas est refusé
    sans rien créditer (aucune confiance au client ni au contenu libre du webhook).
    """
    achat = pack(pack_id)
    if int(montant_cents) != int(achat["prix_eur_cents"]):
        return "montant_invalide", "le montant payé ne correspond pas au prix du pack"
    if str(devise or "").lower() != achat["devise"]:
        return "devise_invalide", "la devise payée ne correspond pas au pack"
    if not _inscrire_mouvement(
        session,
        customer_id=customer_id,
        delta=achat["validations"],
        reason="achat",
        pack_id=pack_id,
        amount_cents=int(montant_cents),
        currency=achat["devise"],
        stripe_event_id=stripe_event_id,
        stripe_session_id=stripe_session_id,
        payment_intent_id=payment_intent_id,
    ):
        return "deja_traite", "cet événement Stripe a déjà été crédité"
    _ajouter_au_solde(session, customer_id, achat["validations"])
    session.commit()
    logger.info(
        "credits : achat %s (pack %s, +%d) pour le compte %s",
        stripe_event_id,
        pack_id,
        achat["validations"],
        customer_id,
    )
    return "credite", f"{achat['validations']} validations ajoutées"


def rembourser(
    session,
    *,
    customer_id: int,
    credits_rendus: int,
    montant_cents: int,
    stripe_event_id: str,
    pack_id: str | None = None,
) -> tuple[str, str]:
    """Rembourse un achat : transaction négative, solde possiblement négatif."""
    if not _inscrire_mouvement(
        session,
        customer_id=customer_id,
        delta=-abs(int(credits_rendus)),
        reason="remboursement",
        pack_id=pack_id,
        amount_cents=int(montant_cents),
        stripe_event_id=stripe_event_id,
    ):
        return "deja_traite", "ce remboursement a déjà été traité"
    _ajouter_au_solde(session, customer_id, -abs(int(credits_rendus)))
    session.commit()
    logger.info(
        "credits : remboursement %s (-%d) pour le compte %s",
        stripe_event_id,
        abs(int(credits_rendus)),
        customer_id,
    )
    return "rembourse", f"{abs(int(credits_rendus))} validations reprises"


# --------------------------------------------------------------------------- #
# Webhook Stripe : signature, événements, idempotence
# --------------------------------------------------------------------------- #


def verifier_signature(
    payload: bytes,
    entete: str | None,
    secret: str | None = None,
    maintenant: int | None = None,
    tolerance: int = TOLERANCE_WEBHOOK_SECONDES,
) -> bool:
    """Vérifie `Stripe-Signature` (HMAC-SHA256, en-tête `t=…,v1=…`).

    Implémenté sans dépendance : c'est le même calcul que le SDK officiel, et il
    évite d'ajouter un paquet pour une seule fonction. Une signature fausse, un
    horodatage hors tolérance ou un secret absent valent refus.
    """
    secret = str(secret if secret is not None else settings.stripe_webhook_secret or "").strip()
    if not secret or not entete:
        return False
    morceaux: dict[str, list[str]] = {}
    for element in str(entete).split(","):
        nom, egal, valeur = element.partition("=")
        if egal:
            morceaux.setdefault(nom.strip(), []).append(valeur.strip())
    try:
        horodatage = int(morceaux["t"][0])
    except (KeyError, IndexError, ValueError):
        return False
    moment = int(maintenant if maintenant is not None else time.time())
    if abs(moment - horodatage) > max(1, int(tolerance)):
        return False
    signe = hmac.new(
        secret.encode("utf-8"), f"{horodatage}.".encode("utf-8") + payload, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(signe, valeur) for valeur in morceaux.get("v1", []))


def traiter_evenement(session, evenement: dict[str, Any]) -> tuple[str, str]:
    """Applique un événement Stripe **déjà décodé et authentifié**.

    Événements utiles : `checkout.session.completed` (achat) et
    `charge.refunded` (remboursement). Les autres sont ignorés (`200` côté route,
    pour que Stripe ne réessaie pas indéfiniment) ; un `payment_intent.succeeded`
    est ignoré parce que l'achat est déjà crédité par la session, ce qui évite un
    double crédit.
    """
    if not isinstance(evenement, dict):
        return "invalide", "événement Stripe illisible"
    type_evenement = str(evenement.get("type") or "")
    identifiant = str(evenement.get("id") or "")
    if not identifiant:
        return "invalide", "événement sans identifiant"
    objet = ((evenement.get("data") or {}).get("object") or {})
    if not isinstance(objet, dict):
        return "invalide", "objet d'événement illisible"

    if type_evenement == "checkout.session.completed":
        if str(objet.get("payment_status") or "") != "paid":
            return "ignore", "session Stripe non payée"
        metadonnees = objet.get("metadata") or {}
        try:
            customer_id = int(metadonnees.get("customer_id") or 0)
        except (TypeError, ValueError):
            return "invalide", "metadata.customer_id absent ou illisible"
        try:
            return crediter_achat(
                session,
                customer_id=customer_id,
                pack_id=str(metadonnees.get("pack_id") or ""),
                montant_cents=int(objet.get("amount_total") or 0),
                devise=str(objet.get("currency") or ""),
                stripe_event_id=identifiant,
                stripe_session_id=str(objet.get("id") or "") or None,
                payment_intent_id=str(objet.get("payment_intent") or "") or None,
            )
        except PaiementInvalide as exc:
            return "invalide", str(exc)

    if type_evenement == "charge.refunded":
        paiement = str(objet.get("payment_intent") or "")
        achat = session.scalar(
            select(CreditTransaction).where(
                CreditTransaction.payment_intent_id == paiement,
                CreditTransaction.reason == "achat",
            )
        )
        if paiement and achat is None:
            return "ignore", "remboursement d'un paiement inconnu du grand livre"
        credits_rendus = achat.delta if achat is not None and achat.delta else 0
        if credits_rendus <= 0:
            return "ignore", "aucun crédit à reprendre pour ce remboursement"
        return rembourser(
            session,
            customer_id=int(achat.customer_id),
            credits_rendus=int(credits_rendus),
            montant_cents=int(objet.get("amount_refunded") or 0),
            stripe_event_id=identifiant,
            pack_id=achat.pack_id,
        )

    return "ignore", f"événement {type_evenement or 'inconnu'} sans effet sur les crédits"


def traiter_webhook(
    session, payload: bytes, entete: str | None
) -> tuple[int, dict[str, Any]]:
    """Point d'entrée de la route `POST /stripe/webhook` : `(statut HTTP, corps)`.

    La route que Claude branchera n'a rien à décider : elle lit le corps brut (pour
    la signature), passe l'en-tête `Stripe-Signature`, et renvoie ce couple. Un
    `503` sur erreur de base demande à Stripe de réessayer ; tout le reste répond
    `200`, y compris un événement ignoré, pour arrêter les rejeux inutiles.
    """
    if not verifier_signature(payload, entete):
        return 400, {"error": "signature_invalide", "recu": False}
    try:
        evenement = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"error": "json_invalide", "recu": False}
    try:
        code, message = traiter_evenement(session, evenement)
    except SQLAlchemyError as exc:
        session.rollback()
        logger.warning("webhook Stripe : base indisponible (%s)", type(exc).__name__)
        return 503, {"error": "base_indisponible", "recu": False}
    return 200, {"recu": True, "resultat": code, "detail": message}


# --------------------------------------------------------------------------- #
# Achat : session Stripe Checkout (le HTML et les boutons sont de Claude)
# --------------------------------------------------------------------------- #


def _poster_stripe(donnees: list[tuple[str, str]]) -> httpx.Response:
    """Appel HTTP à Stripe (isolé : les tests remplacent cette fonction)."""
    return httpx.post(
        f"{settings.stripe_api_base.rstrip('/')}/v1/checkout/sessions",
        data=donnees,
        auth=(str(settings.stripe_secret_key or ""), ""),
        timeout=20.0,
    )


def creer_session_checkout(
    *,
    client_email: str,
    customer_id: int,
    pack_id: str,
    base_url: str,
    locale: str = "auto",
) -> dict[str, Any]:
    """Crée une session Stripe Checkout (`mode=payment`) pour un pack.

    Lève `StripeIndisponible` si Stripe n'est pas configuré ou refuse : l'interface
    répond alors `503`, jamais une redirection à moitié construite. Le montant
    envoyé à Stripe est un **prix** du dashboard (identifiant serveur) : le client
    ne fournit jamais de montant.
    """
    if not stripe_configure():
        raise StripeIndisponible("paiement par carte non configuré")
    achat = pack(pack_id)
    prix = prix_stripe(pack_id)
    if not prix:
        raise StripeIndisponible(f"prix Stripe absent pour {pack_id}")
    donnees = [
        ("mode", "payment"),
        ("line_items[0][price]", prix),
        ("line_items[0][quantity]", "1"),
        ("client_reference_id", str(customer_id)),
        ("metadata[customer_id]", str(customer_id)),
        ("metadata[pack_id]", pack_id),
        ("metadata[validations]", str(achat["validations"])),
        ("success_url", f"{base_url.rstrip('/')}/compte/credits/succes?session_id={{CHECKOUT_SESSION_ID}}"),
        ("cancel_url", f"{base_url.rstrip('/')}/compte/credits/annule"),
        ("locale", locale or "auto"),
    ]
    if client_email:
        donnees.append(("customer_email", client_email))
    logger.info(
        "credits : creation de session Checkout pour le compte %s (pack %s)",
        customer_id,
        pack_id,
    )
    try:
        reponse = _poster_stripe(donnees)
    except httpx.HTTPError as exc:
        raise StripeIndisponible(
            f"Stripe injoignable ({type(exc).__name__})"
        ) from exc
    if reponse.status_code >= 400:
        raise StripeIndisponible(f"Stripe a refusé la session (HTTP {reponse.status_code})")
    try:
        document = reponse.json()
    except ValueError as exc:
        raise StripeIndisponible("réponse Stripe illisible") from exc
    url = str(document.get("url") or "")
    session_id = str(document.get("id") or "")
    if not url or not session_id:
        raise StripeIndisponible("réponse Stripe sans URL de paiement")
    return {
        "pack_id": pack_id,
        "validations": achat["validations"],
        "amount_cents": achat["prix_eur_cents"],
        "currency": achat["devise"],
        "checkout_url": url,
        "session_id": session_id,
    }
