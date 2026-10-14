"""Mot de passe oublié (D63) : `/compte/mot-de-passe-oublie` et `/compte/nouveau-mot-de-passe`.

Le service ne connaît pas les mots de passe (empreinte bcrypt, D44) : il ne peut
donc pas en « renvoyer » un. Il envoie par mail un **lien** qui permet au client
d'en choisir un nouveau lui-même :

* le lien porte un jeton aléatoire ; la base n'en garde que l'**empreinte**
  (`password_resets`), il vaut `password_reset_minutes` (30) et **une seule
  fois** ; le nouveau mot de passe ferme toutes les sessions du compte et efface
  les autres liens ;
* la page de demande répond **la même chose** que l'adresse ait un compte ou non :
  personne ne s'en sert pour savoir qui est client. Le mail part en arrière-plan,
  après la réponse ;
* le lien est fabriqué avec `public_base_url`, jamais avec l'en-tête `Host` de la
  requête (un `Host` falsifié enverrait le jeton sur un autre site) ;
* `password_reset_per_hour` demandes par IP et par heure, trois par compte : la
  page ne sert ni à inonder une boîte mail ni à tester des adresses en série ;
* le jeton n'est jamais journalisé. `SMTP_HOST` vide : aucun mail ne part, la
  page répond pareil et le journal le dit.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from app import accounts, i18n, ratelimit
from app.config import get_settings
from app.database import SessionLocal
from app.observability import client_ip
from app.routes import connexion, signup
from app.web import PRIVATE_HEADERS

logger = logging.getLogger("address_validator.mot_de_passe")

router = APIRouter()

GABARITS = Path(__file__).resolve().parents[1] / "templates"

#: Fenêtre des plafonds de demande (secondes) et demandes par compte dans la fenêtre.
FENETRE = 3600
PAR_COMPTE = 3

_BLOC = re.compile(r"<!--@si:(\w+)-->(.*?)<!--@fin:\1-->", re.S)
_COCHE = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 8.4 2.9 2.9 6.1-6.6"/></svg>'

TEXTES = {
    "fr": {
        "envoye": ("C'est envoyé.", "Si un compte existe avec cette adresse, le lien vient de partir. Pensez à regarder dans vos courriers indésirables."),
        "email_invalide": "L'adresse email ne semble pas valide.",
        "trop": "Trop de demandes depuis cette adresse : réessayez dans une heure.",
        "differents": "Les deux mots de passe ne sont pas identiques.",
        "sujet": "Votre lien pour choisir un nouveau mot de passe",
        "corps": (
            "Bonjour,\n\nQuelqu'un, sans doute vous, a demandé à changer le mot de passe du compte Address Validator {email}.\n\n"
            "Choisissez un nouveau mot de passe ici :\n{lien}\n\n"
            "Ce lien est valable {minutes} minutes et ne sert qu'une fois.\n\n"
            "Vous n'avez rien demandé ? Ignorez ce message : votre mot de passe ne change pas.\n"
            "Nous ne vous demanderons jamais votre mot de passe, ni par mail, ni par téléphone.\n\n"
            "Address Validator\n{contact}\n"
        ),
    },
    "en": {
        "envoye": ("Sent.", "If an account exists with this address, the link is on its way. Remember to check your spam folder."),
        "email_invalide": "This email address does not look valid.",
        "trop": "Too many requests from this address: try again in an hour.",
        "differents": "The two passwords do not match.",
        "sujet": "Your link to choose a new password",
        "corps": (
            "Hello,\n\nSomeone, probably you, asked to change the password of the Address Validator account {email}.\n\n"
            "Choose a new password here:\n{lien}\n\n"
            "This link is valid for {minutes} minutes and works only once.\n\n"
            "Did not ask for anything? Ignore this message: your password does not change.\n"
            "We will never ask for your password, by email or by phone.\n\n"
            "Address Validator\n{contact}\n"
        ),
    },
    "zh": {
        "envoye": ("已发送。", "如果该地址对应一个账户，链接已经发出。请记得查看垃圾邮件文件夹。"),
        "email_invalide": "该邮箱地址似乎无效。",
        "trop": "来自该地址的请求过多：请一小时后再试。",
        "differents": "两次输入的密码不一致。",
        "sujet": "设置新密码的链接",
        "corps": (
            "您好：\n\n有人（很可能是您本人）申请修改 Address Validator 账户 {email} 的密码。\n\n"
            "请在此设置新密码：\n{lien}\n\n"
            "此链接 {minutes} 分钟内有效，且只能使用一次。\n\n"
            "如果您没有提出申请，请忽略此邮件：您的密码不会改变。\n"
            "我们绝不会通过邮件或电话向您索要密码。\n\n"
            "Address Validator\n{contact}\n"
        ),
    },
    "hi": {
        "envoye": ("भेज दिया गया।", "अगर इस पते से कोई खाता है, तो लिंक भेज दिया गया है। स्पैम फ़ोल्डर भी देख लें।"),
        "email_invalide": "यह ईमेल पता सही नहीं लगता।",
        "trop": "इस पते से बहुत अनुरोध आए हैं: एक घंटे बाद फिर कोशिश करें।",
        "differents": "दोनों पासवर्ड एक जैसे नहीं हैं।",
        "sujet": "नया पासवर्ड चुनने का आपका लिंक",
        "corps": (
            "नमस्ते,\n\nकिसी ने, शायद आपने, Address Validator खाते {email} का पासवर्ड बदलने का अनुरोध किया है।\n\n"
            "यहाँ नया पासवर्ड चुनें:\n{lien}\n\n"
            "यह लिंक {minutes} मिनट तक मान्य है और सिर्फ़ एक बार काम करता है।\n\n"
            "आपने कुछ नहीं माँगा? इस संदेश को अनदेखा करें: आपका पासवर्ड नहीं बदलेगा।\n"
            "हम कभी भी ईमेल या फ़ोन पर आपका पासवर्ड नहीं माँगेंगे।\n\n"
            "Address Validator\n{contact}\n"
        ),
    },
    "es": {
        "envoye": ("Enviado.", "Si existe una cuenta con esta dirección, el enlace ya va en camino. Revisa también la carpeta de spam."),
        "email_invalide": "La dirección de email no parece válida.",
        "trop": "Demasiadas solicitudes desde esta dirección: inténtalo de nuevo dentro de una hora.",
        "differents": "Las dos contraseñas no coinciden.",
        "sujet": "Tu enlace para elegir una nueva contraseña",
        "corps": (
            "Hola:\n\nAlguien, seguramente tú, ha pedido cambiar la contraseña de la cuenta de Address Validator {email}.\n\n"
            "Elige una nueva contraseña aquí:\n{lien}\n\n"
            "Este enlace es válido durante {minutes} minutos y solo funciona una vez.\n\n"
            "¿No has pedido nada? Ignora este mensaje: tu contraseña no cambia.\n"
            "Nunca te pediremos tu contraseña, ni por email ni por teléfono.\n\n"
            "Address Validator\n{contact}\n"
        ),
    },
    "ar": {
        "envoye": ("تم الإرسال.", "إذا كان هناك حساب بهذا العنوان، فقد أُرسل الرابط. تحقّق أيضًا من مجلد الرسائل غير المرغوب فيها."),
        "email_invalide": "يبدو أن عنوان البريد الإلكتروني غير صالح.",
        "trop": "طلبات كثيرة من هذا العنوان: أعد المحاولة بعد ساعة.",
        "differents": "كلمتا المرور غير متطابقتين.",
        "sujet": "رابطك لاختيار كلمة مرور جديدة",
        "corps": (
            "مرحبًا،\n\nطلب أحدهم، وربما أنت، تغيير كلمة مرور حساب Address Validator ‏{email}.\n\n"
            "اختر كلمة مرور جديدة من هنا:\n{lien}\n\n"
            "هذا الرابط صالح لمدة {minutes} دقيقة ولمرة واحدة فقط.\n\n"
            "لم تطلب شيئًا؟ تجاهل هذه الرسالة: لن تتغيّر كلمة مرورك.\n"
            "لن نطلب منك كلمة مرورك أبدًا، لا بالبريد ولا بالهاتف.\n\n"
            "Address Validator\n{contact}\n"
        ),
    },
}


def _session():
    """Session SQLAlchemy. Le point unique que les tests remplacent (SQLite)."""
    return SessionLocal()


def chemin_oubli(lang: str) -> str:
    return i18n.chemin(lang, "compte") + "/mot-de-passe-oublie"


def chemin_nouveau(lang: str) -> str:
    return i18n.chemin(lang, "compte") + "/nouveau-mot-de-passe"


def envoyer_mail(destinataire: str, sujet: str, corps: str) -> None:
    """Envoie un mail texte par le SMTP configuré. Jamais d'exception, jamais le corps au journal."""
    reglages = get_settings()
    if not reglages.smtp_host:
        logger.warning("mot de passe oublie : SMTP_HOST vide, aucun mail envoye")
        return
    message = EmailMessage()
    message["From"] = reglages.smtp_from or reglages.contact_email
    message["To"] = destinataire
    message["Subject"] = sujet
    message.set_content(corps)
    contexte = ssl.create_default_context()
    try:
        if reglages.smtp_port == 465:
            serveur = smtplib.SMTP_SSL(reglages.smtp_host, reglages.smtp_port, context=contexte, timeout=15)
        else:
            serveur = smtplib.SMTP(reglages.smtp_host, reglages.smtp_port, timeout=15)
            serveur.starttls(context=contexte)
        with serveur:
            if reglages.smtp_user:
                serveur.login(reglages.smtp_user, reglages.smtp_password)
            serveur.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("mot de passe oublie : envoi refuse (%s)", type(exc).__name__)


