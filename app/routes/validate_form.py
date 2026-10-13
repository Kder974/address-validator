"""`/validate-form` — l'essai gratuit public (décision D46, élément 3).

Ce que la page fait : valider **une** adresse, gratuitement et sans compte, avec
le **même** pipeline que la route payante (`perform_validation`, partagé avec
`app/routes/validate.py`) — même réponse, même cache, donc aucune divergence
possible entre ce qu'on montre à l'essai et ce qu'on vend.

Ce qu'elle ne fait pas, et c'est aussi une décision :

  * **elle n'écrit rien.** Aucune ligne en base : ni `address_validations`, ni le
    registre des paiements (D29). Seuls les compteurs de `/metrics` avancent. Un
    essai gratuit inscrit au registre ferait mentir le revenu publié (D40), et
    une adresse de visiteur conservée serait une donnée gardée sans raison
    (`PRIVACY.md`) ;
  * **elle ne parle pas à la place du service vendu.** Le résultat est montré
    avec ses limites : `degraded` dit que le fournisseur n'a pas pu être
    interrogé, donc la réponse n'affirme **rien** sur l'adresse — un visiteur ne
    doit pas croire que son adresse est fausse parce qu'un tiers est tombé ;
  * **elle est plafonnée, et le dit.** `validate_form_rate_limit_per_day` essais
    par IP et par 24 h (10 par défaut, `app/ratelimit.py` : Redis quand il est là,
    repli local **compté** sinon). Le refus est un `429` qui donne le temps
    d'attente réel (`Retry-After`, calculé sur la fenêtre en cours) et propose
    l'étape suivante — créer un compte. Un plafond muet serait un mur ; un plafond
    absent ouvrirait le fournisseur payant à tout Internet ;
  * **elle est autonome** : aucun script, aucune ressource externe, la feuille de
    style partagée (`app/web.py`), `form-action 'self'`. C'est la page qu'un
    visiteur voit en premier : elle ne charge rien, et c'est vérifiable par le
    navigateur (CSP) plutôt que par la seule lecture du code.

Enfin, une page qui affiche une adresse ne se met **pas** en cache : la réponse
d'un `POST` porte `PRIVATE_HEADERS` (`no-store`, `noindex`), tandis que le
formulaire vide est indexable et cachable quelques minutes — il ne contient
aucune donnée client.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app import ratelimit
from app.config import get_settings
from app.observability import client_ip
from app.routes.metrics import metrics
from app.routes.validate import perform_validation
from app.web import PRIVATE_HEADERS, document, public_headers, site_head

router = APIRouter()

#: Fenêtre du plafond d'essais gratuits : une journée annoncée comme telle
#: (« 10 essais par jour »). La fenêtre est fixe et démarre au premier essai, ce
#: que la page dit au visiteur pour qu'il n'ait pas à le deviner.
FENETRE_ESSAI_SECONDES = 24 * 60 * 60

#: Champs du formulaire, dans l'ordre d'une adresse postale : `(nom, libellé,
#: exemple)`. Ce sont ceux de l'API payante (`app/schemas.py`) — une adresse qui
#: passe ici passe là-bas.
CHAMPS: tuple[tuple[str, str, str], ...] = (
    ("street", "Numéro et rue", "1 Rue de Paris"),
    ("city", "Ville", "Saint-Denis"),
    ("state", "Région ou état", "RE (La Réunion), CA, Île-de-France…"),
    ("zip", "Code postal", "97400"),
    ("country", "Pays (2 lettres)", "US"),
)

#: Pays retenu quand le visiteur laisse le champ vide : la même valeur par défaut
#: que l'API payante (`app/schemas.py`, `AddressInput.country`).
PAYS_PAR_DEFAUT = "US"


def _champ(nom: str, libelle: str, exemple: str, valeurs: dict[str, str]) -> str:
    """Un champ du formulaire, réaffiché tel que le visiteur l'a saisi.

    La valeur est échappée : elle vient du visiteur, donc elle n'entre jamais
    telle quelle dans la page (même règle que partout ailleurs).
    """
    return (
        f'<p><label for="{nom}">{html.escape(libelle)}</label><br>\n'
        f'   <input id="{nom}" name="{nom}" type="text" size="34" '
        f'placeholder="{html.escape(exemple, quote=True)}" '
        f'value="{html.escape(valeurs.get(nom, ""), quote=True)}"></p>'
    )

def _verdict(resultat: dict) -> str:
    """Ce que le résultat affirme — et rien de plus.

    `degraded` est traité **à part** : dans ce cas la réponse ne dit pas que
    l'adresse est invalide, elle dit que personne n'a pu la vérifier. Confondre
    les deux ferait porter au visiteur la panne d'un tiers.
    """
    if resultat.get("degraded"):
        return (
            '<p class="lead">Vérification impossible pour l\'instant : le '
            "fournisseur d'adresses n'a pas répondu. La réponse ci-dessous "
            "n'est <strong>pas</strong> un jugement sur votre adresse.</p>"
        )
    if resultat.get("validated"):
        return (
            '<p class="lead"><span class="ok">Adresse reconnue</span> — le '
            "transporteur devrait livrer à cette adresse.</p>"
        )
    return (
        '<p class="lead"><span class="ko">Adresse non reconnue</span> — vérifiez '
        "le numéro, la voie et le code postal avant d'expédier à cette adresse.</p>"
    )


def _resultat_html(resultat: dict, restants: int) -> str:
    """Le résultat de l'essai : adresse standardisée, livrabilité, limites dites."""
    standard = resultat.get("standardized") or {}
    lignes = [
        standard.get("line1") or "",
        standard.get("line2") or "",
        " ".join(part for part in (standard.get("zip"), standard.get("city")) if part),
        standard.get("state") or "",
        standard.get("country") or "",
    ]
    adresse_std = "<br>".join(html.escape(ligne) for ligne in lignes if ligne)

    latitude, longitude = resultat.get("latitude"), resultat.get("longitude")
    coordonnees = ""
    if latitude is not None and longitude is not None:
        coordonnees = (
            '<div class="col"><div class="tag">Coordonnées</div>'
            f'<div class="price mono" style="font-size:16px">{float(latitude):.5f}, '
            f'{float(longitude):.5f}</div>'
            '<p class="per">obtenues par géocodage, à titre indicatif</p></div>'
        )

    degrade = ""
    if resultat.get("degraded"):
        degrade = (
            '<div class="note"><strong>Résultat dégradé :</strong> le fournisseur '
            "d'adresses n'a pas pu être interrogé (panne, délai dépassé ou "
            "disjoncteur ouvert). Cette réponse ne dit rien de l'adresse : elle "
            "n'est pas une preuve d'inexistence. Réessayez plus tard.</div>"
        )

    if restants > 0:
        suite = (
            f'<p class="per">Il vous reste {restants} essai(s) gratuit(s) '
            "aujourd'hui. Pour un usage régulier : "
            '<a href="/signup">créez un compte</a> (14 jours et 100 validations '
            "offertes), ou appelez l'API payante (0,02 USDC la validation).</p>"
        )
    else:
        suite = (
            "<p class=\"per\">C'était votre dernier essai gratuit de la journée. La "
            "fenêtre de 24 h se referme au plus tard 24 h après votre premier "
            "essai : <a href=\"/signup\">créez un compte</a> pour continuer "
            "maintenant (100 validations offertes, sans carte bancaire).</p>"
        )

    return f"""<h2 class="sec">Résultat</h2>
{_verdict(resultat)}
{degrade}
<div class="cols">
  <div class="col"><div class="tag">Adresse standardisée</div>
    <p class="mono">{adresse_std or "—"}</p></div>
  <div class="col"><div class="tag">Livrabilité</div>
    <div class="price">{html.escape(str(resultat.get("deliverability") or "inconnue"))}</div>
    <p class="per">indice de confiance
    {float(resultat.get("confidence") or 0.0):.2f}</p></div>
  {coordonnees}
</div>
{suite}
<div class="note"><strong>Ce que nous gardons :</strong> rien. Ni votre adresse, ni
votre IP en clair : l'essai ne laisse qu'un compteur anonyme pour faire respecter
le plafond, et le résultat n'est pas enregistré. L'API payante, elle, conserve
l'empreinte de l'adresse et l'issue — jamais l'adresse elle-même.</div>"""



