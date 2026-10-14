"""`/signup` et `/compte` — inscription et espace client (décision D44), en six langues (D57).

Trois exigences, dans cet ordre de priorité :

1. **La clé API est montrée une fois.** Comme chez Stripe : elle est générée à
   l'inscription, affichée sur une page `Cache-Control: no-store`, puis il n'en
   reste que l'empreinte en base. La régénérer est possible depuis l'espace
   client (mot de passe exigé) ; la relire, non — c'est le prix de ne pas la
   stocker en clair. La nouvelle clé s'affiche sur la même page que celle de
   l'inscription, une seule fois.
2. **Fermé par défaut côté lecture.** `/compte` exige une session ouverte par
   la page de connexion (D62, `app/routes/connexion.py`), ou le couple
   email/mot de passe en HTTP Basic envoyé par un script. Base injoignable =
   `503` explicite : jamais des zéros décoratifs à la place de l'absence de
   données (même règle que `/prospection`).
3. **Honnête et autonome.** Aucun script, aucun appel sortant, aucune ressource
   externe. Les statistiques viennent de `customer_validations` ; l'estimation
   d'économies est affichée **avec sa formule**, dans la monnaie de la langue
   (`i18n.prix(lang, "colis_rate")`, dont la base en dollars reste
   `accounts.COUT_ECHEC_USD`), et la facturation dit qu'elle est vide au lieu
   d'inventer un montant. La clé s'envoie dans l'en-tête `X-API-Key`.

Le dessin vient des maquettes validées (`docs/maquettes/site-multilingue/`
`inscription-*`, `inscription-cle-*`, `compte-*`) et vit dans
`app/templates/<page>/<langue>.html`. Les champs `{{…}}` sont remplis et
échappés par `i18n.rendre` ; deux marques de plus servent à ce que l'état d'un
compte change : `<!--@si:nom-->…<!--@fin:nom-->` (bloc gardé ou retiré) et
`<!--@nom-->` (fragment construit ici, valeurs échappées). Les messages que le
serveur produit (refus, 409, 429, 503, états du compte) sont dans `TEXTES`, une
entrée par langue ; le français est la formulation d'avant.

Le français reste sur `/signup`, `/compte`, `/compte/cle` ; les autres langues
ont leurs chemins **littéraux** (`/en/signup`…), et un `POST` répond dans la
langue de son chemin. Le plafond d'inscription (`5 par IP et par heure`) vient
de la configuration, s'appuie sur `app/ratelimit.py` (Redis partagé, repli
local compté) et vaut pour toutes les langues ensemble.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import accounts, i18n, ratelimit
from app.config import get_settings
from app.database import SessionLocal
from app.observability import client_ip
from app.routes import connexion
from app.routes.dashboard import UNAUTHORIZED_HEADERS, _decode_basic
from app.web import PRIVATE_HEADERS, public_headers

router = APIRouter()

GABARITS = Path(__file__).resolve().parents[1] / "templates"

#: Pages qui viennent de contenir, ou contiennent, une clé API : jamais de cache,
#: et un formulaire qui ne peut poster qu'ici (`form-action 'self'`). Le dessin
#: vit dans `app/web.py` (`PRIVATE_HEADERS`) : la même définition sert à
#: `/signup` et à `/validate-form`, qui ne peuvent donc pas diverger.
SECRET_HEADERS = PRIVATE_HEADERS

#: Fenêtre du plafond d'inscription (secondes) : « 5 par IP et **par heure** ».
FENETRE_INSCRIPTION = 3600

# Isolants Unicode (LRI, FSI, PDI) : en arabe, une date, un montant ou un nom de
# boutique latin gardent leur ordre, comme le `<bdi dir="ltr">` des maquettes.
_G, _A, _F = "\u2066", "\u2068", "\u2069"

TEXTES = {
    "fr": {
        "email_invalide": "L'adresse email ne semble pas valide.",
        "mdp_court": "Le mot de passe doit compter au moins {min} caractères.",
        "mdp_long": "Le mot de passe dépasse {max} octets (au-delà, bcrypt ignorerait la fin : mieux vaut un mot de passe plus court).",
        "boutique_requise": "Le nom de la boutique est requis.",
        "plateforme": "Choisissez une plateforme : Shopify, WooCommerce ou Autre.",
        "email_pris": "Un compte existe déjà avec cet email. Connectez-vous depuis {compte}, ou utilisez une autre adresse.",
        "trop": (
            "Trop de tentatives d'inscription : le plafond de {limite} inscriptions par adresse IP et par heure est atteint. "
            "C'est un garde-fou contre la création de comptes en série ; il se réinitialise tout seul.",
            "Réessayez dans moins d'une heure. Si un vrai client est bloqué, écrivez-nous : le compteur se lève à la main.",
        ),
        "indisponible": (
            "Base de données indisponible : les comptes clients sont stockés dans PostgreSQL. Le service est démarré, "
            "mais sa base ne répond pas : aucun compte ne peut être créé ni consulté pour l'instant.",
            "Réessayez dans quelques minutes. Aucune donnée n'a été perdue : rien n'a été écrit.",
        ),
        "detail": "Détail technique : {detail}",
        "auth_requise": "Authentification requise : email du compte et mot de passe (HTTP Basic). La clé API ne donne pas accès à l'espace client.",
        "auth_refus": "Email ou mot de passe incorrect.",
        "plans": {"trial": "Essai gratuit", "starter": "Starter", "pro": "Pro", "enterprise": "Enterprise"},
        "illimite": "illimité",
        "sans_plafond": "sans plafond",
        "cree": {
            "titre": "Votre compte est créé",
            "h1": "Votre compte est créé.",
            "pastille": "Compte d'essai créé pour {boutique}",
            "intro": "Voici votre clé API. C'est la seule fois que nous pouvons vous la montrer : nous n'en conservons qu'une empreinte.",
        },
        "regeneree": {
            "titre": "Votre nouvelle clé API",
            "h1": "Votre nouvelle clé API.",
            "pastille": "Nouvelle clé pour {boutique}",
            "intro": "L'ancienne clé ne fonctionne plus. Voici la nouvelle : c'est la seule fois que nous pouvons vous la montrer, nous n'en conservons qu'une empreinte.",
        },
        "essai_aucun": "Aucune échéance d'essai (plan payant).",
        "essai_fini": "Votre essai est terminé (fin le {date}).",
        "essai_en_cours": "Essai en cours : {n} jour(s) restant(s), jusqu'au {date}.",
        "jours_ecoules": ("{n} jour écoulé", "{n} jours écoulés"),
        "jours_restants": ("{n} restant sur {total}", "{n} restants sur {total}"),
        "utilisees": ("utilisée", "utilisées"),
        "jauge": "Jauge : {n} sur {quota} validations utilisées, reste {r}.",
        "case": ("Une case égale une validation.", "Une case égale {n} validations."),
        "calcul": (
            "Calcul : {n} adresse invalide multipliée par {cout} égale {total} d'économies estimées.",
            "Calcul : {n} adresses invalides multipliées par {cout} égalent {total} d'économies estimées.",
        ),
        "etats": {
            "expire": {
                "code": "403", "titre": "Essai terminé",
                "msg": "{essai_fini}<br><strong>Essai terminé :</strong> vos validations par clé API sont suspendues jusqu'au passage à un plan payant ({prix_starter}/mois pour {quota_starter} validations, {prix_pro}/mois pour {quota_pro}).",
                "suite": "Les appels reçoivent 403, compte expiré, tant que le compte reste sur l'essai. Le passage se fait sur demande, rien n'est prélevé automatiquement.",
            },
            "quota": {
                "code": "402", "titre": "Quota du mois atteint",
                "msg": "<strong>Quota atteint :</strong> vos {quota} validations du mois sont consommées. Le service par clé API est suspendu jusqu'au premier jour du mois suivant (UTC) ; écrivez-nous pour passer au plan supérieur.",
                "suite": 'Les appels reçoivent 402 avec <code class="inline">"reason": "{motif}"</code> jusqu\'au 1<sup>er</sup> du mois à 00:00 UTC, ou jusqu\'au passage au plan supérieur.',
            },
            "payant": {
                "code": "{plan}", "titre": "Plan payant",
                "msg": "{essai_aucun}",
                "suite": "Pas de date de fin : seul le quota du mois limite les appels, {quota_starter} en Starter, {quota_pro} en Pro. En Enterprise, le quota est illimité et le reste s'affiche « {sans_plafond} ».",
            },
        },
    },
    "en": {
        "email_invalide": "The email address does not look valid.",
        "mdp_court": "The password must be at least {min} characters long.",
        "mdp_long": "The password is longer than {max} bytes (beyond that, bcrypt would ignore the end: a shorter password is better).",
        "boutique_requise": "The store name is required.",
        "plateforme": "Choose a platform: Shopify, WooCommerce or Other.",
        "email_pris": "An account already exists with this email. Sign in at {compte}, or use another address.",
        "trop": (
            "Too many signup attempts: the limit of {limite} signups per IP address per hour has been reached. "
            "It is a safeguard against mass account creation; it resets on its own.",
            "Try again within the hour. If a real customer is blocked, write to us: the counter can be lifted by hand.",
        ),
        "indisponible": (
            "Database unavailable: customer accounts are stored in PostgreSQL. The service is running, "
            "but its database is not responding: no account can be created or viewed for now.",
            "Try again in a few minutes. No data was lost: nothing was written.",
        ),
        "detail": "Technical detail: {detail}",
        "auth_requise": "Authentication required: account email and password (HTTP Basic). The API key does not give access to the dashboard.",
        "auth_refus": "Incorrect email or password.",
        "plans": {"trial": "Free trial", "starter": "Starter", "pro": "Pro", "enterprise": "Enterprise"},
        "illimite": "unlimited",
        "sans_plafond": "no cap",
        "cree": {
            "titre": "Your account is created",
            "h1": "Your account is created.",
            "pastille": "Trial account created for {boutique}",
            "intro": "Here is your API key. This is the only time we can show it to you: we keep only a fingerprint of it.",
        },
        "regeneree": {
            "titre": "Your new API key",
            "h1": "Your new API key.",
            "pastille": "New key for {boutique}",
            "intro": "The old key no longer works. Here is the new one: this is the only time we can show it to you, we keep only a fingerprint of it.",
        },
        "essai_aucun": "No trial deadline (paid plan).",
        "essai_fini": "Your trial has ended (ended {date}).",
        "essai_en_cours": "Trial in progress: {n} day(s) left, until {date}.",
        "jours_ecoules": ("{n} day elapsed", "{n} days elapsed"),
        "jours_restants": ("{n} left out of {total}", "{n} left out of {total}"),
        "utilisees": ("used", "used"),
        "jauge": "Gauge: {n} of {quota} validations used, {r} left.",
        "case": ("One cell equals one validation.", "One cell equals {n} validations."),
        "calcul": (
            "Calculation: {n} invalid address multiplied by {cout} equals {total} in estimated savings.",
            "Calculation: {n} invalid addresses multiplied by {cout} equals {total} in estimated savings.",
        ),
        "etats": {
            "expire": {
                "code": "403", "titre": "Trial ended",
                "msg": "{essai_fini}<br><strong>Trial ended:</strong> your API key validations are suspended until you move to a paid plan ({prix_starter}/month for {quota_starter} validations, {prix_pro}/month for {quota_pro}).",
                "suite": "Calls receive 403, account expired, as long as the account stays on the trial. The switch is done on request, and nothing is charged automatically.",
            },
            "quota": {
                "code": "402", "titre": "Monthly quota reached",
                "msg": "<strong>Quota reached:</strong> your {quota} validations for the month are used up. API key service is suspended until the first day of the next month (UTC); write to us to move up to the next plan.",
                "suite": 'Calls receive 402 with <code class="inline">"reason": "{motif}"</code> until the 1st of the month at 00:00 UTC, or until you move up to the next plan.',
            },
            "payant": {
                "code": "{plan}", "titre": "Paid plan",
                "msg": "{essai_aucun}",
                "suite": 'No end date: only the monthly quota limits calls, {quota_starter} on Starter, {quota_pro} on Pro. On Enterprise, the quota is unlimited and the remainder shows as "{sans_plafond}".',
            },
        },
    },
    "zh": {
        "email_invalide": "邮箱地址似乎无效。",
        "mdp_court": "密码至少需要 {min} 个字符。",
        "mdp_long": "密码超过 {max} 字节（超出的部分 bcrypt 会忽略：请改用更短的密码）。",
        "boutique_requise": "请填写店铺名称。",
        "plateforme": "请选择平台：Shopify、WooCommerce 或其他。",
        "email_pris": "该邮箱已有账户。请在 {compte} 登录，或改用其他邮箱地址。",
        "trop": (
            "注册尝试次数过多：已达到每个 IP 地址每小时 {limite} 次注册的上限。这是防止批量创建账户的保护措施，会自动重置。",
            "请在一小时内重试。如果是真实客户被拦下，请写信给我们：计数可以手动解除。",
        ),
        "indisponible": (
            "数据库不可用：客户账户保存在 PostgreSQL 中。服务已启动，但数据库没有响应：目前无法创建或查看任何账户。",
            "请几分钟后再试。没有任何数据丢失：没有写入任何内容。",
        ),
        "detail": "技术细节：{detail}",
        "auth_requise": "需要身份验证：账户邮箱和密码（HTTP Basic）。API 密钥无法用来登录客户中心。",
        "auth_refus": "邮箱或密码不正确。",
        "plans": {"trial": "免费试用", "starter": "Starter", "pro": "Pro", "enterprise": "Enterprise"},
        "illimite": "不限",
        "sans_plafond": "无上限",
        "cree": {
            "titre": "您的账户已创建",
            "h1": "您的账户已创建。",
            "pastille": "已为 {boutique} 创建试用账户",
            "intro": "这是您的 API 密钥。我们只能向您显示这一次：服务器上只保存它的指纹。",
        },
        "regeneree": {
            "titre": "您的新 API 密钥",
            "h1": "您的新 API 密钥。",
            "pastille": "已为 {boutique} 生成新密钥",
            "intro": "旧密钥已失效。这是新密钥：我们只能向您显示这一次，服务器上只保存它的指纹。",
        },
        "essai_aucun": "没有试用截止日期（付费套餐）。",
        "essai_fini": "您的试用已结束（截止于 {date}）。",
        "essai_en_cours": "试用进行中：剩余 {n} 天，截至 {date}。",
        "jours_ecoules": ("已过 {n} 天", "已过 {n} 天"),
        "jours_restants": ("{total} 天中剩余 {n} 天", "{total} 天中剩余 {n} 天"),
        "utilisees": ("", ""),
        "jauge": "用量条：{quota} 次验证中已用 {n} 次，剩余 {r} 次。",
        "case": ("一格代表一次验证。", "一格代表 {n} 次验证。"),
        "calcul": ("计算：{n} 个无效地址乘以 {cout}，等于预估节省 {total}。", "计算：{n} 个无效地址乘以 {cout}，等于预估节省 {total}。"),
        "etats": {
            "expire": {
                "code": "403", "titre": "试用已结束",
                "msg": "{essai_fini}<br><strong>试用已结束：</strong>通过 API 密钥进行的验证已暂停，升级为付费套餐后恢复（每月 {prix_starter} 含 {quota_starter} 次验证，每月 {prix_pro} 含 {quota_pro} 次验证）。",
                "suite": "只要账户仍停留在试用套餐，调用就会收到 403（账户已过期）。升级需提出申请，不会自动扣款。",
            },
            "quota": {
                "code": "402", "titre": "已达本月额度",
                "msg": "<strong>已达额度：</strong>您本月的 {quota} 次验证已用完。通过 API 密钥的服务将暂停，直到下个月第一天（UTC）；如需升级到更高的套餐，请写信联系我们。",
                "suite": '在下月 1 日 00:00 UTC 之前，或升级到更高的套餐之前，调用都会收到 402 和 <code class="inline">"reason": "{motif}"</code>。',
            },
            "payant": {
                "code": "{plan}", "titre": "付费套餐",
                "msg": "{essai_aucun}",
                "suite": "没有截止日期：只有月度额度限制调用次数，Starter 为 {quota_starter} 次，Pro 为 {quota_pro} 次。Enterprise 的额度不限次数，剩余次数显示为“{sans_plafond}”。",
            },
        },
    },
    "hi": {
        "email_invalide": "ईमेल पता मान्य नहीं लगता।",
        "mdp_court": "पासवर्ड में कम से कम {min} अक्षर होने चाहिए।",
        "mdp_long": "पासवर्ड {max} बाइट से लंबा है (उससे आगे bcrypt अंत का हिस्सा अनदेखा कर देता: छोटा पासवर्ड बेहतर है)।",
        "boutique_requise": "स्टोर का नाम ज़रूरी है।",
        "plateforme": "एक प्लेटफ़ॉर्म चुनें: Shopify, WooCommerce या अन्य।",
        "email_pris": "इस ईमेल से पहले से एक खाता मौजूद है। {compte} से लॉग इन करें, या कोई दूसरा पता इस्तेमाल करें।",
        "trop": (
            "साइन-अप की बहुत ज़्यादा कोशिशें: प्रति IP पता प्रति घंटे {limite} साइन-अप की सीमा पूरी हो गई है। "
            "यह थोक में खाते बनाने के ख़िलाफ़ एक सुरक्षा है; यह अपने आप रीसेट हो जाती है।",
            "एक घंटे के भीतर फिर कोशिश करें। अगर कोई असली ग्राहक रुक गया है, तो हमें लिखें: काउंटर हाथ से हटाया जा सकता है।",
        ),
        "indisponible": (
            "डेटाबेस उपलब्ध नहीं है: ग्राहक खाते PostgreSQL में रखे जाते हैं। सेवा चालू है, लेकिन उसका डेटाबेस जवाब नहीं दे रहा: "
            "फ़िलहाल कोई खाता न बनाया जा सकता है, न देखा जा सकता है।",
            "कुछ मिनट बाद फिर कोशिश करें। कोई डेटा नहीं खोया: कुछ भी लिखा नहीं गया।",
        ),
        "detail": "तकनीकी विवरण: {detail}",
        "auth_requise": "प्रमाणीकरण ज़रूरी है: खाते का ईमेल और पासवर्ड (HTTP Basic)। API कुंजी से ग्राहक पोर्टल में पहुँच नहीं मिलती।",
        "auth_refus": "ईमेल या पासवर्ड गलत है।",
        "plans": {"trial": "निःशुल्क ट्रायल", "starter": "Starter", "pro": "Pro", "enterprise": "Enterprise"},
        "illimite": "असीमित",
        "sans_plafond": "कोई सीमा नहीं",
        "cree": {
            "titre": "आपका खाता बन गया है",
            "h1": "आपका खाता बन गया है।",
            "pastille": "{boutique} के लिए ट्रायल खाता बना",
            "intro": "यह आपकी API कुंजी है। हम इसे आपको सिर्फ़ यही एक बार दिखा सकते हैं: हम इसका केवल फ़िंगरप्रिंट रखते हैं।",
        },
        "regeneree": {
            "titre": "आपकी नई API कुंजी",
            "h1": "आपकी नई API कुंजी।",
            "pastille": "{boutique} के लिए नई कुंजी",
            "intro": "पुरानी कुंजी अब काम नहीं करती। यह नई कुंजी है: हम इसे आपको सिर्फ़ यही एक बार दिखा सकते हैं, हम इसका केवल फ़िंगरप्रिंट रखते हैं।",
        },
        "essai_aucun": "ट्रायल की कोई समय-सीमा नहीं (सशुल्क प्लान)।",
        "essai_fini": "आपका ट्रायल समाप्त हो गया है ({date} को)।",
        "essai_en_cours": "ट्रायल चालू है: {n} दिन शेष, {date} तक।",
        "jours_ecoules": ("{n} दिन बीत चुका", "{n} दिन बीत चुके"),
        "jours_restants": ("{total} में से {n} शेष", "{total} में से {n} शेष"),
        "utilisees": ("उपयोग हुआ", "उपयोग हुए"),
        "jauge": "गेज: {quota} में से {n} वैलिडेशन उपयोग हुए, {r} शेष।",
        "case": ("एक खाना एक वैलिडेशन के बराबर है।", "एक खाना {n} वैलिडेशन के बराबर है।"),
        "calcul": (
            "गणना: {n} अमान्य पता गुणा {cout} बराबर {total} अनुमानित बचत।",
            "गणना: {n} अमान्य पते गुणा {cout} बराबर {total} अनुमानित बचत।",
        ),
        "etats": {
            "expire": {
                "code": "403", "titre": "ट्रायल समाप्त",
                "msg": "{essai_fini}<br><strong>ट्रायल समाप्त:</strong> API कुंजी से वैलिडेशन तब तक रुके हैं जब तक सशुल्क प्लान नहीं लिया जाता ({quota_starter} वैलिडेशन के लिए {prix_starter}/माह, {quota_pro} के लिए {prix_pro}/माह)।",
                "suite": "जब तक खाता ट्रायल पर रहता है, कॉल को 403, खाता समाप्त, मिलता है। बदलाव अनुरोध पर होता है, कुछ भी अपने आप नहीं कटता।",
            },
            "quota": {
                "code": "402", "titre": "इस माह का कोटा पूरा",
                "msg": "<strong>कोटा पूरा:</strong> इस माह के आपके {quota} वैलिडेशन खर्च हो चुके हैं। API कुंजी की सेवा अगले माह के पहले दिन (UTC) तक रुकी है; ऊपर के प्लान पर जाने के लिए हमें लिखें।",
                "suite": 'कॉल को 402 मिलता है, <code class="inline">"reason": "{motif}"</code> माह की 1 तारीख़ 00:00 UTC तक, या ऊपर के प्लान पर जाने तक।',
            },
            "payant": {
                "code": "{plan}", "titre": "सशुल्क प्लान",
                "msg": "{essai_aucun}",
                "suite": "कोई समाप्ति तिथि नहीं: कॉल केवल माह का कोटा सीमित करता है, Starter में {quota_starter}, Pro में {quota_pro}। Enterprise में कोटा असीमित है और बाकी जगह «{sans_plafond}» दिखता है।",
            },
        },
    },
    "es": {
        "email_invalide": "La dirección de correo no parece válida.",
        "mdp_court": "La contraseña debe tener al menos {min} caracteres.",
        "mdp_long": "La contraseña supera los {max} bytes (más allá, bcrypt ignoraría el final: mejor una contraseña más corta).",
        "boutique_requise": "El nombre de la tienda es obligatorio.",
        "plateforme": "Elige una plataforma: Shopify, WooCommerce u Otra.",
        "email_pris": "Ya existe una cuenta con este correo. Inicia sesión desde {compte} o usa otra dirección.",
        "trop": (
            "Demasiados intentos de registro: se ha alcanzado el límite de {limite} registros por dirección IP y por hora. "
            "Es una protección contra la creación de cuentas en serie; se reinicia sola.",
            "Vuelve a intentarlo en menos de una hora. Si un cliente real está bloqueado, escríbenos: el contador se levanta a mano.",
        ),
        "indisponible": (
            "Base de datos no disponible: las cuentas de clientes se guardan en PostgreSQL. El servicio está en marcha, "
            "pero su base no responde: por ahora no se puede crear ni consultar ninguna cuenta.",
            "Vuelve a intentarlo en unos minutos. No se ha perdido ningún dato: no se ha escrito nada.",
        ),
        "detail": "Detalle técnico: {detail}",
        "auth_requise": "Autenticación requerida: correo y contraseña de la cuenta (HTTP Basic). La clave de API no da acceso al área de cliente.",
        "auth_refus": "Correo o contraseña incorrectos.",
        "plans": {"trial": "Prueba gratuita", "starter": "Starter", "pro": "Pro", "enterprise": "Enterprise"},
        "illimite": "ilimitado",
        "sans_plafond": "sin tope",
        "cree": {
            "titre": "Tu cuenta está creada",
            "h1": "Tu cuenta está creada.",
            "pastille": "Cuenta de prueba creada para {boutique}",
            "intro": "Esta es tu clave de API. Es la única vez que podemos mostrártela: solo conservamos su huella.",
        },
        "regeneree": {
            "titre": "Tu nueva clave de API",
            "h1": "Tu nueva clave de API.",
            "pastille": "Nueva clave para {boutique}",
            "intro": "La clave anterior ya no funciona. Esta es la nueva: es la única vez que podemos mostrártela, solo conservamos su huella.",
        },
        "essai_aucun": "Sin fecha de fin de prueba (plan de pago).",
        "essai_fini": "Tu prueba ha terminado (fin el {date}).",
        "essai_en_cours": "Prueba en curso: quedan {n} día(s), hasta el {date}.",
        "jours_ecoules": ("{n} día transcurrido", "{n} días transcurridos"),
        "jours_restants": ("{n} restante de {total}", "{n} restantes de {total}"),
        "utilisees": ("usada", "usadas"),
        "jauge": "Indicador: {n} de {quota} validaciones usadas, quedan {r}.",
        "case": ("Una casilla equivale a una validación.", "Una casilla equivale a {n} validaciones."),
        "calcul": (
            "Cálculo: {n} dirección inválida multiplicada por {cout} da {total} de ahorro estimado.",
            "Cálculo: {n} direcciones inválidas multiplicadas por {cout} dan {total} de ahorro estimado.",
        ),
        "etats": {
            "expire": {
                "code": "403", "titre": "Prueba terminada",
                "msg": "{essai_fini}<br><strong>Prueba terminada:</strong> tus validaciones con clave de API quedan suspendidas hasta que pases a un plan de pago ({prix_starter}/mes por {quota_starter} validaciones, {prix_pro}/mes por {quota_pro}).",
                "suite": "Las llamadas reciben 403, cuenta caducada, mientras la cuenta siga en la prueba. El paso se hace a petición y no se cobra nada automáticamente.",
            },
            "quota": {
                "code": "402", "titre": "Cuota del mes alcanzada",
                "msg": "<strong>Cuota alcanzada:</strong> has consumido tus {quota} validaciones del mes. El servicio con clave de API queda suspendido hasta el primer día del mes siguiente (UTC); escríbenos para pasar al plan superior.",
                "suite": 'Las llamadas reciben 402 con <code class="inline">"reason": "{motif}"</code> hasta el día 1 del mes a las 00:00 UTC, o hasta el paso al plan superior.',
            },
            "payant": {
                "code": "{plan}", "titre": "Plan de pago",
                "msg": "{essai_aucun}",
                "suite": "Sin fecha de fin: solo la cuota del mes limita las llamadas, {quota_starter} en Starter y {quota_pro} en Pro. En Enterprise, la cuota es ilimitada y el resto se muestra como «{sans_plafond}».",
            },
        },
    },
    "ar": {
        "email_invalide": "لا يبدو عنوان البريد الإلكتروني صحيحًا.",
        "mdp_court": "يجب أن تتكون كلمة المرور من {min} حرفًا على الأقل.",
        "mdp_long": "تتجاوز كلمة المرور {max} بايت (ما بعد ذلك سيتجاهله bcrypt: الأفضل كلمة مرور أقصر).",
        "boutique_requise": "اسم المتجر مطلوب.",
        "plateforme": "اختر منصة: Shopify أو WooCommerce أو أخرى.",
        "email_pris": f"يوجد حساب بهذا البريد الإلكتروني مسبقًا. سجّل الدخول من {_G}{{compte}}{_F}، أو استخدم عنوانًا آخر.",
        "trop": (
            f"محاولات تسجيل كثيرة جدًا: بلغت الحد الأقصى للتسجيلات لكل عنوان IP في الساعة ({_G}{{limite}}{_F}). "
            "هذا إجراء وقائي ضد إنشاء الحسابات بالجملة، ويُعاد ضبطه تلقائيًا.",
            "أعد المحاولة خلال أقل من ساعة. إذا كان عميل حقيقي محظورًا، راسلنا: يمكن رفع العدّاد يدويًا.",
        ),
        "indisponible": (
            "قاعدة البيانات غير متاحة: تُخزَّن حسابات العملاء في PostgreSQL. الخدمة تعمل، لكن قاعدة بياناتها لا تستجيب: "
            "لا يمكن إنشاء أي حساب أو الاطلاع عليه حاليًا.",
            "أعد المحاولة بعد بضع دقائق. لم تُفقد أي بيانات: لم يُكتب أي شيء.",
        ),
        "detail": f"تفاصيل تقنية: {_G}{{detail}}{_F}",
        "auth_requise": "المصادقة مطلوبة: البريد الإلكتروني للحساب وكلمة المرور (HTTP Basic). مفتاح API لا يمنح الوصول إلى حساب العميل.",
        "auth_refus": "البريد الإلكتروني أو كلمة المرور غير صحيحة.",
        "plans": {"trial": "التجربة المجانية", "starter": "Starter", "pro": "Pro", "enterprise": "Enterprise"},
        "illimite": "غير محدودة",
        "sans_plafond": "دون سقف",
        "cree": {
            "titre": "تم إنشاء حسابك",
            "h1": "تم إنشاء حسابك.",
            "pastille": f"إنشاء حساب التجربة لمتجر {_A}{{boutique}}{_F}",
            "intro": "إليك مفتاح API الخاص بك. هذه هي المرة الوحيدة التي يمكننا فيها عرضه عليك: لا نحتفظ إلا ببصمته.",
        },
        "regeneree": {
            "titre": "مفتاح API الجديد",
            "h1": "مفتاح API الجديد الخاص بك.",
            "pastille": f"مفتاح جديد لمتجر {_A}{{boutique}}{_F}",
            "intro": "المفتاح القديم لم يعد يعمل. إليك المفتاح الجديد: هذه هي المرة الوحيدة التي يمكننا فيها عرضه عليك، ولا نحتفظ إلا ببصمته.",
        },
        "essai_aucun": "لا موعد لانتهاء تجربة (باقة مدفوعة).",
        "essai_fini": f"انتهت تجربتك (في {_G}{{date}}{_F}).",
        # Arabe : « الأيام المتبقية : n » s'accorde avec tout nombre (pas de duel ni de pluriel à choisir).
        "essai_en_cours": f"التجربة جارية: الأيام المتبقية {_G}{{n}}{_F}، حتى {_G}{{date}}{_F}.",
        "jours_ecoules": (f"الأيام المنقضية: {_G}{{n}}{_F}", f"الأيام المنقضية: {_G}{{n}}{_F}"),
        "jours_restants": (f"بقي {_G}{{n}}{_F} من {_G}{{total}}{_F}", f"بقي {_G}{{n}}{_F} من {_G}{{total}}{_F}"),
        "utilisees": ("مستخدمة", "مستخدمة"),
        "jauge": f"مقياس: {_G}{{n}}{_F} من أصل {_G}{{quota}}{_F} عملية تحقق مستخدمة، والمتبقي {_G}{{r}}{_F}.",
        "case": ("كل خانة تساوي عملية تحقق واحدة.", f"كل خانة تساوي {_G}{{n}}{_F} عملية تحقق."),
        "calcul": (
            f"الحساب: عدد العناوين غير الصالحة {_G}{{n}}{_F} مضروبًا في {_G}{{cout}}{_F} يساوي {_G}{{total}}{_F} توفيرًا تقديريًا.",
            f"الحساب: عدد العناوين غير الصالحة {_G}{{n}}{_F} مضروبًا في {_G}{{cout}}{_F} يساوي {_G}{{total}}{_F} توفيرًا تقديريًا.",
        ),
        "etats": {
            "expire": {
                "code": "403", "titre": "انتهت التجربة",
                "msg": '{essai_fini}<br><strong>انتهت التجربة:</strong> عمليات التحقق بمفتاح API معلّقة حتى الانتقال إلى باقة مدفوعة (<bdi dir="ltr">{prix_starter}</bdi> شهريًا مقابل {quota_starter} عملية تحقق، و<bdi dir="ltr">{prix_pro}</bdi> شهريًا مقابل {quota_pro}).',
                "suite": "تتلقى الاستدعاءات الرمز 403، أي حساب منتهي الصلاحية، ما دام الحساب على باقة التجربة. يتم الانتقال عند الطلب، ولا يُخصم أي مبلغ تلقائيًا.",
            },
            "quota": {
                "code": "402", "titre": "بلغت حصة الشهر",
                "msg": '<strong>بلغت الحصة:</strong> استُهلكت عمليات التحقق المشمولة لهذا الشهر (<bdi dir="ltr">{quota}</bdi>). الخدمة بمفتاح API معلّقة حتى اليوم الأول من الشهر التالي (UTC)؛ راسلنا للانتقال إلى الباقة الأعلى.',
                "suite": 'تتلقى الاستدعاءات الرمز 402 مع <code class="inline" dir="ltr">"reason": "{motif}"</code> حتى اليوم الأول من الشهر الساعة <bdi dir="ltr">00:00 UTC</bdi>، أو حتى الانتقال إلى الباقة الأعلى.',
            },
            "payant": {
                "code": "{plan}", "titre": "باقة مدفوعة",
                "msg": "{essai_aucun}",
                "suite": "لا تاريخ انتهاء: حصة الشهر وحدها تحدّ الاستدعاءات، {quota_starter} في Starter و{quota_pro} في Pro. في Enterprise، الحصة غير محدودة ويظهر المتبقي «{sans_plafond}».",
            },
        },
    },
}

#: Mois écrits dans une phrase (le chinois écrit le numéro du mois).
MOIS = {
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
    "zh": tuple(str(numero) for numero in range(1, 13)),
    "hi": ("जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्तूबर", "नवंबर", "दिसंबर"),
    "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"),
    "ar": ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"),
}
MOIS_COURTS = {
    **MOIS,
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "hi": ("जन.", "फ़र.", "मार्च", "अप्रैल", "मई", "जून", "जुल.", "अग.", "सित.", "अक्तू.", "नव.", "दिस."),
    "es": ("ene.", "feb.", "mar.", "abr.", "may.", "jun.", "jul.", "ago.", "sept.", "oct.", "nov.", "dic."),
}
#: Formes de date des maquettes : 12/11/2026 (11/12/2026 en anglais), 12 nov., 12/11, novembre 2026.
DATES = {
    "fr": {"longue": "{j:02}/{m:02}/{a}", "courte": "{j} {mc}", "chiffres": "{j:02}/{m:02}", "mois": "{mois} {a}"},
    "en": {"longue": "{m:02}/{j:02}/{a}", "courte": "{mc} {j}", "chiffres": "{m:02}/{j:02}", "mois": "{mois} {a}"},
    "zh": {"longue": "{a} 年 {m} 月 {j} 日", "courte": "{m} 月 {j} 日", "chiffres": "{m} 月 {j} 日", "mois": "{a} 年 {m} 月"},
    "hi": {"longue": "{j:02}/{m:02}/{a}", "courte": "{j} {mc}", "chiffres": "{j:02}/{m:02}", "mois": "{mois} {a}"},
    "es": {"longue": "{j:02}/{m:02}/{a}", "courte": "{j} {mc}", "chiffres": "{j:02}/{m:02}", "mois": "{mois} de {a}"},
    "ar": {"longue": "{j:02}/{m:02}/{a}", "courte": "{j} {mc}", "chiffres": "{j:02}/{m:02}", "mois": "{mois} {a}"},
}

CLASSES_ETAT = {"expire": "s-expired", "quota": "s-quota", "payant": "s-paid"}

_BLOC = re.compile(r"<!--@si:(\w+)-->(.*?)<!--@fin:\1-->", re.S)


def _session():
    """Session SQLAlchemy. Le point unique que les tests remplacent (SQLite)."""
    return SessionLocal()


def _date(lang: str, moment: datetime, forme: str) -> str:
    return DATES[lang][forme].format(
        j=moment.day, m=moment.month, a=moment.year,
        mois=MOIS[lang][moment.month - 1], mc=MOIS_COURTS[lang][moment.month - 1],
    )


def _pluriel(lang: str, n: int, formes: tuple[str, str]) -> str:
    """Singulier ou pluriel. fr et hi mettent 0 et 1 au singulier ; zh n'accorde pas."""
    # ponytail: deux formes seulement ; l'arabe est écrit sans accord (« الأيام المتبقية : n »).
    singulier = n <= 1 if lang in ("fr", "hi") else n == 1
    return formes[0 if singulier else 1]


