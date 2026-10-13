"""`/signup` et `/compte` — inscription et espace client (décision D44).

Trois exigences, dans cet ordre de priorité :

1. **La clé API est montrée une fois.** Comme chez Stripe : elle est générée à
   l'inscription, affichée sur une page `Cache-Control: no-store`, puis il n'en
   reste que l'empreinte en base. La régénérer est possible depuis l'espace
   client (mot de passe exigé) ; la relire, non — c'est le prix de ne pas la
   stocker en clair.
2. **Fermé par défaut côté lecture.** `/compte` exige le couple email/mot de
   passe (HTTP Basic, même garde que `/dashboard`, D32). Base injoignable =
   `503` explicite : jamais des zéros décoratifs à la place de l'absence de
   données (même règle que `/prospection`).
3. **Honnête et autonome.** Même feuille de style que le reste (`app/web.py`),
   aucun script, aucun appel sortant. Les statistiques viennent de
   `customer_validations` ; l'estimation d'économies est affichée **avec sa
   formule**, et la facturation dit qu'elle est vide au lieu d'inventer un
   montant.

Le plafond d'inscription (`5 par IP et par heure`) vient de la configuration et
s'appuie sur `app/ratelimit.py` : Redis partagé, repli local compté.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import accounts, ratelimit
from app.config import get_settings
from app.database import SessionLocal
from app.observability import client_ip
from app.routes.dashboard import UNAUTHORIZED_HEADERS, _decode_basic
from app.web import PRIVATE_HEADERS, document, public_headers, site_head

router = APIRouter()

PLATEFORME_LIBELLES = {
    "shopify": "Shopify",
    "woocommerce": "WooCommerce",
    "autre": "Autre",
}

#: Pages qui viennent de contenir, ou contiennent, une clé API : jamais de cache,
#: et un formulaire qui ne peut poster qu'ici (`form-action 'self'`). Le dessin
#: vit dans `app/web.py` (`PRIVATE_HEADERS`) : la même définition sert à
#: `/signup` et à `/validate-form`, qui ne peuvent donc pas diverger.
SECRET_HEADERS = PRIVATE_HEADERS


def _session():
    """Session SQLAlchemy. Le point unique que les tests remplacent (SQLite)."""
    return SessionLocal()


def _indisponible(exc: Exception) -> Response:
    """Base injoignable : `503` qui dit quoi faire, jamais une page vide."""
    return HTMLResponse(
        content=document(
            "Compte indisponible",
            f'{site_head()}<div class="site"><h1>Base de données indisponible</h1>'
            '<p class="lead">Les comptes clients sont stockés dans PostgreSQL. '
            "Le service est démarré, mais sa base ne répond pas : aucun compte ne "
            "peut être créé ni consulté pour l'instant.</p>"
            '<div class="note"><strong>Détail technique :</strong> '
            f"{html.escape(str(exc)[:300])}</div>"
            '<p class="per">Réessayez dans quelques minutes. Aucune donnée n\'a '
            "été perdue : rien n'a été écrit.</p></div>",
            robots="noindex, nofollow",
        ),
        status_code=503,
        headers=SECRET_HEADERS,
    )


def _formulaire(
    *,
    email: str = "",
    boutique: str = "",
    plateforme: str = "",
    plan: str = accounts.PLAN_PAR_DEFAUT,
    erreurs: tuple[str, ...] = (),
    statut: int = 200,
) -> HTMLResponse:
    """Le formulaire d'inscription. Les champs saisis sont **réaffichés**, sauf
    le mot de passe (jamais conservé, jamais renvoyé)."""
    bloc_erreurs = ""
    if erreurs:
        items = "".join(f"<li>{html.escape(e)}</li>" for e in erreurs)
        bloc_erreurs = f'<div class="note"><ul>{items}</ul></div>'

    options = "".join(
        f'<option value="{html.escape(cle, quote=True)}"'
        f'{" selected" if cle == plateforme else ""}>'
        f"{html.escape(libelle)}</option>"
        for cle, libelle in PLATEFORME_LIBELLES.items()
    )

    body = f"""{site_head()}<div class="site">