def _erreurs(adresse: dict[str, str]) -> list[str]:
    """Ce qui manque pour interroger le pipeline — mêmes champs que l'API payante.

    Les cinq champs sont obligatoires côté API payante (`app/schemas.py`) : les
    exiger ici aussi évite d'afficher un verdict calculé sur une adresse
    incomplète, qui serait un faux verdict.
    """
    erreurs = [
        f"{libelle} : champ requis."
        for nom, libelle, _exemple in CHAMPS
        if nom != "country" and not adresse.get(nom)
    ]
    if len(adresse.get("country", "")) != 2:
        erreurs.append("Pays : deux lettres attendues (US, FR, RE…).")
    return erreurs


def _page(
    *,
    adresse: dict[str, str] | None = None,
    resultat: dict | None = None,
    restants: int | None = None,
    erreurs: tuple[str, ...] = (),
    statut: int = 200,
) -> HTMLResponse:
    """La page d'essai : trois états, deux régimes d'en-têtes.

    Le formulaire vide ne contient aucune donnée client : il est indexable et
    cachable quelques minutes. Dès qu'un `POST` a eu lieu, la réponse contient une
    adresse — elle porte `PRIVATE_HEADERS` (`no-store`, `noindex`), parce qu'un
    proxy qui garderait cette page garderait l'adresse du visiteur.
    """
    valeurs = adresse or {}
    reglages = get_settings()
    prive = bool(erreurs) or resultat is not None or statut != 200

    bloc_erreurs = ""
    if erreurs:
        items = "".join(f"<li>{html.escape(e)}</li>" for e in erreurs)
        bloc_erreurs = f'<div class="note"><ul>{items}</ul></div>'

    champs = "".join(
        _champ(nom, libelle, exemple, valeurs) for nom, libelle, exemple in CHAMPS
    )
    bloc_resultat = (
        _resultat_html(resultat, restants or 0) if resultat is not None else ""
    )

    body = f"""{site_head()}<div class="site">
<h1>Tester une adresse, gratuitement</h1>
<p class="lead">{reglages.validate_form_rate_limit_per_day} validations offertes par
jour et par visiteur, sans compte et sans carte bancaire. Le résultat s'affiche
ci-dessous : adresse standardisée, livrabilité, coordonnées.</p>
{bloc_erreurs}
<form method="post" action="/validate-form" class="col">
  {champs}
  <p><button class="btn primary" type="submit">Vérifier cette adresse</button></p>
</form>
{bloc_resultat}
<div class="note"><strong>Comment c'est plafonné :</strong>
{reglages.validate_form_rate_limit_per_day} essais par visiteur et par 24 h. La
fenêtre démarre à votre premier essai et ne se prolonge pas à chaque tentative ;
au-delà, la page vous donne le temps d'attente exact et propose de créer un compte.
<strong>Pourquoi un plafond :</strong> chaque vérification interroge un fournisseur
payant — c'est ce plafond qui permet d'offrir l'essai sans ouvrir le fournisseur à
tout Internet.</div>
<div class="actions">
  <a class="btn primary" href="/signup">Créer un compte (100 validations offertes)</a>
  <a class="btn ghost" href="/pricing">Voir les tarifs</a>
</div>
</div>"""

    return HTMLResponse(
        content=document(
            "Tester une adresse — Address Validator",
            body,
            description=(
                "Vérifiez gratuitement une adresse de livraison : adresse "
                "standardisée, livrabilité et coordonnées, sans compte."
            ),
            robots="noindex, nofollow" if prive else "index, follow",
        ),
        status_code=statut,
        headers=PRIVATE_HEADERS if prive else public_headers(form=True, max_age=300),
    )