def _montant(lang: str, valeur) -> str:
    """Un montant dans la monnaie de la langue, écrit comme `i18n.prix` l'écrit."""
    devise = i18n.DEVISE[lang]
    texte = i18n.nombre(lang, valeur)
    return f"{texte} €" if devise == "EUR" else i18n.SYMBOLE[devise] + texte


def _quota(lang: str, quota: int | None) -> str:
    return TEXTES[lang]["illimite"] if quota is None else i18n.nombre(lang, quota)


def _page(gabarit: str, lang: str, valeurs: dict, blocs=frozenset(), fragments=None) -> str:
    """Le gabarit rempli (`i18n.rendre`, valeurs échappées), ses blocs gardés ou
    retirés, ses fragments posés. Une marque oubliée lève une erreur au lieu de
    publier un bloc qui ne correspond pas à l'état du compte."""
    texte = i18n.rendre(GABARITS / gabarit / f"{lang}.html", valeurs)
    texte = _BLOC.sub(lambda bloc: bloc.group(2) if bloc.group(1) in blocs else "", texte)
    for nom, fragment in (fragments or {}).items():
        texte = texte.replace(f"<!--@{nom}-->", fragment)
    if "<!--@" in texte:
        raise RuntimeError(f"gabarit {gabarit}/{lang}.html : un bloc ou un fragment n'est pas traité")
    return texte


