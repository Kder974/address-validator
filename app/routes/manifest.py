"""GET /manifest — description du service pour la découverte x402 Bazaar / MCP.

Conforme à la spec (Phase 3). Décision D33 : le manifest porte désormais des
schémas complets (entrée, sortie, erreurs), des **exemples** conformes au code,
des cas d'usage et des **limites explicites**.

Du Lot B (D34, D36, D37) :

* `payment.accepts[]` est produit par `app.x402.payment_requirements` — la
  **même** fonction que le corps `402` et que la carte MCP : une offre publiée
  ici ne peut pas diverger de celle qui est facturée ;
* `payment.x402_standard.compliant` vaut `True` parce que le protocole v1 est
  réellement implémenté (signature EIP-712 vérifiée hors ligne, règlement par
  facilitateur, `X-PAYMENT-RESPONSE`), et `verified[]` liste ce qui est mesuré
  plutôt que de le proclamer ;
* `endpoints.mcp` décrit le serveur MCP et sa carte de découverte.

Pourquoi insister sur les limites : un agent qui découvre un endpoint sans
savoir ce qu'il ne fait pas produit des appels perdus. Les cas les plus coûteux
sont écrits noir sur blanc (mode dégradé, couverture internationale partielle,
voie héritée à secret partagé réservée à l'exploitant).
"""

from fastapi import APIRouter

from app.config import get_settings
from app.payment import PROOF_HEADER, generate_payment_required_response
from app.schemas import ValidationRequest, ValidationResponse
from app.x402 import (
    MCP_PAYMENT_META_KEY,
    MCP_PAYMENT_RESPONSE_META_KEY,
    MCP_RESOURCE_PREFIX,
    PAYMENT_HEADER,
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_RESPONSE_HEADER_V2,
    PAYMENT_SIGNATURE_HEADER,
    X402_VERSION,
    network_v2,
    payment_requirements,
    payment_requirements_v2,
)

router = APIRouter()
settings = get_settings()


