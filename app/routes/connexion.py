"""`/compte/connexion` et `/compte/deconnexion` — se connecter à l'espace client (D62).

Avant D62, `/compte` répondait `401` avec le défi HTTP Basic : le navigateur
ouvrait sa fenêtre grise, sans logo ni explication, puis une ligne de texte brut
si l'on annulait. Désormais un navigateur reçoit une **page de connexion** au
dessin du site, dans sa langue, et l'espace client tient par une **session** :

* **le cookie ne contient qu'un jeton aléatoire** ; la base n'en garde que
  l'empreinte SHA-256 (`customer_sessions`), donc une copie de la base n'ouvre
  aucune session. Il porte `__Host-` (Secure, Path=/, aucun Domain), `HttpOnly`
  (aucun script ne le lit) et `SameSite=Lax` (un formulaire posté depuis un autre
  site ne l'emporte pas) ;
* **la session dure `session_hours` au plus** (12 h) ; se déconnecter efface sa
  ligne, et le cookie ne vaut plus rien, même recopié ;
* **`login_max_failures` mots de passe faux bloquent `login_lock_minutes`** (5 et
  15), par adresse IP **et** par compte : seuls les échecs comptent
  (`ratelimit.blocked` regarde sans compter), et le message est le même pour un
  email inconnu et un mauvais mot de passe ;
* **un formulaire venu d'un autre site est refusé** (`Origin` différent de
  l'hôte servi) : connexion, déconnexion et régénération de clé.

HTTP Basic reste accepté sur `/compte` et `/compte/cle` pour les scripts qui
l'envoient d'eux-mêmes (même blocage des échecs) ; il n'est plus **demandé** à
un navigateur. Les trois valeurs ci-dessus sont écrites sur la page de
connexion, injectées depuis la configuration (D48).
"""

from __future__ import annotations

import hashlib
import html
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from app import accounts, i18n, ratelimit
from app.cache import CACHE_TTL
from app.config import get_settings
from app.database import SessionLocal
from app.observability import client_ip
from app.web import PRIVATE_HEADERS

router = APIRouter()

GABARITS = Path(__file__).resolve().parents[1] / "templates" / "connexion"

#: Nom du cookie. Le préfixe `__Host-` oblige le navigateur à exiger Secure,
#: Path=/ et l'absence de Domain : aucun sous-domaine ne peut le poser ni le lire.
COOKIE = "__Host-av_session"

TEXTES = {
    "fr": {
        "identifiants": "Email ou mot de passe incorrect.",
        "bloque": "Trop de mots de passe faux : la connexion est en pause. Réessayez dans {attente} min.",
        "origine": "Ce formulaire ne vient pas de ce site : par sécurité, il est refusé. Reconnectez-vous depuis cette page.",
        "indisponible": "Le service ne peut pas vérifier votre compte pour l'instant. Réessayez dans quelques minutes.",
        "mdp_cle": "Mot de passe incorrect : la clé n'a pas été changée.",
    },
    "en": {
        "identifiants": "Incorrect email or password.",
        "bloque": "Too many wrong passwords: sign-in is paused. Try again in {attente} min.",
        "origine": "This form does not come from this site, so it was refused for your safety. Sign in again from this page.",
        "indisponible": "The service cannot check your account right now. Try again in a few minutes.",
        "mdp_cle": "Incorrect password: your key was not changed.",
    },
    "zh": {
        "identifiants": "邮箱或密码不正确。",
        "bloque": "密码错误次数过多，登录已暂停。请在 {attente} 分钟后重试。",
        "origine": "此表单并非来自本网站，为了您的安全已被拒绝。请在本页重新登录。",
        "indisponible": "服务暂时无法验证您的账户。请几分钟后重试。",
        "mdp_cle": "密码不正确：密钥未更改。",
    },
    "hi": {
        "identifiants": "ईमेल या पासवर्ड गलत है।",
        "bloque": "बहुत बार गलत पासवर्ड डाला गया: साइन-इन रोका गया है। {attente} मिनट बाद फिर कोशिश करें।",
        "origine": "यह फ़ॉर्म इस साइट से नहीं आया, इसलिए आपकी सुरक्षा के लिए इसे अस्वीकार किया गया। इसी पेज से फिर साइन इन करें।",
        "indisponible": "सेवा अभी आपका खाता नहीं जाँच पा रही। कुछ मिनट बाद फिर कोशिश करें।",
        "mdp_cle": "पासवर्ड गलत है: आपकी कुंजी नहीं बदली गई।",
    },
    "es": {
        "identifiants": "Email o contraseña incorrectos.",
        "bloque": "Demasiadas contraseñas incorrectas: el acceso está en pausa. Inténtalo de nuevo en {attente} min.",
        "origine": "Este formulario no viene de este sitio: por seguridad, se ha rechazado. Vuelve a entrar desde esta página.",
        "indisponible": "El servicio no puede comprobar tu cuenta ahora mismo. Inténtalo de nuevo en unos minutos.",
        "mdp_cle": "Contraseña incorrecta: la clave no se ha cambiado.",
    },
    "ar": {
        "identifiants": "البريد الإلكتروني أو كلمة المرور غير صحيحة.",
        "bloque": "كلمات مرور خاطئة كثيرة: تسجيل الدخول متوقف مؤقتًا. أعد المحاولة بعد {attente} دقيقة.",
        "origine": "هذا النموذج لا يأتي من هذا الموقع، لذلك رُفض حفاظًا على أمانك. سجّل الدخول من جديد من هذه الصفحة.",
        "indisponible": "لا تستطيع الخدمة التحقق من حسابك الآن. أعد المحاولة بعد بضع دقائق.",
        "mdp_cle": "كلمة المرور غير صحيحة: لم يُغيَّر المفتاح.",
    },
}


