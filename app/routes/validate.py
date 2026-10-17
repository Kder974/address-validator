"""POST /validate — cœur payant de l'API.

Conforme à la spec (Phase 3) avec un ajout : le **cache Redis** (décision D5).
La spec exigeait « le cache réduit les appels SmartyStreets de 80 % » sans
brancher le cache dans la route ; on l'intègre donc ici.

Deux ajouts du chantier de découvrabilité :

  D29  le paiement accepté est enregistré sous le hash de sa **preuve**
       (`X-Payment-Proof`), et non plus seulement sous un en-tête maison
       optionnel : le registre durable compte ainsi ce qui est réellement payé.
  D30  une requête refusée en `402` est journalisée (IP pseudonymisée) : c'est
       la seule façon de mesurer si l'API est *trouvée*.

Ajout du Lot B :

  D34  le corps du `402` est un `PaymentRequired` x402 v1 (`x402Version` +
       `accepts[]`) et un paiement x402 réglé reçoit `X-PAYMENT-RESPONSE`. Le
       registre (D29) est désormais identifié par le hash du **règlement** pour
       un paiement x402 : ce qui est compté est ce qui a été encaissé.

Ajout du durcissement W2 :

  D41  un paiement accepté dont l'inscription au registre échoue n'est **pas**
       servi : la réponse est un `500` explicite et le compteur
       `ledger_write_failures_total` s'incrémente. Le registre étant ce qui
       prouve l'encaissement (D29) et ce qui alimente le revenu publié (D40),
       servir sans ligne produirait un revenu perdu *et* silencieux — le pire
       des deux mondes : le client a payé, l'exploitant ne le saura jamais.

Clé de cache = SHA-256 de l'adresse brute normalisée en JSON trié, ce qui
permet de répondre avant tout appel réseau.

Ajout de D45 (abonnement par clé API) :

  D45  `/validate` accepte deux façons de payer. Un client qui a un compte
       présente `X-API-Key` ; un agent autonome paie en x402. **La clé a la
       priorité** quand elle est là : un client qui envoie les deux veut être
       servi par son abonnement, et le lui dire (`401`) vaut mieux que
       l'encaisser en USDC sur une clé qui ne fonctionne pas. Une validation
       servie par clé n'écrit **rien** au registre USDC (D29) : elle est couverte
       par un abonnement, la compter comme un encaissement gonflerait le revenu
       publié (D40). C'est le compteur du compte (`customer_validations`) qui
       avance, pas la caisse. Une base de comptes injoignable donne un `503`
       explicite, **jamais** un `402` : une clé qui n'a pas pu être vérifiée n'est
       pas une clé refusée, et demander un paiement sur une panne pousserait un
       client en règle à payer deux fois.
"""

import hashlib
import json
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app import accounts, audience
from app.cache import get_cached_result, store_in_cache
from app.config import get_settings
from app.database import SessionLocal
from app.observability import log_unpaid_attempt
from app.payment import (
    PROOF_HEADER,
    generate_payment_required_response,
    payment_result,
    record_transaction,
    tx_hash_from_proof,
)
from app.routes.metrics import metrics
from app.schemas import AddressOutput, ValidationRequest, ValidationResponse
from app.validation import validate_full
from app.x402 import PAYMENT_REQUIRED_HEADER, payment_required_header

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)

#: En-tête d'authentification par clé API (D45). Même graphie que celle annoncée
#: au client sur `/compte` et sur la page d'inscription : la page, le guide et le
#: code disent une seule chose.
API_KEY_HEADER = "X-API-Key"
#: Comment cet appel a été payé : `api_key`, `x402` ou `hmac`. Sans cet en-tête,
#: un client ne peut pas distinguer un appel couvert par son abonnement d'un
#: appel encaissé en USDC.
PAYMENT_MODE_HEADER = "X-Payment-Mode"

#: Motif de refus → code HTTP. Le code porte un sens, il n'est pas décoratif :
#:   * `401` — la clé ne désigne aucun compte : le client doit corriger sa
#:     configuration avant de réessayer ;
#:   * `403` — le compte existe mais n'ouvre plus le service (essai terminé) ;
#:   * `402` — le quota du mois est consommé : c'est un plafond d'usage, pas un
#:     défaut d'authentification (un `401` ferait chercher une faute de clé).
REFUS_HTTP = {
    accounts.REFUS_CLE_INCONNUE: 401,
    accounts.REFUS_COMPTE_EXPIRE: 403,
    accounts.REFUS_QUOTA_ATTEINT: 402,
}