<h1>Créez votre compte</h1>
<p class="lead">14 jours et 100 validations offertes, sans carte bancaire.
Votre clé API est affichée une seule fois, à la fin de cette étape.</p>
{bloc_erreurs}
<form method="post" action="/signup" class="col">
  <input type="hidden" name="plan" value="{html.escape(plan, quote=True)}">
  <p><label for="email">Email professionnel</label><br>
     <input id="email" name="email" type="email" required size="34"
            value="{html.escape(email, quote=True)}"></p>
  <p><label for="password">Mot de passe (12 caractères minimum)</label><br>
     <input id="password" name="password" type="password" required
            minlength="{accounts.PASSWORD_MIN_LENGTH}" size="34"
            autocomplete="new-password"></p>
  <p><label for="shop_name">Nom de la boutique</label><br>
     <input id="shop_name" name="shop_name" type="text" required size="34"
            value="{html.escape(boutique, quote=True)}"></p>
  <p><label for="platform">Plateforme</label><br>
     <select id="platform" name="platform" required>{options}</select></p>
  <p><button class="btn primary" type="submit">Créer mon compte</button></p>
</form>
<div class="note"><strong>Ce que nous ne faisons pas :</strong> votre mot de
passe n'est jamais stocké en clair (hachage bcrypt, sel unique par compte), et
votre clé API n'est conservée que sous forme d'empreinte. Elle ne pourra plus
être relue après cette page ; elle se régénère quand vous voulez depuis votre
espace.</div>
<p class="per">Déjà un compte ? <a href="/compte">Connectez-vous</a> avec votre
email et votre mot de passe.</p>
</div>"""

    entetes = public_headers(form=True) if statut == 200 else SECRET_HEADERS
    return HTMLResponse(
        content=document(
            "Créer un compte — Address Validator",
            body,
            description=(
                "Créez votre compte Address Validator : 14 jours d'essai, "
                "100 validations, sans carte bancaire."
            ),
            robots="index, follow" if statut == 200 else "noindex, nofollow",
        ),
        status_code=statut,
        headers=entetes,
    )


def _page_cle(
    *,
    shop_name: str,
    cle: str,
    prefixe: str,
    plan: str,
    jours_essai: int,
    quota: int | None,
    regeneration: bool,
) -> HTMLResponse:
    """La **seule** page qui montre la clé en clair : `no-store`, non indexée."""
    titre = "Votre nouvelle clé API" if regeneration else "Votre compte est créé"
    quota_texte = "illimité" if quota is None else f"{quota:,}".replace(",", " ")
    if regeneration:
        intro = "L'ancienne clé cesse de fonctionner immédiatement."
    else:
        intro = (
            "Voici votre clé API. C'est la seule fois que nous pouvons vous la "
            "montrer : nous n'en conservons qu'une empreinte."
        )
    body = f"""{site_head()}<div class="site">
<h1>{html.escape(titre)}</h1>
<p class="lead">{html.escape(intro)}</p>
<div class="col">
  <div class="tag">Clé API — {html.escape(shop_name)}</div>
  <p class="mono" style="font-size:15px;word-break:break-all">{html.escape(cle)}</p>
  <p class="per">Copiez-la maintenant dans votre gestionnaire de secrets, ou
  dans la configuration de votre boutique — jamais dans un dépôt git.</p>