def _trop_d_essais(limite: int, secondes: int) -> HTMLResponse:
    """`429` : le plafond d'essais gratuits est atteint, et il dit quand il tombe."""
    minutes = max(1, (secondes + 59) // 60)
    body = f"""{site_head()}<div class="site">
<h1>Plafond d'essais gratuits atteint</h1>
<p class="lead">{limite} essais gratuits par jour et par visiteur : c'est le
garde-fou qui permet d'offrir l'essai sans ouvrir le fournisseur payant à tout
Internet.</p>
<div class="note"><strong>Votre fenêtre de 24 h se referme dans environ
{minutes} minute(s)</strong> — elle démarre au premier essai et ne se prolonge pas
à chaque tentative. Vous pouvez aussi continuer tout de suite avec un compte :
100 validations offertes, 14 jours, sans carte bancaire.</div>
<div class="actions">
  <a class="btn primary" href="/signup">Créer un compte</a>
  <a class="btn ghost" href="/pricing">Voir les tarifs</a>
</div>
<p class="per">Un cas légitime bloqué (adresses de test, volume ponctuel) ?
Écrivez-nous : le compteur se lève à la main.</p>
</div>"""
    return HTMLResponse(
        content=document(
            "Plafond d'essais atteint — Address Validator", body, robots="noindex, nofollow"
        ),
        status_code=429,
        headers={
            **PRIVATE_HEADERS,
            # Le client sait quand réessayer sans le deviner.
            "Retry-After": str(secondes),
        },
    )


@router.get("/validate-form")
async def validate_form_page() -> Response:
    """Le formulaire public : indexable et cachable, sans aucune donnée client."""
    return _page()


@router.post("/validate-form")
async def validate_form_submit(request: Request) -> Response:
    """Un essai gratuit : plafonné, sans aucune écriture, pipeline partagé."""
    reglages = get_settings()
    cle = f"validate-form:{client_ip(request)}"
    metrics.incr("validate_form_requests")

    autorise, restants = ratelimit.allow(
        cle, reglages.validate_form_rate_limit_per_day, FENETRE_ESSAI_SECONDES
    )
    if not autorise:
        # Un refus n'est pas compté dans la fenêtre par `ratelimit.allow` : le
        # visiteur bloqué ne repousse donc pas lui-même la fin de sa fenêtre.
        metrics.incr("validate_form_denied")
        return _trop_d_essais(
            reglages.validate_form_rate_limit_per_day,
            ratelimit.retry_after(cle, FENETRE_ESSAI_SECONDES),
        )

    donnees = await request.form()
    adresse = {
        nom: str(donnees.get(nom, "") or "").strip() for nom, _libelle, _ex in CHAMPS
    }
    adresse["country"] = adresse["country"].upper() or PAYS_PAR_DEFAUT
    erreurs = _erreurs(adresse)
    if erreurs:
        return _page(adresse=adresse, erreurs=tuple(erreurs), statut=400)

    # Même pipeline que la route payante (`perform_validation`, D5/D34) : une
    # seule validation, un seul cache, donc aucun écart possible entre ce que
    # voit le visiteur ici et ce que rend l'API vendue. L'essai ne touche ni le
    # registre des paiements (D29) ni la base des comptes : il n'y a rien à
    # débiter, et rien à inscrire (D40).
    resultat, _cle_cache = perform_validation(adresse)
    metrics.incr("validate_form_validations")
    return _page(adresse=adresse, resultat=resultat, restants=restants)


    """Un champ du formulaire, réaffiché tel que le visiteur l'a saisi."""
    return (
        f'<p><label for="{nom}">{html.escape(libelle)}</label><br>\n'
        f'   <input id="{nom}" name="{nom}" type="text" size="34" '
        f'placeholder="{html.escape(exemple, quote=True)}" '
        f'value="{html.escape(valeurs.get(nom, ""), quote=True)}"></p>'
    )
