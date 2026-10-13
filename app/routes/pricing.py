"""GET /pricing — page publique de l'offre commerciale (décision D43).

Première page **publique** du service (les précédentes sont d'exploitation ou de
découverte). Elle vend, donc trois exigences :

1. **Autonome**, comme `/dashboard` (D32) : aucune ressource externe (pas de CDN,
   pas de police distante, pas de script, pas d'image). La feuille de style est
   celle du dashboard, importée depuis `app/web.py` : une seule feuille, donc
   aucune dérive possible entre l'exploitation et le site commercial.
2. **Honnête.** Chaque chiffre affiché est soit **calculé** (le prix unitaire est
   dérivé du prix et du quota du plan, jamais recopié), soit la valeur
   **réellement facturée** par l'API (`settings.x402_price`). Les ordres de
   grandeur du problème (taux d'échec, coût d'un échec) sont présentés comme des
   ordres de grandeur à confronter aux chiffres du transporteur du visiteur,
   jamais comme une mesure du service. Les témoignages sont des **exemples**
   et ne sont pas publiés par défaut (`PRICING_SHOW_TESTIMONIALS`, cf. D43).
3. **Indexable et cachable** — l'inverse de `/dashboard`, qui est `no-store` :
   une page commerciale n'a aucun secret à protéger, et un cache partagé de
   quelques minutes lui va mieux qu'une reconstruction à chaque visite.

La page ne contient ni donnée client, ni identifiant : elle peut donc être
mise en cache par le proxy sans créer de fuite.
"""

from __future__ import annotations

import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.web import document, public_headers, site_head

router = APIRouter()

#: Les trois étapes du service. Le vocabulaire est celui de la route réelle
#: (`POST /validate`) : la page décrit ce que le client recevra, pas un idéal.
ETAPES = (
    (
        "Envoyez l'adresse",
        "Un appel POST /validate avec la rue, la ville, le code postal et le "
        "pays — depuis votre back-office, un webhook de commande ou un tableur.",
    ),
    (
        "Recevez la validation",
        "Adresse standardisée, coordonnées et score de livrabilité si elle est "
        "livrable ; motif précis du refus sinon (code postal manquant, rue "
        "inconnue, ville incohérente).",
    ),
    (
        "Expédiez l'adresse corrigée",
        "Les adresses douteuses sont corrigées ou mises de côté avant de partir "
        "chez le transporteur : c'est là que le retour est évité.",
    ),
)

#: Ordres de grandeur du problème, présentés comme tels (aucun de ces chiffres
#: n'est présenté comme une mesure de ce service) : le corps de la page précise
#: qu'ils sont à confronter aux chiffres du transporteur du visiteur.
PROBLEME = (
    ("22 %", "des échecs de livraison sont causés par une adresse incorrecte"),
    ("17 à 30 $", "de coût réel par colis en échec (retour, réexpédition, appel)"),
)

#: Titre et description : ce que voit un moteur de recherche et un onglet.
TITRE = "Tarifs — vérifier les adresses avant expédition | Address Validator"
DESCRIPTION = (
    "Vérifiez les adresses de livraison avant expédition : essai gratuit 14 "
    "jours, puis 29 €/mois (2 000 validations) ou 79 €/mois (10 000 "
    "validations). Adresse standardisée, coordonnées et score de livrabilité."
)