def _communs(lang: str, page: str) -> dict:
    """Champs partagés : adresses de la langue, contact, prix et quotas des plans."""
    settings = get_settings()
    return {
        **i18n.liens(lang, page),
        "url_compte_cle": i18n.chemin(lang, "compte") + "/cle",
        "contact": settings.contact_email,
        "prix_x402": i18n.nombre(lang, settings.x402_price),
        "prix_starter": i18n.prix(lang, "starter"),
        "prix_pro": i18n.prix(lang, "pro"),
        "quota_starter": i18n.nombre(lang, accounts.PLANS["starter"]),
        "quota_pro": i18n.nombre(lang, accounts.PLANS["pro"]),
        "mention_euros": i18n.mention_euros(lang),
    }


def _formulaire(
    lang: str,
    *,
    email: str = "",
    boutique: str = "",
    plateforme: str = "",
    erreurs: tuple[str, ...] = (),
    statut: int = 200,
    entetes: dict | None = None,
) -> HTMLResponse:
    """Le formulaire d'inscription. Les champs saisis sont **réaffichés**, sauf
    le mot de passe (jamais conservé, jamais renvoyé)."""
    settings = get_settings()
    valeurs = {
        **_communs(lang, "signup"),
        "robots": "index, follow" if statut == 200 else "noindex, nofollow",
        "jours_essai": i18n.nombre(lang, settings.trial_days),
        "validations_essai": i18n.nombre(lang, accounts.PLANS[accounts.PLAN_PAR_DEFAUT]),
        "prix_essai": i18n.prix(lang, "essai"),
        "mdp_min": accounts.PASSWORD_MIN_LENGTH,
        "mdp_max": accounts.PASSWORD_MAX_BYTES,
        "email": email,
        "boutique": boutique,
        **{f"choix_{nom}": " selected" if nom == plateforme else "" for nom in accounts.PLATEFORMES},
        "prefixe_cle": accounts.API_KEY_PREFIX,
        "longueur_cle": accounts.API_KEY_CHARS,
    }
    alerte = ""
    if erreurs:
        items = "".join(f"<li>{html.escape(erreur)}</li>" for erreur in erreurs)
        alerte = f'<div class="alert" role="alert"><ul>{items}</ul></div>'
    page = _page("inscription", lang, valeurs, fragments={"alerte": alerte})
    if entetes is None:
        entetes = public_headers(form=True) if statut == 200 else SECRET_HEADERS
    return HTMLResponse(page, status_code=statut, headers=entetes)