</div>
<div class="note"><strong>Comment l'utiliser :</strong> envoyez-la dans l'en-tête
<code>X-API-Key</code> de vos appels à <code>POST /validate</code>. Le
<a href="/guide">guide d'intégration</a> (Shopify, WooCommerce, import CSV, appel
direct) détaille chaque cas.</div>
<h2 class="sec">Votre offre</h2>
<div class="cols">
  <div class="col"><div class="tag">Plan</div>
    <div class="price">{html.escape(accounts.PLAN_LIBELLES.get(plan, plan))}</div>
    <p class="per">essai de {jours_essai} jours</p></div>
  <div class="col"><div class="tag">Validations incluses</div>
    <div class="price">{quota_texte}</div>
    <p class="per">puis 29 €/mois (Starter) ou 79 €/mois (Pro)</p></div>
  <div class="col"><div class="tag">Identifiant de la clé</div>
    <div class="price mono" style="font-size:18px">{html.escape(prefixe)}…</div>
    <p class="per">pour la reconnaître dans votre espace</p></div>
</div>
<div class="actions">
  <a class="btn primary" href="/compte">Aller à mon espace client</a>
  <a class="btn ghost" href="/pricing">Voir les tarifs</a>
</div>
<p class="per">Votre espace se connecte avec votre email et votre mot de passe.
Cette page n'est pas mise en cache.</p>
</div>"""
    return HTMLResponse(
        content=document(titre, body, robots="noindex, nofollow"),
        status_code=200,
        headers=SECRET_HEADERS,
    )


def _compte_page(
    *,
    shop_name: str,
    email: str,
    prefixe: str,
    statut: dict,
    nouvelle_cle: str | None = None,
) -> HTMLResponse:
    """L'espace client : consommation réelle, clé, offre, facturation.

    Chaque chiffre a une source : `customer_validations` pour la consommation,
    `COUT_ECHEC_USD` (borne basse) pour l'estimation — dont la formule est
    affichée. Un total d'économies sans formule serait un argument, pas un calcul.
    """
    quota = statut["quota"]
    quota_texte = "illimité" if quota is None else f"{quota:,}".replace(",", " ")
    reste_texte = (
        "sans plafond"
        if statut["restant"] is None
        else f"{statut['restant']:,}".replace(",", " ")
    )

    if statut["trial_ends_at"] is None:
        essai = "Aucune échéance d'essai (plan payant)."
    elif statut["trial_expire"]:
        essai = (
            "Votre essai est terminé"
            f" (fin le {statut['trial_ends_at']:%d/%m/%Y})."
        )
    else:
        essai = (
            f"Essai en cours : {statut['trial_jours_restants']} jour(s) restant(s),"
            f" jusqu'au {statut['trial_ends_at']:%d/%m/%Y}."
        )

    avertissements = ""
    if statut["expire"]:
        avertissements += (
            '<div class="note"><strong>Essai terminé :</strong> vos validations '
            "par clé API sont suspendues jusqu'au passage à un plan payant "
            "(29 €/mois pour 2 000 validations, 79 €/mois pour 10 000).</div>"
        )
    elif statut["quota_atteint"]:
        avertissements += (
            '<div class="note"><strong>Quota atteint :</strong> vos '
            f"{quota_texte} validations du mois sont consommées. Le service par clé "
            "API est suspendu jusqu'au premier jour du mois suivant (UTC) ; "
            "écrivez-nous pour passer au plan supérieur.</div>"
        )

    bloc_cle = ""
    if nouvelle_cle:
        bloc_cle = f"""<div class="col">
  <div class="tag">Nouvelle clé API — à copier maintenant</div>
  <p class="mono" style="font-size:15px;word-break:break-all">{html.escape(nouvelle_cle)}</p>
  <p class="per">L'ancienne clé ne fonctionne plus. Cette page n'est pas mise en
  cache et la clé ne sera plus affichée.</p>
</div>"""

    body = f"""{site_head()}<div class="site">