def _session():
    """Session SQLAlchemy. Le point unique que les tests remplacent (SQLite)."""
    return SessionLocal()


def chemin_connexion(lang: str) -> str:
    return i18n.chemin(lang, "compte") + "/connexion"


def jeton(request: Request) -> str:
    return request.cookies.get(COOKIE, "")


def origine_etrangere(request: Request) -> bool:
    """`True` si le formulaire a été posté depuis un autre site (CSRF).

    Un navigateur envoie `Origin` avec chaque `POST` ; il doit nommer l'hôte que
    ce serveur sert. Sans `Origin` (script, vieux client), rien n'est refusé ici :
    le cookie `SameSite=Lax` ne part déjà pas d'un formulaire d'un autre site.
    """
    origine = request.headers.get("origin")
    if origine is None:
        return False
    return urlsplit(origine).netloc.lower() != request.headers.get("host", "").lower()


def cles_echecs(request: Request, email: str) -> tuple[str, str]:
    """Les deux compteurs d'échecs : l'adresse IP, et le compte visé (empreinte)."""
    compte = hashlib.sha256(accounts.normalize_email(email).encode("utf-8")).hexdigest()[:32]
    return f"login:ip:{client_ip(request)}", f"login:compte:{compte}"


def attente_blocage(cles: tuple[str, ...]) -> int | None:
    """Secondes avant la fin du blocage si l'un des compteurs est plein, sinon `None`."""
    reglages = get_settings()
    fenetre = reglages.login_lock_minutes * 60
    pleines = [cle for cle in cles if ratelimit.blocked(cle, reglages.login_max_failures, fenetre)]
    if not pleines:
        return None
    return max(ratelimit.retry_after(cle, fenetre) for cle in pleines)


def compter_echec(cles: tuple[str, ...]) -> None:
    reglages = get_settings()
    for cle in cles:
        ratelimit.allow(cle, reglages.login_max_failures, reglages.login_lock_minutes * 60)


def poser_cookie(reponse: Response, valeur: str) -> None:
    reponse.set_cookie(
        COOKIE, valeur, max_age=get_settings().session_hours * 3600,
        path="/", secure=True, httponly=True, samesite="lax",
    )