def _indisponible(lang: str, exc: Exception, **champs) -> Response:
    """Base injoignable : `503` qui dit quoi faire, jamais une page vide."""
    textes = TEXTES[lang]
    erreurs = (*textes["indisponible"], textes["detail"].format(detail=str(exc)[:300]))
    return _formulaire(lang, erreurs=erreurs, statut=503, **champs)


def _trop_de_tentatives(lang: str, limite: int) -> HTMLResponse:
    """`429` : le plafond d'inscription par IP est atteint, et il le dit."""
    return _formulaire(
        lang,
        erreurs=tuple(phrase.format(limite=limite) for phrase in TEXTES[lang]["trop"]),
        statut=429,
        # Le client sait quand réessayer sans deviner.
        entetes={**SECRET_HEADERS, "Retry-After": str(FENETRE_INSCRIPTION)},
    )


def _refus(message: str) -> Response:
    """Refus d'authentification, avec le défi Basic (aucun secret dans le corps)."""
    return Response(
        status_code=401,
        content=message,
        media_type="text/plain; charset=utf-8",
        headers=UNAUTHORIZED_HEADERS,
    )


def _page_cle(lang: str, *, infos: dict, cle: str, statut: dict, regeneration: bool, maintenant: datetime) -> HTMLResponse:
    """La **seule** page qui montre la clé en clair : `no-store`, non indexée."""
    textes = TEXTES[lang]
    variante = textes["regeneree" if regeneration else "cree"]
    fin = statut["trial_ends_at"]
    empreinte = accounts.api_key_hash(cle)
    valeurs = {
        **_communs(lang, "compte"),
        "titre": variante["titre"],
        "h1": variante["h1"],
        "pastille": variante["pastille"].format(boutique=infos["shop_name"]),
        "intro": variante["intro"],
        "boutique": infos["shop_name"],
        "cle": cle,
        "cle_prefixe": accounts.API_KEY_PREFIX,
        "cle_secret": cle[len(accounts.API_KEY_PREFIX):],
        "longueur_cle": accounts.API_KEY_CHARS,
        "empreinte": f"{empreinte[:8]}…{empreinte[-8:]}",
        "prefixe": infos["prefixe"],
        "plan": textes["plans"].get(statut["plan"], statut["plan"]),
        "quota": _quota(lang, statut["quota"]),
        "fin_essai": _date(lang, fin, "longue") if fin else "",
        "aujourdhui": _date(lang, maintenant, "chiffres"),
        "fin_courte": _date(lang, fin, "chiffres") if fin else "",
    }
    blocs = set()
    if fin is not None:
        blocs.add("fin_essai")
        if not statut["trial_expire"]:
            blocs.add("piste")
    return HTMLResponse(_page("inscription-cle", lang, valeurs, blocs), headers=SECRET_HEADERS)