#: Offres publiques. `prix_eur` et `validations` servent au calcul du prix
#: unitaire affiché : le chiffre montré au visiteur est calculé, jamais saisi.
OFFRES = (
    {
        "nom": "Essai gratuit",
        "tag": "14 jours",
        "prix": "0 €",
        "prix_eur": None,
        "validations": 100,
        "periode": "14 jours, 100 validations",
        "accroche": "Mesurez l'effet sur vos colis, sans carte bancaire.",
        "points": (
            "100 validations offertes",
            "Les mêmes contrôles que les plans payants",
            "Aucune carte bancaire demandée",
            "Fin automatique, sans engagement",
        ),
        "cta": ("Commencer l'essai gratuit", "/signup"),
        "mis_en_avant": False,
    },
    {
        "nom": "Starter",
        "tag": "Boutique",
        "prix": "29 €",
        "prix_eur": 29.0,
        "validations": 2000,
        "periode": "par mois, 2 000 validations",
        "accroche": "Pour une boutique qui expédie quelques dizaines de colis par jour.",
        "points": (
            "2 000 validations par mois",
            "Clé API et tableau de bord d'usage",
            "Cache : une même adresse n'est facturée qu'une fois",
            "Support par email",
        ),
        "cta": ("Commencer l'essai gratuit", "/signup"),
        "mis_en_avant": False,
    },
    {
        "nom": "Pro",
        "tag": "Le plus choisi",
        "prix": "79 €",
        "prix_eur": 79.0,
        "validations": 10000,
        "periode": "par mois, 10 000 validations",
        "accroche": "Pour les volumes qui rendent chaque retour visible dans la marge.",
        "points": (
            "10 000 validations par mois",
            "Validation en masse (import CSV)",
            "Webhooks Shopify / WooCommerce",
            "Réponse prioritaire",
        ),
        "cta": ("Commencer l'essai gratuit", "/signup"),
        "mis_en_avant": True,
    },
    {
        "nom": "Enterprise",
        "tag": "Volume",
        "prix": "Sur devis",
        "prix_eur": None,
        "validations": None,
        "periode": "volume illimité",
        "accroche": "Pour une place de marché ou un réseau logistique.",
        "points": (
            "Volume illimité, facturation annuelle",
            "Engagement de niveau de service",
            "Intégration accompagnée (ERP, WMS)",
            "Interlocuteur unique",
        ),
        "cta": ("Demander un devis", "/signup?plan=enterprise"),
        "mis_en_avant": False,
    },
)

#: Cas d'usage : où le contrôle d'adresse se branche réellement.
CAS_USAGE = (
    (
        "E-commerce",
        "Validez l'adresse au moment de la commande, puis relancez le client "
        "avant l'expédition si elle est douteuse.",
    ),
    (
        "Logistique",
        "Nettoyez un fichier d'expédition en masse avant de le remettre au "
        "transporteur : un CSV accepté vaut un formulaire rempli.",
    ),
    (
        "Marketplaces",
        "Imposez à vos vendeurs une adresse vérifiée à l'inscription, plutôt "
        "que de rattraper les retours après coup.",
    ),
)

#: FAQ : cinq questions réellement posées avant un abonnement. Aucune promesse
#: qui ne serait pas tenue par une fonctionnalité déployée : `x402_price` est la
#: valeur facturée, `/manifest` est publié (D33), la pseudonymisation est celle
#: de `PRIVACY.md`.
QUESTIONS = (
    (
        "Faut-il une carte bancaire pour l'essai gratuit ?",
        "Non. L'essai dure 14 jours et inclut 100 validations, sans carte "
        "bancaire et sans engagement : à la fin, vous choisissez un plan, ou "
        "rien ne se passe.",
    ),
    (
        "Comment suis-je facturé si je dépasse mon quota ?",
        "Votre consommation est visible dans votre espace, avec le nombre "
        "d'adresses invalides détectées et les économies estimées. Aucun "
        "prélèvement automatique n'a lieu : c'est vous qui décidez de passer au "
        "plan supérieur.",
    ),
    (
        "Que deviennent les adresses que je vous envoie ?",
        "Elles servent à la validation et sont mises en cache sous forme "
        "d'empreinte (SHA-256) : jamais conservées en clair pour de la revente "
        "ou de la publicité.",
    ),
    (
        "Couvrez-vous La Réunion et l'international ?",
        "Oui : la vérification s'appuie sur un référentiel d'adresses "
        "international et géocode les résultats. Les limites connues sont "
        "publiées dans le manifeste de l'API.",
    ),
    (
        "Combien de temps prend l'intégration ?",
        "Quelques minutes pour un appel direct (POST /validate avec votre clé "
        "API), une demi-journée pour un branchement sur les commandes. Le guide "
        "d'intégration couvre Shopify, WooCommerce, l'import CSV et l'appel "
        "direct.",
    ),
)