#: Ce que le client lit, et ce qu'il peut faire ensuite. Un refus qui n'indique
#: pas la sortie est un mur : chaque motif dit l'action qui débloque.
REFUS_MESSAGES = {
    accounts.REFUS_CLE_INCONNUE: (
        "Clé API inconnue.",
        "Aucun compte ne correspond à cette clé. Vérifiez qu'elle est complète "
        "(elle commence par sk_live_ et compte 40 caractères), qu'elle n'a pas été "
        "régénérée depuis, et qu'elle appartient bien à ce service. Une clé perdue "
        "se régénère depuis l'espace client : elle ne peut pas être relue.",
    ),
    accounts.REFUS_COMPTE_EXPIRE: (
        "Compte expiré.",
        "L'essai gratuit est terminé : le service par clé API est suspendu pour ce "
        "compte. Les tarifs et le passage à un plan payant sont indiqués sur la "
        "page tarifs ; rien n'est prélevé automatiquement.",
    ),
    accounts.REFUS_QUOTA_ATTEINT: (
        "Quota du mois atteint.",
        "Le nombre de validations incluses dans votre plan pour ce mois est "
        "consommé. Le compteur repart au premier jour du mois suivant (UTC) ; "
        "pour continuer avant, écrivez-nous pour changer de plan.",
    ),
}