def _etat(lang: str, nom: str, champs: dict) -> str:
    """Le bandeau d'état que le code produit vraiment : 403, 402 ou plan payant."""
    etat = TEXTES[lang]["etats"][nom]
    surs = {cle: html.escape(str(valeur)) for cle, valeur in champs.items()}
    mention = i18n.mention_euros(lang) if nom == "expire" else ""
    note = f'<p class="then">{html.escape(mention)}</p>' if mention else ""
    return (
        f'<div class="state {CLASSES_ETAT[nom]}" role="status"><div class="state-top">'
        f'<span class="code">{etat["code"].format(**surs)}</span><h3>{html.escape(etat["titre"])}</h3></div>'
        f'<p class="msg">{etat["msg"].format(**surs)}</p>{note}<p class="then">{etat["suite"].format(**surs)}</p></div>'
    )


def _compte_page(lang: str, *, infos: dict, statut: dict, maintenant: datetime, jours: list, erreur_cle: str = "") -> HTMLResponse:
    """L'espace client : le tableau de bord du compte (D64, `app/routes/tableau.py`).

    Chaque chiffre a une source : `customer_validations` pour la consommation (à vie,
    du mois, et jour par jour), `i18n.prix(lang, "colis_rate")` pour l'estimation,
    dont la formule est affichée.
    """
    # `tableau` réutilise les outils de ce module (dates, montants, bandeaux) : importé à l'appel.
    from app.routes import tableau

    return tableau.page(lang, infos=infos, statut=statut, maintenant=maintenant, jours=jours, erreur_cle=erreur_cle)