@router.get("/manifest")
async def manifest():
    return {
        "name": "AddressValidator",
        "description": (
            "Valide une adresse de livraison brute : parsing, standardisation, "
            "vérification auprès du fournisseur, géocodage, puis score de "
            "livrabilité (residential / commercial / unknown / invalid) avec "
            "niveau de confiance."
        ),
        "version": "1.0.0",
        "category": "data",
        "tags": [
            "address",
            "validation",
            "delivery",
            "logistics",
            "ecommerce",
            "geocoding",
        ],
        "price": settings.x402_price,
        "currency": "USDC",
        "protocol": "x402",
        # Bloc de vérité : ce qui est réellement accepté aujourd'hui, et ce qui
        # reste vrai en parallèle (la preuve HMAC de D15, outillage exploitant).
        "payment": {
            "protocol": "x402",
            "x402_version": X402_VERSION,
            # En-tête client et en-tête de règlement : ceux de la spécification
            # x402 v1 (`specs/transports-v1/http.md`), pas un en-tête maison.
            "header": PAYMENT_HEADER,
            "response_header": PAYMENT_RESPONSE_HEADER,
            "encoding": "base64(json)",
            "freshness_window_seconds": 300,
            "wallet": settings.cdp_wallet_address,
            "network": settings.x402_network,
            "usdc_contract": settings.x402_usdc_contract,
            # L'offre publiée ici est *la même* que celle du corps 402 : une seule
            # fonction la produit, donc une seule vérité.
            "accepts": [payment_requirements(settings.x402_price)],
            # D77 : la même offre en x402 v2, dans les en-têtes de `specs/transports-v2/http.md`.
            "v2": {
                "required_header": PAYMENT_REQUIRED_HEADER,
                "header": PAYMENT_SIGNATURE_HEADER,
                "response_header": PAYMENT_RESPONSE_HEADER_V2,
                "network": network_v2(),
                "accepts": [payment_requirements_v2(settings.x402_price)],
            },
            "facilitator": settings.x402_facilitator_url,
            "settlement_required": bool(settings.x402_require_settlement),
            "settlement_confirmations": settings.x402_min_confirmations,
            # Voie héritée, conservée pour l'outillage de l'exploitant : elle
            # n'est pas publiable auprès d'un agent tiers (secret partagé).
            "legacy_proof": {
                "header": PROOF_HEADER,
                "format": "tx_hash:timestamp:hmac_sha256",
                "audience": "operator-tooling",
            },
            "x402_standard": {
                "compliant": True,
                "missing": [],
                "verified": [
                    "corps 402 au format PaymentRequired (x402Version + accepts[])",
                    "signature EIP-712 TransferWithAuthorization (EIP-3009) verifiee hors ligne",
                    "reglement par facilitateur (/verify puis /settle) avant tout service",
                    "en-tete X-PAYMENT-RESPONSE publie sur les appels payes",
                    "x402 v2 : offre dans PAYMENT-REQUIRED, paiement par PAYMENT-SIGNATURE, recu dans PAYMENT-RESPONSE",
                    "extension bazaar (catalogue Coinbase) dans l'offre v2",
                ],
            },
        },
        "endpoints": {
            "validate": {
                "method": "POST",
                "path": "/validate",
                "price": settings.x402_price,
                "currency": "USDC",
                # Schémas dérivés des modèles Pydantic : ils ne peuvent pas
                # s'écarter du contrat réel, contrairement à un schéma recopié.
                "input_schema": ValidationRequest.model_json_schema(),
                "output_schema": ValidationResponse.model_json_schema(),
                "errors": {
                    "402": {
                        "description": (
                            "Paiement absent ou refusé : en-tête X-PAYMENT "
                            "(signature EIP-712 EIP-3009) illisible, périmé, mal "
                            "dirigé, montant insuffisant, ou règlement refusé par "
                            "le facilitateur. Le corps porte l'offre exacte dans "
                            "`accepts[]`, et `X-PAYMENT-RESPONSE` dit pourquoi."
                        ),
                        # Généré par la même fonction que la route : l'exemple ne
                        # peut pas se désynchroniser du corps réel.
                        "body_example": generate_payment_required_response(
                            settings.x402_price
                        ),
                    },
                    "422": {
                        "description": (
                            "Corps JSON invalide : `address` et ses champs "
                            "`street`, `city`, `state`, `zip` sont requis."
                        ),
                    },
                },
                "examples": [
                    {
                        "name": "adresse residentielle US",
                        "request": {
                            "address": {
                                "street": "1600 Amphitheatre Parkway",
                                "city": "Mountain View",
                                "state": "CA",
                                "zip": "94043",
                                "country": "US",
                            }
                        },
                        "response_200": {
                            "result": {
                                "validated": True,
                                "standardized": {
                                    "line1": "1600 Amphitheatre Parkway",
                                    "line2": "",
                                    "city": "Mountain View",
                                    "state": "CA",
                                    "zip": "94043",
                                    "country": "US",
                                },
                                "latitude": 37.422,
                                "longitude": -122.084,
                                "deliverability": "residential",
                                "confidence": 1.0,
                                "degraded": False,
                                "cache_key": "<sha256 de l'adresse brute>",
                            },
                            "price_paid": settings.x402_price,
                            "transaction_id": None,
                        },
                    },
                    {
                        "name": "adresse hors couverture, mode degrade (degraded=true)",
                        "request": {
                            "address": {
                                "street": "12 rue de la Paix",
                                "city": "Paris",
                                "state": "IDF",
                                "zip": "75002",
                                "country": "FR",
                            }
                        },
                        "response_200": {
                            "result": {
                                "validated": False,
                                "standardized": {
                                    "line1": "12 rue de la Paix",
                                    "line2": "",
                                    "city": "Paris",
                                    "state": "IDF",
                                    "zip": "75002",
                                    "country": "FR",
                                },
                                "latitude": None,
                                "longitude": None,
                                "deliverability": "unknown",
                                "confidence": 0.2,
                                "degraded": True,
                                "cache_key": "<sha256 de l'adresse brute>",
                            },
                            "price_paid": settings.x402_price,
                            "transaction_id": None,
                        },
                    },
                ],
            },
            "mcp": {
                "method": "POST",
                "path": "/mcp",
                "protocol": "json-rpc-2.0",
                "transport": "streamable-http",
                "tool": "validate_address",
                "discovery_card": f"{settings.public_base_url.rstrip('/')}/.well-known/mcp-server",
                "price": settings.x402_price,
                "currency": "USDC",
                # Le schéma MCP est celui de l'API : un seul contrat, deux transports.
                "input_schema": ValidationRequest.model_json_schema(),
                "output_schema": ValidationResponse.model_json_schema(),
                "free_methods": [
                    "initialize",
                    "ping",
                    "tools/list",
                    "resources/list",
                    "prompts/list",
                ],
                "paid_methods": ["tools/call"],
                "payment": {
                    # D78 : le transport MCP x402 v2 porte le paiement dans
                    # `params._meta` et rend le reçu dans `result._meta`.
                    "x402_version": 2,
                    "meta_key": MCP_PAYMENT_META_KEY,
                    "response_meta_key": MCP_PAYMENT_RESPONSE_META_KEY,
                    "resource_url": f"{MCP_RESOURCE_PREFIX}validate_address",
                    "accepts": [payment_requirements_v2(settings.x402_price)],
                    # Compatibilité D37 : un client v1 paie par l'en-tête HTTP.
                    "v1_header": PAYMENT_HEADER,
                },
                "errors": {
                    "isError": {
                        "description": (
                            "Paiement absent ou refusé : résultat d'outil "
                            "isError=true dont structuredContent est le "
                            "PaymentRequired x402 v2 (réseau eip155:8453, amount, "
                            "payTo) et content[0].text le même document. Un paiement "
                            "présenté puis refusé joint son reçu d'échec dans "
                            "x402/payment-response."
                        ),
                    }
                },
            },
        },
        "use_cases": [
            {
                "secteur": "e-commerce",
                "besoin": "verifier une adresse de livraison avant expedition",
                "valeur": "reduire les echecs de livraison et les retours",
            },
            {
                "secteur": "logistique",
                "besoin": "nettoyer un lot de commandes avant preparation",
                "valeur": "eviter les colis non distribuables",
            },
            {
                "secteur": "marketplaces",
                "besoin": "valider les adresses vendeurs ou acheteurs a l'inscription",
                "valeur": "fiabiliser la base d'adresses",
            },
            {
                "secteur": "agents IA",
                "besoin": "verifier une adresse demandee par un utilisateur",
                "valeur": "un appel paye, sans abonnement ni compte",
            },
        ],
        "limitations": [
            "Couverture : les adresses des Etats-Unis sont verifiees par SmartyStreets (USPS DPV, code Y/S/D requis) ; celles de France et d'outre-mer par la Base Adresse Nationale (BAN : score >= 0.8, code postal et ville confrontes) ; les adresses internationales des autres pays sont acceptees mais leur verification reste partielle.",
            "Mode degrade : si le fournisseur est injoignable, la reponse reste un 200 avec degraded=true et confidence basse. Lire degraded avant de conclure.",
            "Paiement : protocole x402 v1 (en-tete X-PAYMENT, base64 d'un PaymentPayload signe EIP-712) regle par le facilitateur avant tout service : 0,02 USDC sur Base. La voie heritage a secret partage (X-Payment-Proof) reste ouverte pour l'outillage de l'exploitant, mais elle n'est pas publiable : un agent tiers paie en x402.",
            "La voie x402 ne traite aucun identifiant de personne : pas de compte, pas de nom, pas d'e-mail. L'abonnement par cle d'API (X-API-Key, D44) cree un compte client : e-mail, nom de boutique, plateforme, empreinte bcrypt du mot de passe et empreinte SHA-256 de la cle (voir PRIVACY.md). Le revenu est publie sous forme d'agregats (/metrics/revenue), sans adresse IP.",
            "Pas de limitation de debit dans l'application : elle est tenue en amont par Nginx (10 req/s par IP, rafale 20, 429 au-dela).",
            "Les paiements anterieurs au deploiement de D29 ne figurent pas dans le registre durable : les fenetres 24 h / 7 j / 30 j ne sont pas retroactives.",
            "Serveur MCP : un seul outil (validate_address), sans etat et sans flux SSE (GET /mcp repond 405). La decouverte (tools/list) est gratuite, l'appel de l'outil est paye. Un appel sans paiement rend un resultat d'outil isError=true dont structuredContent est le PaymentRequired x402 v2 ; un client MCP v2 le paie en renvoyant le PaymentPayload dans params._meta[\"x402/payment\"], un client v1 peut aussi presenter l'en-tete X-PAYMENT.",
        ],
        "verification": {
            "united_states": "SmartyStreets, USPS DPV : code Y, S ou D requis",
            "france_outre_mer": "Base Adresse Nationale (api-adresse.data.gouv.fr) : score >= 0.8, code postal et ville confrontes",
            "other_countries": "pas de referentiel verifie : verification partielle",
        },
        "deliverability_values": {
            "residential": "adresse reconnue, usage residentiel (indicateur RDI de SmartyStreets ; par defaut sans RDI, et pour la France)",
            "commercial": "adresse reconnue, usage commercial (indicateur RDI de SmartyStreets)",
            "unknown": "information insuffisante pour conclure (code DPV X)",
            "invalid": "adresse inexistante ou verification echouee (code DPV N)",
        },
        "discovery": {
            "documentation": "https://kder-ai.com/docs",
            "openapi": "https://kder-ai.com/openapi.json",
            "health": "https://kder-ai.com/health",
            "metrics": "https://kder-ai.com/metrics",
            "revenue": "https://kder-ai.com/metrics/revenue",
            "manifest": "https://kder-ai.com/manifest",
            "mcp": f"{settings.public_base_url.rstrip('/')}/mcp",
            "mcp_server_card": f"{settings.public_base_url.rstrip('/')}/.well-known/mcp-server",
            "llms_txt": f"{settings.public_base_url.rstrip('/')}/llms.txt",
        },
        "contact": {
            "email": settings.contact_email,
        },
        "license": "MIT",
    }