def _rendre(gabarit: str, lang: str, valeurs: dict, blocs=frozenset(), etat: str = "", statut: int = 200, entetes=None) -> HTMLResponse:
    """Le gabarit rempli (valeurs échappées), ses blocs gardés ou retirés, l'état posé."""
    reglages = get_settings()
    valeurs = {
        **i18n.liens(lang, "compte"),
        "url_connexion": connexion.chemin_connexion(lang),
        "url_oubli": chemin_oubli(lang),
        "url_nouveau": chemin_nouveau(lang),
        "lien_minutes": i18n.nombre(lang, reglages.password_reset_minutes),
        "contact": reglages.contact_email,
        "prix_x402": i18n.nombre(lang, reglages.x402_price),
        **valeurs,
    }
    texte = i18n.rendre(GABARITS / gabarit / f"{lang}.html", valeurs)
    texte = _BLOC.sub(lambda bloc: bloc.group(2) if bloc.group(1) in blocs else "", texte)
    texte = texte.replace("<!--@etat-->", etat, 1)
    if "<!--@" in texte:
        raise RuntimeError(f"gabarit {gabarit}/{lang}.html : un bloc n'est pas traité")
    return HTMLResponse(texte, status_code=statut, headers=entetes or dict(PRIVATE_HEADERS))