async def _inscrire(request: Request, lang: str) -> Response:
    """Crée le compte, puis **montre la clé une fois** (`no-store`)."""
    settings = get_settings()
    textes = TEXTES[lang]
    # Une seule fenêtre pour toutes les langues : changer de langue ne rouvre pas le plafond.
    autorise, _restants = ratelimit.allow(
        f"signup:{client_ip(request)}", settings.signup_rate_limit_per_hour, FENETRE_INSCRIPTION
    )
    if not autorise:
        return _trop_de_tentatives(lang, settings.signup_rate_limit_per_hour)

    donnees = await request.form()
    email = str(donnees.get("email", "") or "")
    mot_de_passe = str(donnees.get("password", "") or "")
    boutique = str(donnees.get("shop_name", "") or "")
    plateforme = str(donnees.get("platform", "") or "")
    # D54 : l'inscription libre ne crée que des comptes d'essai. Le plan n'est
    # jamais lu dans le formulaire (le client l'aurait choisi lui-même) ; un plan
    # payant s'accorde côté exploitant.
    plan = accounts.PLAN_PAR_DEFAUT
    champs = {"email": email, "boutique": boutique, "plateforme": plateforme}

    erreurs: list[str] = []
    if not accounts.valid_email(email):
        erreurs.append(textes["email_invalide"])
    if accounts.password_problem(mot_de_passe):
        # Les deux règles d'`accounts.password_problem`, dites dans la langue de la page.
        if len(mot_de_passe) < accounts.PASSWORD_MIN_LENGTH:
            erreurs.append(textes["mdp_court"].format(min=accounts.PASSWORD_MIN_LENGTH))
        else:
            erreurs.append(textes["mdp_long"].format(max=accounts.PASSWORD_MAX_BYTES))
    if not boutique.strip():
        erreurs.append(textes["boutique_requise"])
    if plateforme not in accounts.PLATEFORMES:
        erreurs.append(textes["plateforme"])
    if erreurs:
        return _formulaire(lang, erreurs=tuple(erreurs), statut=400, **champs)

    maintenant = accounts.now_utc()
    try:
        with _session() as session:
            client, cle = accounts.create_customer(
                session,
                email=email,
                password=mot_de_passe,
                shop_name=boutique,
                platform=plateforme,
                plan=plan,
                maintenant=maintenant,
                jours_essai=settings.trial_days,
            )
            infos = {"shop_name": client.shop_name, "prefixe": client.api_key_prefix}
            statut = accounts.plan_status(session, client, maintenant)
    except IntegrityError:
        # L'email est deja pris : on le dit, parce qu'un client qui a deja un
        # compte doit pouvoir le comprendre sans appeler le support.
        erreur = textes["email_pris"].format(compte=i18n.chemin(lang, "compte"))
        return _formulaire(lang, erreurs=(erreur,), statut=409, **champs)
    except SQLAlchemyError as exc:
        return _indisponible(lang, exc, **champs)

    return _page_cle(lang, infos=infos, cle=cle, statut=statut, regeneration=False, maintenant=maintenant)


