"""POST /stripe/webhook — le paiement Stripe active le plan tout seul (D91).

Le client paie sur un lien de paiement Stripe ouvert depuis son espace client ;
le lien porte `client_reference_id` = le numéro de son compte. Stripe appelle
ensuite cette route :

* `checkout.session.completed` (payé, en euros) : le montant désigne l'offre
  (`accounts.PLAN_PAR_MONTANT` : 2 900 -> Starter, 7 900 -> Pro) et le compte
  passe à ce plan ; son client Stripe (`cus_…`) est noté ;
* `customer.subscription.deleted` : Stripe a terminé l'abonnement (résiliation
  en fin de période, ou paiements refusés) ; le compte revient à l'essai, déjà
  échu, donc sa clé n'est plus servie.

**Rien n'est cru sans signature.** L'en-tête `Stripe-Signature` (`t=…,v1=…`) est
un HMAC-SHA256 de `t.corps` avec le secret du webhook (`STRIPE_WEBHOOK_SECRET`,
dans le `.env` seulement) ; un horodatage de plus de 5 minutes est refusé (rejeu).
Sans secret configuré, la route répond `503` et ne fait rien. Un événement
inconnu, ou qui ne désigne aucun compte, reçoit `200` (Stripe cesse de le
renvoyer) et ne change rien. Aucune carte ni moyen de paiement ne passe ici.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import accounts
from app.config import get_settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

#: Écart maximal entre l'horodatage signé et l'heure du serveur (secondes).
TOLERANCE = 300


def signature_valide(corps: bytes, entete: str, secret: str, maintenant: float | None = None) -> bool:
    """`Stripe-Signature` (`t=…,v1=…`) : HMAC-SHA256 de « t.corps » avec le secret, horodatage récent."""
    morceaux = [morceau.split("=", 1) for morceau in entete.split(",") if "=" in morceau]
    horodatage = next((valeur for cle, valeur in morceaux if cle.strip() == "t"), "")
    signatures = [valeur for cle, valeur in morceaux if cle.strip() == "v1"]
    if not secret or not horodatage.isdigit() or not signatures:
        return False
    if abs((maintenant if maintenant is not None else time.time()) - int(horodatage)) > TOLERANCE:
        return False
    attendu = hmac.new(secret.encode(), horodatage.encode() + b"." + corps, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(attendu, signature) for signature in signatures)


def _session():
    """Session SQLAlchemy. Le point unique que les tests remplacent (SQLite)."""
    return SessionLocal()


router = APIRouter()


@router.post("/stripe/webhook", include_in_schema=False)
async def webhook(request: Request) -> JSONResponse:
    secret = get_settings().stripe_webhook_secret
    if not secret:
        return JSONResponse({"error": "webhook Stripe non configure"}, status_code=503)
    corps = await request.body()
    if not signature_valide(corps, request.headers.get("stripe-signature", ""), secret):
        return JSONResponse({"error": "signature invalide"}, status_code=400)
    try:
        evenement = json.loads(corps)
    except ValueError:
        return JSONResponse({"error": "corps illisible"}, status_code=400)
    genre = evenement.get("type")
    objet = (evenement.get("data") or {}).get("object") or {}
    session = _session()
    try:
        if genre == "checkout.session.completed" and objet.get("payment_status") == "paid" and objet.get("currency") == "eur":
            reference = str(objet.get("client_reference_id") or "")
            montant = int(objet.get("amount_total") or 0)
            client = accounts.activer_abonnement(session, int(reference), montant, str(objet.get("customer") or "")) if reference.isdigit() else None
            if client is None:
                logger.warning("paiement Stripe sans compte ou montant connu : reference=%r montant=%s", reference, montant)
            else:
                logger.info("abonnement active : compte %s, plan %s", client.id, client.plan)
        elif genre == "customer.subscription.deleted":
            client = accounts.resilier_abonnement(session, str(objet.get("customer") or ""))
            if client is not None:
                logger.info("abonnement termine : compte %s", client.id)
    finally:
        session.close()
    return JSONResponse({"received": True})