def _alerte(message: str) -> str:
    return f'<div class="alert" role="alert"><ul><li>{html.escape(message)}</li></ul></div>'


def _envoye(lang: str) -> str:
    titre, suite = TEXTES[lang]["envoye"]
    return f'<div class="okbox" role="status">{_COCHE}<p><b>{html.escape(titre)}</b> {html.escape(suite)}</p></div>'


def _oubli_page(lang: str, *, email: str = "", etat: str = "", statut: int = 200, entetes=None) -> HTMLResponse:
    return _rendre("mot-de-passe-oublie", lang, {"email": email}, etat=etat, statut=statut, entetes=entetes)


async def _demander(request: Request, lang: str, taches: BackgroundTasks) -> Response:
    """Envoie le lien si le compte existe ; répond **pareil** dans tous les cas."""
    textes = TEXTES[lang]
    reglages = get_settings()
    if connexion.origine_etrangere(request):
        return _oubli_page(lang, etat=_alerte(connexion.TEXTES[lang]["origine"]), statut=403)
    email = str((await request.form()).get("email", "") or "")[:320]
    if not accounts.valid_email(email):
        return _oubli_page(lang, email=email, etat=_alerte(textes["email_invalide"]), statut=400)
    autorise, _ = ratelimit.allow(f"reset:ip:{client_ip(request)}", reglages.password_reset_per_hour, FENETRE)
    if not autorise:
        return _oubli_page(
            lang, email=email, etat=_alerte(textes["trop"]), statut=429,
            entetes={**PRIVATE_HEADERS, "Retry-After": str(ratelimit.retry_after(f"reset:ip:{client_ip(request)}", FENETRE))},
        )
    empreinte = hashlib.sha256(accounts.normalize_email(email).encode("utf-8")).hexdigest()[:32]
    par_compte, _ = ratelimit.allow(f"reset:compte:{empreinte}", PAR_COMPTE, FENETRE)
    try:
        with _session() as session:
            client = accounts.find_by_email(session, email)
            if client is not None and par_compte:
                jeton = accounts.create_password_reset(session, client, accounts.now_utc(), reglages.password_reset_minutes)
                # `public_base_url`, jamais l'en-tête Host : le lien ne peut pas partir vers un autre site.
                lien = f"{reglages.public_base_url.rstrip('/')}{chemin_nouveau(lang)}?jeton={jeton}"
                corps = textes["corps"].format(
                    email=client.email, lien=lien, minutes=reglages.password_reset_minutes, contact=reglages.contact_email
                )
                taches.add_task(envoyer_mail, client.email, textes["sujet"], corps)
    except SQLAlchemyError:
        # Même réponse : une panne ne doit pas dire si le compte existe.
        logger.warning("mot de passe oublie : base injoignable")
    return _oubli_page(lang, etat=_envoye(lang))