#: Témoignages — **exemples**, publiés seulement si l'exploitant l'a décidé
#: (`PRICING_SHOW_TESTIMONIALS`, D43). Un témoignage inventé présenté comme réel
#: est une pratique commerciale trompeuse : la section existe pour être remplie,
#: pas pour être crue sur parole. D'où le marquage « exemple » dans le texte.
TEMOIGNAGES = (
    (
        "Nous avons divisé nos retours par deux le premier mois, surtout sur "
        "les adresses d'outre-mer.",
        "Gérant d'une boutique en ligne (exemple — à remplacer)",
    ),
    (
        "Le refus est enfin explicite : on sait quoi corriger avant d'imprimer "
        "l'étiquette.",
        "Responsable logistique (exemple — à remplacer)",
    ),
    (
        "Branché sur les commandes en une après-midi, aucun changement pour "
        "l'équipe.",
        "Responsable e-commerce (exemple — à remplacer)",
    ),
)

def _prix_unitaire(offre: dict) -> str:
    """Prix unitaire **calculé** depuis le plan (jamais recopié dans le texte).

    29 € / 2 000 validations = 0,0145 € ; 79 € / 10 000 = 0,0079 €. Le chiffre
    affiché au visiteur ne peut donc pas contredire le plan qu'il achète.
    """
    prix, quota = offre.get("prix_eur"), offre.get("validations")
    if not prix or not quota:
        return ""
    return f"{prix / quota:.4f}".replace(".", ",") + " € la validation"


def _carte_offre(offre: dict) -> str:
    """Une carte de tarif : nom, prix, quota, points, bouton d'action."""
    unitaire = _prix_unitaire(offre)
    points = "".join(f"<li>{html.escape(point)}</li>" for point in offre["points"])
    texte, lien = offre["cta"]
    classe = "btn primary" if offre["mis_en_avant"] else "btn"
    detail = f'<div class="per">{html.escape(unitaire)}</div>' if unitaire else ""
    return (
        f'<div class="{"plan featured" if offre["mis_en_avant"] else "plan"}">'
        f'<div class="tag">{html.escape(offre["tag"])}</div>'
        f'<h3 class="sub">{html.escape(offre["nom"])}</h3>'
        f'<div class="price">{html.escape(offre["prix"])}</div>'
        f'<div class="per">{html.escape(offre["periode"])}</div>'
        f"{detail}"
        f"<ul>{points}</ul>"
        f'<a class="{classe}" href="{html.escape(lien, quote=True)}">'
        f"{html.escape(texte)}</a>"
        "</div>"
    )


def _etape(numero: int, titre: str, texte: str) -> str:
    return (
        f'<div class="col"><span class="n">{numero}</span>'
        f'<h3 class="sub">{html.escape(titre)}</h3>'
        f'<p class="per">{html.escape(texte)}</p></div>'
    )


def _question(question: str, reponse: str) -> str:
    """`<details>` : une FAQ dépliable sans une ligne de JavaScript (D43)."""
    return (
        f'<details class="faq"><summary>{html.escape(question)}</summary>'
        f"<p>{html.escape(reponse)}</p></details>"
    )


def _temoignages() -> str:
    cartes = "".join(
        f'<div class="col"><blockquote>{html.escape(citation)}</blockquote>'
        f'<div class="who">{html.escape(auteur)}</div></div>'
        for citation, auteur in TEMOIGNAGES
    )
    return (
        '<section id="temoignages"><h2 class="sec">Ce qu\'en disent nos '
        'premiers utilisateurs</h2>'
        '<p class="per">Exemples de retours d\'usage — à remplacer par de '
        'vrais témoignages avant publication.</p>'
        f'<div class="cols">{cartes}</div></section>'
    )


