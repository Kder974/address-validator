"""`/guide` — guide d'intégration public (décision D48, élément 4).

La page qui répond à la question que se pose un client qui vient de recevoir sa
clé : « je fais quoi, maintenant ? ». Elle est **publique** (elle doit être
lisible et indexable par un inconnu), **autonome** (aucun script, aucune
ressource externe, la feuille de style partagée — même discipline que D43/D46) et
elle ne contient **aucun secret** : ni clé, ni identifiant, seulement des
exemples avec un `sk_live_…` tronqué.

Trois règles de rédaction, parce qu'un guide est un contrat :

1. **Ce qui est montré est ce qui est mesuré.** La base publique vient de
   `public_base_url` (jamais recopiée en dur), les corps d'exemple sont ceux de
   `app/schemas.py`, les en-têtes sont ceux que la route pose vraiment
   (`X-Payment-Mode`, `X-Plan`, `X-Quota-Remaining`), et les codes sont ceux de
   `app/routes/validate.py`. Un guide qui promet un en-tête que le serveur ne
   renvoie pas fait perdre une journée à un intégrateur.
2. **Les limites sont dites avec le reste** : quota **mensuel** en UTC, cache
   trente jours pour un verdict et **cinq minutes** pour un résultat dégradé
   (D47), plafond de l'essai gratuit, `402` qui n'est pas forcément un défaut de
   paiement (quota du mois), `503` qui n'est jamais une demande de paiement.
3. **Aucune astuce qui contourne la porte** : l'exemple de masse illustre le
   cache (une même adresse n'interroge le fournisseur qu'une fois) et le respect
   de `Retry-After`. Rien qui aide à fabriquer une preuve de paiement.
"""

from __future__ import annotations

import html
import re

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from app.config import get_settings
from app.web import document, public_headers, site_head

router = APIRouter()

#: Codes de retour, leur cause, et quoi faire. Les trois refus du mode clé
#: (401/403/402) sont volontairement distincts côté serveur (D45) : les confondre
#: ici annulerait le bénéfice.
CODES: tuple[tuple[str, str, str], ...] = (
    ("200", "Validation servie", "Lisez `result`. `cache_key` est votre pseudonyme de cette adresse : conservez-le pour demander un effacement."),
    ("401", "Clé API inconnue", "La clé est incomplète, révoquée (régénérée) ou destinée à un autre service. Corrigez la variable d'environnement, ne réessayez pas en boucle."),
    ("402", "Quota du mois consommé **ou** aucun paiement valide", "Lisez la réponse : `{\"reason\": \"quota_atteint\"}` = plafond d'usage atteint (le compteur repart le 1er du mois, UTC) ; `{\"x402Version\": 1}` = le paiement manque pour cet appel."),
    ("403", "Compte expiré", "L'essai gratuit est terminé et le compte est resté sur le plan d'essai. Passez à un plan payant (`/pricing`) : rien n'est prélevé automatiquement."),
    ("422", "Requête mal formée", "Un champ est absent ou vide. `street`, `city`, `state`, `zip` sont obligatoires ; `country` vaut `US` par défaut."),
    ("429", "Plafond d'essais gratuits atteint", "Concerne `/validate-form` (essai sans compte), pas l'API payante. Attendez le délai annoncé dans `Retry-After`, ou créez un compte."),
    ("500", "Paiement encaissé, mais registre injoignable", "Le service **refuse** de servir une validation qu'il ne peut pas inscrire : réessayez, et si l'appel était payé, citez `transaction_id` (le rapprochement est fait à la main)."),
    ("503", "Comptes clients ou base injoignable", "La clé n'a pas pu être vérifiée (ce n'est **pas** un refus) ou la base ne répond pas. Réessayez dans quelques minutes ; rien n'a été consommé."),
)


def _code(texte: str) -> str:
    """Bloc de code : **échappé** (le guide contient des guillemets et des `<>`)."""
    return (
        '<pre class="mono" style="white-space:pre-wrap;overflow-x:auto;'
        "background:#171a21;border:1px solid #242833;border-radius:8px;"
        f'padding:12px 14px">{html.escape(texte.strip(chr(10)))}</pre>'
    )