def _nouveau_page(lang: str, *, jeton: str = "", email: str = "", erreur: str = "", statut: int = 200) -> HTMLResponse:
    valide = bool(email)
    return _rendre(
        "nouveau-mot-de-passe", lang,
        {"jeton": jeton, "email": email, "mdp_min": accounts.PASSWORD_MIN_LENGTH, "mdp_max": accounts.PASSWORD_MAX_BYTES},
        blocs={"valide"} if valide else {"perime"},
        etat=_alerte(erreur) if erreur else "",
        statut=statut if valide else 400,
    )


def _formulaire_nouveau(request: Request, lang: str) -> Response:
    jeton = request.query_params.get("jeton", "")
    try:
        with _session() as session:
            trouve = accounts.password_reset_customer(session, jeton, accounts.now_utc())
            email = trouve[1].email if trouve else ""
    except SQLAlchemyError:
        email = ""
    return _nouveau_page(lang, jeton=jeton if email else "", email=email)


async def _enregistrer(request: Request, lang: str) -> Response:
    """Nouveau mot de passe : lien consommé, sessions fermées, nouvelle session ouverte."""
    if connexion.origine_etrangere(request):
        return _nouveau_page(lang)
    donnees = await request.form()
    jeton = str(donnees.get("jeton", "") or "")
    mot_de_passe = str(donnees.get("password", "") or "")
    confirmation = str(donnees.get("password2", "") or "")
    maintenant = accounts.now_utc()
    try:
        with _session() as session:
            trouve = accounts.password_reset_customer(session, jeton, maintenant)
            if trouve is None:
                return _nouveau_page(lang)
            ligne, client = trouve
            erreur = ""
            if accounts.password_problem(mot_de_passe):
                regles = signup.TEXTES[lang]
                erreur = (regles["mdp_court"].format(min=accounts.PASSWORD_MIN_LENGTH)
                          if len(mot_de_passe) < accounts.PASSWORD_MIN_LENGTH
                          else regles["mdp_long"].format(max=accounts.PASSWORD_MAX_BYTES))
            elif mot_de_passe != confirmation:
                erreur = TEXTES[lang]["differents"]
            if erreur:
                return _nouveau_page(lang, jeton=jeton, email=client.email, erreur=erreur, statut=400)
            accounts.finish_password_reset(session, ligne, client, mot_de_passe, maintenant)
            valeur = accounts.open_session(session, client, maintenant, get_settings().session_hours)
    except SQLAlchemyError:
        return _nouveau_page(lang)
    reponse = RedirectResponse(i18n.chemin(lang, "compte"), status_code=303, headers=dict(PRIVATE_HEADERS))
    connexion.poser_cookie(reponse, valeur)
    return reponse


def _routes(lang: str):
    async def oubli(request: Request) -> Response:
        return _oubli_page(lang)

    async def demander(request: Request, taches: BackgroundTasks) -> Response:
        return await _demander(request, lang, taches)

    async def nouveau(request: Request) -> Response:
        return _formulaire_nouveau(request, lang)

    async def enregistrer(request: Request) -> Response:
        return await _enregistrer(request, lang)

    return (
        (chemin_oubli(lang), oubli, "GET"),
        (chemin_oubli(lang), demander, "POST"),
        (chemin_nouveau(lang), nouveau, "GET"),
        (chemin_nouveau(lang), enregistrer, "POST"),
    )


# Chemins **littéraux** dans chaque langue, jamais `/{lang}/…` (D57) ; hors du schéma OpenAPI.
for _lang in i18n.LANGUES:
    for _chemin, _route, _methode in _routes(_lang):
        router.add_api_route(_chemin, _route, methods=[_methode], include_in_schema=False)