def _infos(client) -> dict:
    return {
        "shop_name": client.shop_name,
        "email": client.email,
        "prefixe": client.api_key_prefix,
        "cree_le": client.created_at,
    }


def _espace_basic(request: Request, lang: str, *, regeneration: bool) -> Response:
    """HTTP Basic envoyé par un script : même compte, même blocage des échecs (D62)."""
    textes = TEXTES[lang]
    identifiants = _decode_basic(request.headers.get("authorization", ""))
    if identifiants is None:
        return _refus(textes["auth_requise"])
    email, mot_de_passe = identifiants
    cles = connexion.cles_echecs(request, email)
    attente = connexion.attente_blocage(cles)
    if attente is not None:
        message = connexion.TEXTES[lang]["bloque"].format(attente=max(1, (attente + 59) // 60))
        return Response(message, status_code=429, media_type="text/plain; charset=utf-8",
                        headers={**PRIVATE_HEADERS, "Retry-After": str(attente)})

    maintenant = accounts.now_utc()
    try:
        with _session() as session:
            client = accounts.authenticate(session, email, mot_de_passe)
            if client is None:
                connexion.compter_echec(cles)
                return _refus(textes["auth_refus"])
            cle = accounts.regenerate_api_key(session, client) if regeneration else None
            infos = _infos(client)
            statut = accounts.plan_status(session, client, maintenant)
            jours = accounts.daily_usage(session, client, maintenant)
    except SQLAlchemyError as exc:
        return _indisponible(lang, exc)

    if cle:
        return _page_cle(lang, infos=infos, cle=cle, statut=statut, regeneration=True, maintenant=maintenant)
    return _compte_page(lang, infos=infos, statut=statut, maintenant=maintenant, jours=jours)


async def _espace_client(request: Request, lang: str, *, regeneration: bool) -> Response:
    """Espace client : lecture, ou régénération de clé.

    Un navigateur arrive avec le cookie de session (D62) ; sans session valable,
    il est renvoyé vers la page de connexion de sa langue (`303`), jamais vers la
    fenêtre grise du défi Basic. Régénérer la clé redemande le mot de passe : un
    cookie volé ne suffit pas à obtenir une nouvelle clé.
    """
    if request.headers.get("authorization", "").lower().startswith("basic "):
        return _espace_basic(request, lang, regeneration=regeneration)
    if regeneration and connexion.origine_etrangere(request):
        return connexion.page(lang, erreur=connexion.TEXTES[lang]["origine"], statut=403)
    mot_de_passe = str((await request.form()).get("password", "") or "") if regeneration else ""

    maintenant = accounts.now_utc()
    erreur_cle, cle = "", None
    try:
        with _session() as session:
            client = accounts.session_customer(session, connexion.jeton(request), maintenant)
            if client is None:
                return RedirectResponse(connexion.chemin_connexion(lang), status_code=303, headers=dict(SECRET_HEADERS))
            if regeneration:
                cles = connexion.cles_echecs(request, client.email)
                attente = connexion.attente_blocage(cles)
                if attente is not None:
                    erreur_cle = connexion.TEXTES[lang]["bloque"].format(attente=max(1, (attente + 59) // 60))
                elif accounts.verify_password(mot_de_passe, client.password_hash):
                    cle = accounts.regenerate_api_key(session, client)
                else:
                    connexion.compter_echec(cles)
                    erreur_cle = connexion.TEXTES[lang]["mdp_cle"]
            infos = _infos(client)
            statut = accounts.plan_status(session, client, maintenant)
            jours = accounts.daily_usage(session, client, maintenant)
    except SQLAlchemyError as exc:
        return _indisponible(lang, exc)

    if cle:
        return _page_cle(lang, infos=infos, cle=cle, statut=statut, regeneration=True, maintenant=maintenant)
    reponse = _compte_page(lang, infos=infos, statut=statut, maintenant=maintenant, jours=jours, erreur_cle=erreur_cle)
    if erreur_cle:
        reponse.status_code = 400
    return reponse


@router.get("/signup")
async def signup_form() -> Response:
    """Le formulaire d'inscription (public, indexable, sans état)."""
    return _formulaire(i18n.DEFAUT)


@router.post("/signup")
async def signup_submit(request: Request) -> Response:
    """Crée le compte, puis **montre la clé une fois** (`no-store`)."""
    return await _inscrire(request, i18n.DEFAUT)


@router.get("/compte")
async def compte(request: Request) -> Response:
    return await _espace_client(request, i18n.DEFAUT, regeneration=False)


@router.post("/compte/cle")
async def compte_nouvelle_cle(request: Request) -> Response:
    """Régénère la clé : la nouvelle est affichée une fois, l'ancienne meurt."""
    return await _espace_client(request, i18n.DEFAUT, regeneration=True)


def _routes(lang: str):
    """Les quatre routes d'une langue préfixée, qui répondent dans cette langue."""

    async def formulaire() -> Response:
        return _formulaire(lang)

    async def inscription(request: Request) -> Response:
        return await _inscrire(request, lang)

    async def espace(request: Request) -> Response:
        return await _espace_client(request, lang, regeneration=False)

    async def nouvelle_cle(request: Request) -> Response:
        return await _espace_client(request, lang, regeneration=True)

    return (
        (i18n.chemin(lang, "signup"), formulaire, "GET"),
        (i18n.chemin(lang, "signup"), inscription, "POST"),
        (i18n.chemin(lang, "compte"), espace, "GET"),
        (i18n.chemin(lang, "compte") + "/cle", nouvelle_cle, "POST"),
    )


# Chemins **littéraux** (`/en/signup`…), jamais `/{lang}/…` : un paramètre de chemin
# avalerait d'autres routes (même règle que la vitrine, D57).
for _lang in i18n.PREFIXEES:
    for _chemin, _route, _methode in _routes(_lang):
        router.add_api_route(_chemin, _route, methods=[_methode], include_in_schema=False)