def _enrichi(texte: str) -> str:
    """Échappe le texte, puis rend les deux formes utilisées ici : `code`, **gras**."""
    sortie = html.escape(texte)
    for morceau in re.findall(r"`([^`]+)`", sortie):
        sortie = sortie.replace(f"`{morceau}`", f"<code>{morceau}</code>")
    for morceau in re.findall(r"\*\*([^*]+)\*\*", sortie):
        sortie = sortie.replace(f"**{morceau}**", f"<strong>{morceau}</strong>")
    return sortie


def _bloc(titre: str, corps: str, *, ancre: str) -> str:
    """Une section : titre avec ancre stable, puis son contenu."""
    return f'<h2 class="sec" id="{ancre}">{html.escape(titre)}</h2>\n{corps}\n'


def _tableau(tableau: tuple[tuple[str, ...], ...]) -> str:
    """Tableau HTML : la première ligne porte les en-têtes, le reste les lignes."""
    entetes, *lignes = tableau
    tete = "".join(f"<th>{_enrichi(cellule)}</th>" for cellule in entetes)
    corps = "".join(
        "<tr>" + "".join(f"<td>{_enrichi(cellule)}</td>" for cellule in ligne) + "</tr>"
        for ligne in lignes
    )
    return f"<table><thead><tr>{tete}</tr></thead><tbody>{corps}</tbody></table>"