def page(lang: str, *, email: str = "", erreur: str = "", statut: int = 200, entetes: dict | None = None) -> HTMLResponse:
    """La page de connexion, dans la langue de son adresse, privée (`no-store`, `noindex`)."""
    reglages = get_settings()
    quota = accounts.PLANS[accounts.PLAN_PAR_DEFAUT]
    jours = reglages.trial_days
    devise = i18n.DEVISE[lang]
    cout = i18n.MONTANTS[devise]["colis_rate"]
    nombre = lambda valeur: i18n.nombre(lang, valeur)  # noqa: E731
    montant = lambda valeur: (f"{nombre(valeur)} €" if devise == "EUR" else i18n.SYMBOLE[devise] + nombre(valeur))  # noqa: E731
    # Aperçu : des valeurs d'exemple, dites comme telles, proportionnelles à l'essai réel.
    ex_restantes, ex_jours = round(quota * 0.63), max(1, round(jours * 0.64))
    valeurs = {
        **i18n.liens(lang, "compte"),
        "url_connexion": chemin_connexion(lang),
        "email": email,
        "contact": reglages.contact_email,
        "prix_x402": nombre(reglages.x402_price),
        "jours_essai": nombre(jours),
        "validations_essai": nombre(quota),
        "session_heures": nombre(reglages.session_hours),
        "echecs_max": nombre(reglages.login_max_failures),
        "blocage_minutes": nombre(reglages.login_lock_minutes),
        "cache_jours": nombre(CACHE_TTL // 86400),
        "ex_restantes": nombre(ex_restantes),
        "ex_pct_quota": ex_restantes * 100 // quota,
        "ex_jours": nombre(ex_jours),
        "ex_pct_jours": ex_jours * 100 // jours,
        "ex_invalides": nombre(3),
        "ex_cout": montant(cout),
        "ex_economies": montant(3 * cout),
        "ex_cle": accounts.API_KEY_PREFIX + "a1b2…",
    }
    alerte = f'<div class="alert" role="alert"><ul><li>{html.escape(erreur)}</li></ul></div>' if erreur else ""
    texte = i18n.rendre(GABARITS / f"{lang}.html", valeurs).replace("<!--@alerte-->", alerte, 1)
    return HTMLResponse(texte, status_code=statut, headers=entetes or dict(PRIVATE_HEADERS))


def _formulaire(request: Request, lang: str) -> Response:
    """Déjà connecté : direction l'espace. Sinon, la page de connexion."""
    valeur = jeton(request)
    if valeur:
        try:
            with _session() as session:
                if accounts.session_customer(session, valeur, accounts.now_utc()) is not None:
                    return RedirectResponse(i18n.chemin(lang, "compte"), status_code=303, headers=dict(PRIVATE_HEADERS))
        except SQLAlchemyError:
            pass
    return page(lang)


async def _connecter(request: Request, lang: str) -> Response:
    """Vérifie le couple email/mot de passe, puis ouvre une session (cookie)."""
    textes = TEXTES[lang]
    if origine_etrangere(request):
        return page(lang, erreur=textes["origine"], statut=403)
    donnees = await request.form()
    email = str(donnees.get("email", "") or "")[:320]
    mot_de_passe = str(donnees.get("password", "") or "")
    cles = cles_echecs(request, email)
    attente = attente_blocage(cles)
    if attente is not None:
        # Bloqué : le mot de passe n'est même pas vérifié (rien à deviner, rien à calculer).
        return page(
            lang, email=email, erreur=textes["bloque"].format(attente=max(1, (attente + 59) // 60)), statut=429,
            entetes={**PRIVATE_HEADERS, "Retry-After": str(attente)},
        )
    try:
        with _session() as session:
            client = accounts.authenticate(session, email, mot_de_passe)
            if client is None:
                compter_echec(cles)
                return page(lang, email=email, erreur=textes["identifiants"], statut=400)
            valeur = accounts.open_session(session, client, accounts.now_utc(), get_settings().session_hours)
    except SQLAlchemyError:
        return page(lang, email=email, erreur=textes["indisponible"], statut=503)
    reponse = RedirectResponse(i18n.chemin(lang, "compte"), status_code=303, headers=dict(PRIVATE_HEADERS))
    poser_cookie(reponse, valeur)
    return reponse


def _deconnecter(request: Request, lang: str) -> Response:
    """Ferme la session : sa ligne est effacée et le cookie retiré."""
    if origine_etrangere(request):
        return page(lang, erreur=TEXTES[lang]["origine"], statut=403)
    try:
        with _session() as session:
            accounts.close_session(session, jeton(request))
    except SQLAlchemyError:
        pass
    reponse = RedirectResponse(chemin_connexion(lang), status_code=303, headers=dict(PRIVATE_HEADERS))
    reponse.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return reponse


def _routes(lang: str):
    async def formulaire(request: Request) -> Response:
        return _formulaire(request, lang)

    async def connecter(request: Request) -> Response:
        return await _connecter(request, lang)

    async def deconnecter(request: Request) -> Response:
        return _deconnecter(request, lang)

    compte = i18n.chemin(lang, "compte")
    return (
        (compte + "/connexion", formulaire, "GET"),
        (compte + "/connexion", connecter, "POST"),
        (compte + "/deconnexion", deconnecter, "POST"),
    )


# Chemins **littéraux** dans chaque langue (`/compte/connexion`, `/en/compte/connexion`…),
# jamais `/{lang}/…` (D57). Hors du schéma OpenAPI : ce sont des pages, pas l'API.
for _lang in i18n.LANGUES:
    for _chemin, _route, _methode in _routes(_lang):
        router.add_api_route(_chemin, _route, methods=[_methode], include_in_schema=False)