def cache_key(address: dict) -> str:
    payload = json.dumps(address, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def perform_validation(raw: dict) -> tuple[dict, str]:
    """Pipeline de validation **partagé** (route HTTP et outil MCP).

    Renvoie `(résultat, clé de cache)`. Les noms appelés ici sont des globaux du
    module (`validate_full`, `get_cached_result`, `store_in_cache`) : les oracles
    et les tests les remplacent par `mock.patch` sans toucher au code.
    """
    key = cache_key(raw)
    cached = get_cached_result(key)
    # D67 : le tableau de bord compte les appels au fournisseur (ce qui coûte) et
    # les pays des adresses ; seul le code pays de l'adresse est noté.
    audience.noter(address_country=raw.get("country"))
    if cached is not None:
        metrics.incr("cache_hits")
        audience.noter(cache_hit=True, degraded=bool(cached.get("degraded")))
        return cached, key

    metrics.incr("cache_misses")
    result = validate_full(raw)
    store_in_cache(key, result)
    audience.noter(cache_hit=False, degraded=bool(result.get("degraded")))
    if result.get("degraded"):
        metrics.incr("provider_errors")
        metrics.incr("degraded_results")
    return result, key


def build_response(
    result: dict,
    key: str,
    transaction_id: str | None = None,
    *,
    price_paid: float | None = None,
) -> ValidationResponse:
    """Réponse publique, construite à partir du résultat interne.

    `price_paid` est le montant réglé **pour cet appel**. En mode clé API (D45),
    l'appel est couvert par l'abonnement : annoncer 0,02 USDC reviendrait à
    annoncer un encaissement qui n'a pas eu lieu, dans une réponse que le client
    archive et que son comptable relit.
    """
    return ValidationResponse(
        result=AddressOutput(
            validated=result["validated"],
            standardized=result["standardized"],
            latitude=result["latitude"],
            longitude=result["longitude"],
            deliverability=result["deliverability"],
            confidence=result["confidence"],
            degraded=result["degraded"],
            cache_key=key,
        ),
        price_paid=settings.x402_price if price_paid is None else price_paid,
        transaction_id=transaction_id,
    )


def _session():
    """Session SQLAlchemy des comptes. Le point unique que les tests remplacent."""
    return SessionLocal()


def _refus_api_key(motif: str, cle: str, statut: dict | None = None) -> JSONResponse:
    """Refus en mode clé : le code, la raison, l'état du compte s'il est connu."""
    titre, detail = REFUS_MESSAGES[motif]
    corps: dict = {
        "error": titre,
        "detail": detail,
        "mode": "api_key",
        "reason": motif,
        # Le préfixe seulement : c'est déjà ce qu'affiche `/compte`, et il permet
        # au client de reconnaître *quelle* clé il a envoyée. Aucune clé complète
        # ne circule dans un corps de réponse, où elle finirait dans un journal.
        "prefix": accounts.api_key_prefix(cle),
    }
    if statut is not None:
        corps["plan"] = statut["plan"]
        corps["quota"] = statut["quota"]
        corps["validations_this_month"] = statut["validations_mois"]
        corps["trial_ends_at"] = (
            statut["trial_ends_at"].isoformat() if statut["trial_ends_at"] else None
        )
        if motif != accounts.REFUS_CLE_INCONNUE:
            # Une sortie concrète : sans elle, un refus de service n'est qu'un mur.
            corps["upgrade"] = f"{settings.public_base_url}/pricing"
    return JSONResponse(status_code=REFUS_HTTP[motif], content=corps)


def serve_with_api_key(raw: dict, response: Response, cle: str):
    """Servir une validation payée par un compte (D45).

    L'ordre est le contrat : **autoriser, puis valider, puis débiter le compte**.
    Aucune validation n'est calculée pour un compte non autorisé, donc aucune ne
    peut être servie hors quota par inadvertance. Le registre USDC (D29) n'est pas
    touché : un abonnement n'est pas un encaissement à l'appel, et l'y inscrire
    ferait mentir le revenu publié (D40).
    """
    metrics.incr("api_key_requests")
    audience.noter(mode="cle")
    try:
        with _session() as session:
            client, motif = accounts.authorize_api_key(session, cle)
            if client is None or motif is not None:
                metrics.incr("api_key_denied")
                statut = (
                    accounts.plan_status(session, client)
                    if client is not None
                    else None
                )
                # Le préfixe affichable suffit à retrouver le compte dans un
                # journal ; la clé présentée n'y entre jamais, même tronquée.
                logger.info(
                    "cle API refusee (%s) prefixe=%s",
                    motif,
                    accounts.api_key_prefix(cle),
                )
                return _refus_api_key(motif, cle, statut)

            audience.noter(customer_id=client.id)
            result, key = perform_validation(raw)
            accounts.record_validation(session, client.id, key, result["validated"])
            statut = accounts.plan_status(session, client)
    except SQLAlchemyError as exc:
        # Jamais un `402` ici : la clé n'a pas été *refusée*, elle n'a pas pu être
        # *vérifiée*. Répondre « payez » sur une panne pousserait un client en
        # règle à payer deux fois pour le même service.
        metrics.incr("api_key_unavailable")
        logger.warning(
            "base des comptes injoignable : appel par cle refuse (503) : %s", exc
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Comptes clients indisponibles.",
                "detail": (
                    "la base des comptes ne répond pas : la clé API ne peut pas "
                    "être vérifiée, donc la validation n'est pas servie. Rien n'a "
                    "été consommé — réessayez dans quelques minutes."
                ),
                "mode": "api_key",
            },
        )

    metrics.incr("api_key_validations")
    response.headers[PAYMENT_MODE_HEADER] = "api_key"
    response.headers["X-Plan"] = statut["plan"]
    if statut["restant"] is not None:
        # Pas d'en-tête quand il n'y a pas de plafond : l'absence dit « illimité »,
        # un chiffre inventé en dirait moins.
        response.headers["X-Quota-Remaining"] = str(statut["restant"])
    return build_response(result, key, price_paid=0.0)


def paiement_requis(request: Request, decision) -> JSONResponse:
    """Le `402` de `/validate` : corps x402 v1, offre v2 dans `PAYMENT-REQUIRED` (D77)."""
    metrics.incr("unpaid_requests")
    # D30 : tracer la tentative non payée. L'entrée ne contient que des
    # métadonnées de requête (aucune adresse, IP pseudonymisée par défaut),
    # et un journal indisponible ne doit jamais casser la réponse.
    log_unpaid_attempt(request, settings.x402_price, decision.reason)
    # L'échec de paiement est aussi porté par `X-PAYMENT-RESPONSE` / `PAYMENT-RESPONSE`
    # (`success: false`, `errorReason`).
    headers = dict(decision.response_headers)
    # Sans aucun moyen de paiement (le cas du catalogue Coinbase) : message standard et fiche
    # du catalogue. Un paiement refusé reçoit son motif, sans la fiche qu'il a déjà lue.
    sans_paiement = decision.protocol == "none"
    headers[PAYMENT_REQUIRED_HEADER] = payment_required_header(
        settings.x402_price, None if sans_paiement else decision.reason, fiche=sans_paiement)
    return JSONResponse(
        status_code=402,
        headers=headers,
        content=generate_payment_required_response(settings.x402_price, decision.reason),
    )