def _page() -> HTMLResponse:
    """La page du guide : tout ce qu'un intégrateur doit savoir, sans secret."""
    reglages = get_settings()
    base = reglages.public_base_url.rstrip("/")
    prix = f"{reglages.x402_price:.2f}"

    une_minute = _bloc(
        "En une minute",
        f"""<ol class="per">
<li><strong>Obtenez une clé</strong> : <a href="/signup">créez un compte</a>
(14 jours et 100 validations offertes, sans carte bancaire). La clé
(<code>sk_live_…</code>) est affichée <strong>une seule fois</strong> ; ensuite elle
se régénère depuis <a href="/compte">l'espace client</a>.</li>
<li><strong>Appelez <code>POST /validate</code></strong> avec la clé dans l'en-tête
<code>X-API-Key</code>.</li>
<li><strong>Lisez le résultat</strong> : adresse standardisée, livrabilité,
coordonnées, et <code>cache_key</code>.</li>
</ol>
{_code(f'''curl -sS {base}/validate \\
  -H "X-API-Key: sk_live_VOTRE_CLE" \\
  -H "content-type: application/json" \\
  -d '{{"address": {{"street": "1 Rue de Paris", "city": "Saint-Denis", "state": "RE", "zip": "97400", "country": "RE"}}}}'
''')}
<p class="per">Pas encore de compte ? <a href="/validate-form">testez une adresse
gratuitement</a> : la page n'écrit rien, et vous montre exactement ce que l'API
renvoie.</p>""",
        ancre="en-une-minute",
    )

    authentification = _bloc(
        "S'authentifier",
        _tableau(
            (
                ("Vous êtes", "Ce que vous envoyez", "Ce que vous payez"),
                (
                    "Une boutique (abonnement)",
                    "`X-API-Key: sk_live_…` — la clé affichée à l'inscription, ou régénérée sur `/compte`",
                    "Le quota de votre plan (mensuel). **Rien** n'est encaissé à l'appel",
                ),
                (
                    "Un agent autonome (x402)",
                    "`X-PAYMENT` — une autorisation EIP-3009 signée, construite à partir du corps `402` (champ `accepts`)",
                    f"**{prix} USDC** par validation encaissée",
                ),
            )
        )
        + """<div class="note"><strong>La clé a la priorité.</strong> Si les deux
en-têtes partent ensemble, c'est l'abonnement qui tranche : un appel couvert par un
compte n'est <strong>jamais</strong> encaissé en USDC. Chaque réponse dit comment
elle a été payée (<code>X-Payment-Mode</code>).</div>
<p class="per">Ne mettez jamais la clé dans un dépôt git, un fichier partagé ou une
URL : elle vit dans une variable d'environnement ou un gestionnaire de secrets, et
circule uniquement en <code>https</code>. Une clé qui a fuité se régénère en un
clic ; l'ancienne cesse de fonctionner immédiatement.</p>""",
        ancre="authentification",
    )

    contrat = _bloc(
        "Le contrat",
        f"""<p class="per">Requête : <code>POST {base}/validate</code>, JSON,
<code>content-type: application/json</code>.</p>
{_code(
'''{"address": {
   "street": "1 Rue de Paris",   // requis
   "city": "Saint-Denis",        // requis
   "state": "RE",                // requis (region ou etat)
   "zip": "97400",               // requis
   "country": "RE"               // optionnel, "US" par defaut (ISO 2 lettres)
}}'''
)}
<p class="per">Réponse <code>200</code> :</p>
{_code(
'''{
  "result": {
    "validated": true,
    "standardized": {"line1": "1 RUE DE PARIS", "line2": "", "city": "SAINT-DENIS",
                     "state": "RE", "zip": "97400", "country": "RE"},
    "latitude": -20.8823, "longitude": 55.4504,
    "deliverability": "residential",
    "confidence": 0.99,
    "degraded": false,
    "cache_key": "9b7a..."
  },
  "price_paid": 0.0,
  "transaction_id": null
}'''
)}
"""
        + _tableau(
            (
                ("Champ", "Ce qu'il dit"),
                (
                    "`validated`",
                    "L'adresse a été reconnue par le fournisseur. **`false` n'est pas toujours un verdict** : lisez `degraded` avant d'en tirer une conclusion.",
                ),
                (
                    "`deliverability`",
                    "`residential`, `commercial`, `unknown` ou `invalid` — le code du fournisseur, traduit en clair.",
                ),
                (
                    "`confidence`",
                    "Indice entre 0 et 1 : **un indice, pas une garantie**. Choisissez votre seuil selon votre taux de retour accepté.",
                ),
                (
                    "`degraded`",
                    "`true` = le fournisseur n'a pas répondu (panne, délai dépassé, disjoncteur ouvert). La réponse ne dit **rien** de l'adresse : ne l'enregistrez jamais comme « adresse invalide ».",
                ),
                (
                    "`price_paid`",
                    "`0.0` pour un appel couvert par un abonnement ; le prix en USDC pour un appel payé.",
                ),
                (
                    "`cache_key`",
                    "Pseudonyme de l'adresse (SHA-256). Il sert au cache et à toute demande d'effacement : conservez-le, il ne contient pas l'adresse.",
                ),
            )
        )
        + _tableau(
            (
                ("En-tête de réponse", "Contenu"),
                ("`X-Payment-Mode`", "`api_key` ou `x402` — comment l'appel a été payé"),
                (
                    "`X-Plan`",
                    "`trial`, `starter`, `pro`, `enterprise` (mode clé seulement)",
                ),
                (
                    "`X-Quota-Remaining`",
                    "Validations restantes **ce mois-ci** (absent = plan illimité : l'absence dit « sans plafond »)",
                ),
            )
        ),
        ancre="contrat",
    )

    codes = _bloc(
        "Les codes de retour, et quoi faire",
        _tableau((("Code", "Situation", "Ce que vous faites"),) + CODES),
        ancre="codes",
    )

    cache = _bloc(
        "Ce qui n'est pas repayé, et ce qui est plafonné",
        _tableau(
            (
                ("Sujet", "Ce qui se passe", "Ce que vous en faites"),
                (
                    "Cache (verdict)",
                    "Une même adresse normalisée ne réinterroge pas le fournisseur pendant **30 jours** : c'est la clé `cache_key` qui le garantit",
                    "Vous pouvez appeler sans craindre de payer deux fois la même question. Pour forcer un nouvel appel, changez l'adresse (l'API ne devine pas).",
                ),
                (
                    "Cache (dégradé)",
                    "Un résultat `degraded` est gardé **5 minutes**, pas 30 jours (D47) : « le fournisseur n'a pas répondu » n'est pas un verdict",
                    "Traitez `degraded: true` comme « réessayez plus tard » : après la fenêtre courte, le même appel obtient une vraie réponse.",
                ),
                (
                    "Quota",
                    "**Mensuel**, fenêtre calendaire **UTC** : il repart le 1er du mois, pas 30 jours après votre premier appel",
                    "Lisez `X-Quota-Remaining` et surveillez-le ; écrivez-nous avant la limite plutôt qu'après.",
                ),
                (
                    "Prix à l'appel",
                    f"**{prix} USDC** par validation encaissée (mode x402). Un appel couvert par un abonnement annonce `price_paid: 0.0`",
                    "Un agent qui paie doit lire le corps `402` (`accepts`) : le prix y est, il n'est jamais à deviner.",
                ),
                (
                    "Essai sans compte",
                    f"**{reglages.validate_form_rate_limit_per_day} validations par jour et par visiteur** sur `/validate-form`",
                    "Pour un usage régulier, créez un compte : `100` validations offertes, puis un plan mensuel.",
                ),
            )
        ),
        ancre="cache-et-quotas",
    )

    exemple_csv = """\"\"\"Verifie un CSV d'adresses et ecrit le verdict a cote.

Le fournisseur n'est interroge qu'une fois par adresse : la reponse est mise en
cache (30 jours pour un verdict), donc une seconde execution sur le meme fichier
ne coute rien de plus.
\"\"\"
import csv
import os
import time

import requests

CLE = os.environ["ADDRESS_VALIDATOR_KEY"]  # jamais dans le code, jamais dans git
API = "BASE_URL/validate"

with open("colis.csv", newline="", encoding="utf-8") as source, open(
    "colis-verifies.csv", "w", newline="", encoding="utf-8"
) as cible:
    lecteur = csv.DictReader(source)
    champs = list(lecteur.fieldnames) + [
        "validated", "deliverability", "degraded", "note",
    ]
    sortie = csv.DictWriter(cible, fieldnames=champs)
    sortie.writeheader()
    for ligne in lecteur:
        reponse = requests.post(
            API,
            headers={"X-API-Key": CLE},
            timeout=30,
            json={
                "address": {
                    "street": ligne["adresse"],
                    "city": ligne["ville"],
                    "state": ligne["region"],
                    "zip": ligne["code_postal"],
                    "country": ligne.get("pays") or "US",
                }
            },
        )
        if reponse.status_code in (500, 503):
            time.sleep(5)  # panne cote service : rien n'a ete consomme
            continue
        if reponse.status_code == 429:
            time.sleep(int(reponse.headers.get("Retry-After", "60")))
            continue
        if reponse.status_code != 200:
            sortie.writerow({**ligne, "note": f"refus {reponse.status_code}"})
            continue
        resultat = reponse.json()["result"]
        sortie.writerow(
            {
                **ligne,
                "validated": resultat["validated"],
                "deliverability": resultat["deliverability"],
                "degraded": resultat["degraded"],
                "note": "a reverifier (fournisseur injoignable)"
                if resultat["degraded"]
                else "",
            }
        )
"""

    pratiques = _bloc(
        "Cas pratiques",
        "<h3>1. Appel direct (PowerShell)</h3>"
        + _code(
            """$cle = $env:ADDRESS_VALIDATOR_KEY
$corps = '{"address":{"street":"1 Rue de Paris","city":"Saint-Denis","state":"RE","zip":"97400","country":"RE"}}'
$r = Invoke-WebRequest -Uri BASE_URL/validate -Method POST `
     -Headers @{ "X-API-Key" = $cle } -ContentType "application/json" -Body $corps
$r.StatusCode
$r.Headers["X-Payment-Mode"], $r.Headers["X-Plan"], $r.Headers["X-Quota-Remaining"]
($r.Content | ConvertFrom-Json).result
""".replace("BASE_URL", base)
        )
        + "<h3>2. Import CSV (Python)</h3>"
        + _code(exemple_csv.replace("BASE_URL", base))
        + "<p class=\"per\">Ce que l'exemple illustre, et rien d'autre : la clé vient "
        "d'une variable d'environnement, `429` est respecté (le délai est *lu*, pas "
        "deviné), et un résultat `degraded` est marqué « à re-vérifier » au lieu "
        "d'être écrit comme une adresse invalide.</p>"
        + "<h3>3. Shopify</h3>"
        + _tableau(
            (
                ("Étape", "Ce que vous branchez"),
                (
                    "Déclencheur",
                    "Un webhook `orders/create` (ou votre étape de préparation de colis) appelle votre service, qui interroge `/validate` avec l'adresse de livraison de la commande.",
                ),
                (
                    "Correspondance des champs",
                    "`shipping_address.address1` → `street` (ajoutez `address2` à `line2` côté résultat) ; `city` → `city` ; `province_code` → `state` ; `zip` → `zip` ; `country_code` → `country`.",
                ),
                (
                    "Décision",
                    "`validated: false` : signalez la commande dans votre back-office **avant** l'expédition. `degraded: true` : ne bloquez rien, re-programmez un contrôle.",
                ),
                (
                    "Ce que nous ne fournissons pas",
                    "Aucune application Shopify prête à installer : l'API est le contrat, l'appel vient de **votre** service. C'est ce qui vous laisse décider de votre seuil de confiance.",
                ),
            )
        )
        + "<h3>4. WooCommerce</h3>"
        + _tableau(
            (
                ("Étape", "Ce que vous branchez"),
                (
                    "Déclencheur",
                    "L'action `woocommerce_checkout_order_processed` (commande payée) ou un cron qui parcourt les commandes en préparation (`wc_get_orders( ['status' => 'processing'] )`).",
                ),
                (
                    "Correspondance des champs",
                    "`$order->get_shipping_address_1()` → `street` ; `$order->get_shipping_city()` → `city` ; `$order->get_shipping_state()` → `state` ; `$order->get_shipping_postcode()` → `zip` ; `$order->get_shipping_country()` → `country`.",
                ),
                (
                    "Traçabilité",
                    "Enregistrez `cache_key` avec la commande : c'est le seul identifiant de l'adresse valable pour une demande d'effacement.",
                ),
            )
        )
        + """<p class="per">Dans tous les cas : un seul appel par adresse et par
commande, jamais dans une boucle qui ignore les codes de retour. Un `402` de quota
n'est pas une panne — c'est une limite, et elle se lit dans la réponse.</p>""",
        ancre="cas-pratiques",
    )

    body = f"""{site_head()}<div class="site">
<h1>Guide d'intégration</h1>
<p class="lead">De la clé API à la production : authentification, contrat,
codes de retour, cache, quotas — et quatre cas pratiques (appel direct, import
CSV, Shopify, WooCommerce).</p>
<nav class="per">
<a href="#en-une-minute">En une minute</a> —
<a href="#authentification">S'authentifier</a> —
<a href="#contrat">Le contrat</a> —
<a href="#codes">Les codes</a> —
<a href="#cache-et-quotas">Cache et quotas</a> —
<a href="#cas-pratiques">Cas pratiques</a>
</nav>
{une_minute}{authentification}{contrat}{codes}{cache}{pratiques}
<div class="note"><strong>Ce que nous conservons, et ce que nous ne conservons
pas :</strong> pour un appel payant ou abonné, une empreinte de l'adresse
(<code>cache_key</code>) et l'issue, jamais l'adresse elle-même ; pour l'essai
gratuit sans compte, <strong>rien</strong> — seulement un compteur anonyme qui fait
respecter le plafond. Une demande d'effacement se fait avec le
<code>cache_key</code>.</div>
<div class="actions">
  <a class="btn primary" href="/signup">Créer un compte</a>
  <a class="btn ghost" href="/pricing">Voir les tarifs</a>
  <a class="btn ghost" href="/validate-form">Tester une adresse</a>
</div>
<p class="per">Pour aller plus loin :
<a href="/docs">documentation interactive</a> (OpenAPI, essayable),
<a href="/manifest">/manifest</a> (description x402 lisible par un agent), et
<a href="/llms.txt">/llms.txt</a> (résumé court pour les moteurs et les agents).</p>
</div>"""

    return HTMLResponse(
        content=document(
            "Guide d'intégration — Address Validator",
            body,
            description=(
                "Intégrer l'API Address Validator : clé API ou paiement x402, "
                "contrat JSON, codes de retour, cache et quotas, exemples "
                "Shopify, WooCommerce et import CSV."
            ),
        ),
        status_code=200,
        headers=public_headers(form=False, max_age=300),
    )


@router.get("/guide")
async def guide() -> Response:
    """Le guide public : indexable, cachable, sans secret et sans ressource externe."""
    return _page()