<h1>Espace client — {html.escape(shop_name)}</h1>
<p class="per">{html.escape(email)} · plan
{html.escape(statut["plan_libelle"])} · {html.escape(essai)}</p>
{avertissements}
{bloc_cle}
<h2 class="sec">Utilisation</h2>
<div class="cols">
  <div class="col"><div class="tag">Validations servies</div>
    <div class="price">{statut['validations']:,}</div>
    <p class="per">depuis la création du compte · ce mois-ci :
    {statut['validations_mois']:,} sur {quota_texte} incluses — reste {reste_texte}</p></div>
  <div class="col"><div class="tag">Adresses invalides détectées</div>
    <div class="price">{statut['invalides']:,}</div>
    <p class="per">refus explicitées avant expédition</p></div>
  <div class="col"><div class="tag">Économies estimées</div>
    <div class="price">{statut['economies_usd']:.2f} $</div>
    <p class="per">estimation : {statut['invalides']} adresses invalides ×
    {statut['cout_par_echec_usd']:.0f} $ (borne basse du coût d'un colis en échec)</p></div>
</div>
<p class="per">Ces compteurs viennent des validations servies avec votre clé API.
Aucune adresse n'est conservée : nous enregistrons une empreinte et l'issue
(valide ou non), rien de plus.</p>

<h2 class="sec">Clé API</h2>
<div class="col">
  <div class="tag">Clé active</div>
  <p class="mono" style="font-size:15px">{html.escape(prefixe)}…</p>
  <p class="per">Pour des raisons de sécurité, la clé complète n'est affichée
  qu'une fois, à sa création. Elle est envoyée dans l'en-tête
  <code>X-API-Key</code> de vos appels.</p>
  <form method="post" action="/compte/cle">
    <button class="btn" type="submit">Régénérer ma clé API</button>
  </form>
  <p class="per">Régénérer invalide immédiatement l'ancienne clé : à faire si elle
  a circulé en clair.</p>
</div>

<h2 class="sec">Facturation</h2>
<div class="col">
  <p class="per">Aucune facturation pour l'instant : votre essai est offert et
  aucun moyen de paiement n'est enregistré.</p>
  <p class="per">Le passage à un plan payant se fait sur demande ; aucun
  prélèvement automatique n'a lieu tant qu'il n'a pas été confirmé.</p>
</div>

<div class="actions">
  <a class="btn ghost" href="/pricing">Voir les tarifs</a>
  <a class="btn ghost" href="/validate-form">Tester une adresse</a>
</div>
<p class="per">Support et passage de plan : voir l'adresse de contact en pied de
page sur <a href="/pricing">/pricing</a>.</p>
</div>"""
    return HTMLResponse(
        content=document(
            f"Espace client — {shop_name}", body, robots="noindex, nofollow"
        ),
        status_code=200,
        headers=SECRET_HEADERS,
    )


def _refus(message: str = "Authentification requise.") -> Response:
    """Refus d'authentification, avec le défi Basic (aucun secret dans le corps)."""
    return Response(
        status_code=401,
        content=message,
        media_type="text/plain; charset=utf-8",
        headers=UNAUTHORIZED_HEADERS,
    )


def _trop_de_tentatives(limite: int, secondes: int) -> HTMLResponse:
    """`429` : le plafond d'inscription par IP est atteint, et il le dit."""
    body = f"""{site_head()}<div class="site">
<h1>Trop de tentatives d'inscription</h1>
<p class="lead">Le plafond de {limite} inscriptions par adresse IP et par heure
est atteint. C'est un garde-fou contre la création de comptes en série ; il se
réinitialise tout seul.</p>
<div class="note">Réessayez dans moins d'une heure. Si un vrai client est bloqué,
écrivez-nous : le compteur se lève à la main.</div>
<p class="per"><a href="/pricing">Retour aux tarifs</a></p>
</div>"""
    return HTMLResponse(
        content=document("Trop de tentatives — Address Validator", body),
        status_code=429,
        headers={
            **SECRET_HEADERS,
            # Le client sait quand réessayer sans deviner.
            "Retry-After": str(secondes),
        },
    )


@router.get("/signup")
async def signup_form(request: Request) -> Response:
    """Le formulaire d'inscription (public, indexable, sans état)."""
    plan = request.query_params.get("plan", accounts.PLAN_PAR_DEFAUT)
    if plan not in accounts.PLANS:
        plan = accounts.PLAN_PAR_DEFAUT
    return _formulaire(plan=plan)


@router.post("/signup")
async def signup_submit(request: Request) -> Response:
    """Crée le compte, puis **montre la clé une fois** (`no-store`)."""
    settings = get_settings()
    autorise, _restants = ratelimit.allow(
        f"signup:{client_ip(request)}", settings.signup_rate_limit_per_hour, 3600
    )
    if not autorise:
        return _trop_de_tentatives(settings.signup_rate_limit_per_hour, 3600)

    donnees = await request.form()
    email = str(donnees.get("email", "") or "")
    mot_de_passe = str(donnees.get("password", "") or "")
    boutique = str(donnees.get("shop_name", "") or "")
    plateforme = str(donnees.get("platform", "") or "")
    plan = str(donnees.get("plan", "") or accounts.PLAN_PAR_DEFAUT)
    if plan not in accounts.PLANS:
        plan = accounts.PLAN_PAR_DEFAUT

    erreurs: list[str] = []
    if not accounts.valid_email(email):
        erreurs.append("L'adresse email ne semble pas valide.")
    probleme = accounts.password_problem(mot_de_passe)
    if probleme:
        erreurs.append(probleme)
    if not boutique.strip():
        erreurs.append("Le nom de la boutique est requis.")
    if plateforme not in accounts.PLATEFORMES:
        erreurs.append("Choisissez une plateforme : Shopify, WooCommerce ou Autre.")
    if erreurs:
        return _formulaire(
            email=email,
            boutique=boutique,
            plateforme=plateforme,
            plan=plan,
            erreurs=tuple(erreurs),
            statut=400,
        )

    try:
        with _session() as session:
            client, cle = accounts.create_customer(
                session,
                email=email,
                password=mot_de_passe,
                shop_name=boutique,
                platform=plateforme,
                plan=plan,
                jours_essai=settings.trial_days,
            )
            infos = {
                "shop_name": client.shop_name,
                "prefixe": client.api_key_prefix,
                "plan": client.plan,
                "quota": accounts.quota_for(client.plan),
            }
    except IntegrityError:
        # L'email est deja pris : on le dit, parce qu'un client qui a deja un
        # compte doit pouvoir le comprendre sans appeler le support.
        return _formulaire(
            email=email,
            boutique=boutique,
            plateforme=plateforme,
            plan=plan,
            erreurs=(
                "Un compte existe déjà avec cet email. Connectez-vous depuis "
                "/compte, ou utilisez une autre adresse.",
            ),
            statut=409,
        )
    except SQLAlchemyError as exc:
        return _indisponible(exc)

    return _page_cle(
        cle=cle, jours_essai=settings.trial_days, regeneration=False, **infos
    )


def _espace_client(request: Request, *, regeneration: bool) -> Response:
    """Espace client : lecture, ou régénération de clé (même authentification)."""
    identifiants = _decode_basic(request.headers.get("authorization", ""))
    if identifiants is None:
        return _refus(
            "Authentification requise : email du compte et mot de passe (HTTP "
            "Basic). La clé API ne donne pas accès à l'espace client."
        )
    email, mot_de_passe = identifiants

    try:
        with _session() as session:
            client = accounts.authenticate(session, email, mot_de_passe)
            if client is None:
                return _refus("Email ou mot de passe incorrect.")
            cle = accounts.regenerate_api_key(session, client) if regeneration else None
            infos = {
                "shop_name": client.shop_name,
                "email": client.email,
                "prefixe": client.api_key_prefix,
            }
            statut = accounts.plan_status(session, client)
    except SQLAlchemyError as exc:
        return _indisponible(exc)

    return _compte_page(nouvelle_cle=cle, statut=statut, **infos)


@router.get("/compte")
async def compte(request: Request) -> Response:
    return _espace_client(request, regeneration=False)


@router.post("/compte/cle")
async def compte_nouvelle_cle(request: Request) -> Response:
    """Régénère la clé : la nouvelle est affichée une fois, l'ancienne meurt."""
    return _espace_client(request, regeneration=True)