@router.post("/validate", response_model=ValidationResponse)
async def validate_address(request: Request, body: ValidationRequest, response: Response):
    metrics.incr("requests_total")

    # --- Mode clé API (D45) : prioritaire dès qu'une clé est présentée --------
    # Un client qui a un compte envoie sa clé ; s'il envoie *aussi* un paiement
    # x402, la clé tranche. Le servir par son abonnement est ce qu'il demande, et
    # l'encaisser en USDC reviendrait à le facturer pour un service déjà payé.
    cle = request.headers.get(API_KEY_HEADER)
    if cle and cle.strip():
        return serve_with_api_key(body.address.model_dump(), response, cle)

    # --- Couche paiement : 402 tant qu'aucun paiement valide n'est fourni ---
    # D34 : deux voies sont acceptées — x402 standard (`X-PAYMENT`, signature
    # EIP-3009 + règlement) et la preuve HMAC historique (`X-Payment-Proof`,
    # D15/D27). `payment_result` choisit, `/validate` ne décide pas du protocole.
    decision = payment_result(request)
    if not decision.paid:
        return paiement_requis(request, decision)

    metrics.incr("paid_requests")
    if decision.protocol == "x402":
        metrics.incr("x402_payments")

    raw = body.address.model_dump()

    # D29 : c'est la preuve de paiement qui identifie ce qui a été payé. En x402,
    # c'est le hash du **règlement** (donc ce qui a réellement été encaissé) ;
    # en HMAC, le hash cité par la preuve ; `X-Transaction-Id` reste un repli
    # d'outillage. `ON CONFLICT DO NOTHING` côté SQL rend l'écriture idempotente :
    # une preuve rejouée ne compte pas deux fois.
    transaction_id = request.headers.get("X-Transaction-Id") or decision.transaction
    ledger_key = (
        decision.transaction
        or tx_hash_from_proof(request.headers.get(PROOF_HEADER))
        or transaction_id
    )

    result, key = perform_validation(raw)
    if ledger_key and not record_transaction(ledger_key, settings.x402_price, key):
        # D41 : le registre est ce qui prouve l'encaissement (D29) et ce qui
        # alimente le revenu publie (D40). Servir une validation sans ligne
        # produirait un revenu reel non compte, invisible et non rejouable : on
        # refuse de servir. Le compteur rend l'incident mesurable (`/metrics`),
        # le 500 le rend visible au client au lieu d'un succes trompeur.
        # Un paiement x402 a deja ete regle : l'operateur doit le rapprocher
        # (le hash du reglement est dans la reponse, donc journalisable).
        metrics.incr("ledger_write_failures_total")
        return JSONResponse(
            status_code=500,
            content={
                "error": "paiement non enregistre : validation non servie",
                "detail": (
                    "le paiement a ete accepte, mais son inscription au registre a "
                    "echoue ; l'encaissement doit etre rapproche"
                ),
                "protocol": "x402",
                "price": settings.x402_price,
                "currency": "USDC",
                "transaction_id": transaction_id,
            },
        )

    # D67 : le revenu par pays ne compte que l'encaissé réel (hors recette, D40).
    audience.noter(mode=decision.protocol, amount_usdc=None if settings.test_mode or not ledger_key else settings.x402_price)
    response.headers.update(decision.response_headers)
    # D45 : tout appel payé dit *comment* il l'a été. Sans cela, un client ne peut
    # pas distinguer un abonnement d'un encaissement en USDC.
    response.headers[PAYMENT_MODE_HEADER] = decision.protocol

    return build_response(result, key, transaction_id)