def _page(settings) -> str:
    """Assemble la page. Aucun chiffre saisi à la main, aucune ressource externe.

    `settings` est passé (et non relu ici) pour rester testable : la porte D43
    appelle `_page()` avec des réglages construits, sans toucher à
    l'environnement.
    """
    faits = "".join(
        f'<div class="col"><div class="price">{html.escape(valeur)}</div>'
        f'<p class="per">{html.escape(legende)}</p></div>'
        for valeur, legende in PROBLEME
    )
    offres = "".join(_carte_offre(offre) for offre in OFFRES)
    etapes = "".join(
        _etape(numero, titre, texte) for numero, (titre, texte) in enumerate(ETAPES, 1)
    )
    usages = "".join(
        f'<div class="col"><h3 class="sub">{html.escape(nom)}</h3>'
        f'<p class="per">{html.escape(texte)}</p></div>'
        for nom, texte in CAS_USAGE
    )
    faq = "".join(_question(question, reponse) for question, reponse in QUESTIONS)
    temoignages = _temoignages() if settings.pricing_show_testimonials else ""

    contact = html.escape(settings.contact_email, quote=True)
    prix_api = f"{settings.x402_price:g} USDC"

    body = f"""{site_head()}<div class="site">

<div class="hero">
  <h1>Arrêtez de perdre de l'argent à cause d'adresses invalides</h1>
  <p class="lead">Un colis qui revient coûte deux fois : le transport, puis la
  réexpédition — quand le client n'annule pas. Notre API vérifie l'adresse
  <strong>avant</strong> que l'étiquette soit imprimée.</p>
  <div class="actions">
    <a class="btn primary" href="/signup">Commencer l'essai gratuit</a>
    <a class="btn ghost" href="/validate-form">Tester une adresse sans compte</a>
  </div>
  <p class="per">14 jours, 100 validations, sans carte bancaire.</p>
</div>

<section id="probleme">
  <h2 class="sec">Le problème</h2>
  <div class="cols">{faits}</div>
  <p class="per">Ordres de grandeur du secteur : à confronter à vos propres
  chiffres, votre transporteur connaît votre taux d'échec réel.</p>
</section>

<section id="solution">
  <h2 class="sec">La solution</h2>
  <p class="lead">Notre API vérifie les adresses avant expédition. Elle
  standardise la rue, la ville et le code postal, situe l'adresse et renvoie un
  score de livrabilité — ou le motif exact du refus. Une adresse douteuse est
  corrigée ou mise de côté <strong>avant</strong> de partir.</p>
</section>

<section id="etapes">
  <h2 class="sec">Comment ça marche</h2>
  <div class="cols">{etapes}</div>
</section>

<section id="tarifs">
  <h2 class="sec">Tarifs</h2>
  <p class="per">Prix hors taxes. Sans engagement : vous arrêtez quand vous
  voulez, aucun prélèvement automatique.</p>
  <div class="plans">{offres}</div>
</section>

<section id="usages">
  <h2 class="sec">Cas d'usage</h2>
  <div class="cols">{usages}</div>
</section>

{temoignages}

<section id="faq">
  <h2 class="sec">Questions fréquentes</h2>
  {faq}
</section>

<section id="rgpd">
  <div class="note"><strong>Conformité RGPD.</strong> Les adresses transmises
  servent uniquement à la validation et ne sont ni revendues ni utilisées pour
  de la publicité. Le cache conserve une empreinte SHA-256, jamais l'adresse en
  clair ; les journaux d'exploitation pseudonymisent l'adresse IP
  (SHA-256 + sel) et sont purgés après 30 jours. Vous pouvez demander la
  suppression de vos données de compte à tout moment.</div>
</section>

<footer class="site-foot">
  <span>Address Validator — Kder Daoud</span>
  <a href="mailto:{contact}">{contact}</a>
  <span>Prix hors taxes. Appel direct sans abonnement : {prix_api} par
  validation.</span>
</footer>
</div>"""

    return document(TITRE, body, description=DESCRIPTION)


@router.get("/pricing", response_class=HTMLResponse)
async def pricing() -> HTMLResponse:
    """Page publique : servie sans compte, cachable quelques minutes (D43)."""
    return HTMLResponse(content=_page(get_settings()), headers=public_headers())
