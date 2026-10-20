"""`/validate-form` — l'essai gratuit public (décision D46, élément 3), en six langues (D57).

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
  * **elle est autonome** : aucun script, aucune ressource externe, `form-action
    'self'`. C'est la page qu'un visiteur voit en premier : elle ne charge rien,
    et c'est vérifiable par le navigateur (CSP) plutôt que par la seule lecture
    du code.

Enfin, une page qui affiche une adresse ne se met **pas** en cache : la réponse
d'un `POST` porte `PRIVATE_HEADERS` (`no-store`, `noindex`), tandis que le
formulaire vide est indexable et cachable quelques minutes — il ne contient
aucune donnée client.

Six langues (D57). Le dessin vient des maquettes validées
(`docs/maquettes/site-multilingue/test-<langue>.html`) et vit dans
`app/templates/validate_form/<langue>.html`. Le français reste sur
`/validate-form`, les autres langues vivent sous `/<langue>/validate-form`
(chemins littéraux), et un `POST` répond dans la langue de son adresse. Le
plafond reste **par IP**, quelle que soit la langue : changer de langue n'ouvre
pas de nouveaux essais. Les quatre écrans d'exemple de la maquette sont devenus
les vrais états de la réponse (adresse reconnue, non reconnue, fournisseur
injoignable, plafond atteint), remplis par le vrai résultat du pipeline, le vrai
nombre d'essais restants et le vrai `Retry-After`. Les messages vivent dans
`TEXTES`, par langue ; le français garde la formulation d'avant, sans tiret
cadratin (maquette validée). Aucun chiffre du code n'est recopié (D48) : plafond,
fenêtre, essai, prix x402, contact et repères de la jauge (calculés par le
barème) sont injectés.

D86 : le formulaire accepte aussi une adresse tapée **sur une seule ligne**
(champ `address`, celui de la page d'accueil). `decouper` la range dans les
champs de l'API : États-Unis quand elle finit par un État, France ou outre-mer
quand un code postal précède la ville, États-Unis sinon ; une ligne américaine
sans virgule part entière dans `street`, que SmartyStreets découpe lui-même. Le
fournisseur rend ensuite l'adresse corrigée (D84) : le visiteur voit la bonne
forme, champ par champ.
"""

from __future__ import annotations

import html
import math
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app import accounts, i18n, ratelimit, seo
from app.config import get_settings
from app.observability import client_ip
from app.routes.metrics import metrics
from app.routes.validate import perform_validation
from app.validation.scorer import score_deliverability
from app.web import PRIVATE_HEADERS, public_headers

router = APIRouter()

GABARITS = Path(__file__).resolve().parents[1] / "templates" / "validate_form"

#: Fenêtre du plafond d'essais gratuits : une journée annoncée comme telle
#: (« 3 essais par jour »). La fenêtre est fixe et démarre au premier essai, ce
#: que la page dit au visiteur pour qu'il n'ait pas à le deviner.
FENETRE_ESSAI_SECONDES = 24 * 60 * 60

#: Champs du formulaire, dans l'ordre d'une adresse postale : `(nom, libellé,
#: exemple)`. Ce sont ceux de l'API payante (`app/schemas.py`) — une adresse qui
#: passe ici passe là-bas. Version française ; les autres langues sont dans `TEXTES`.
CHAMPS: tuple[tuple[str, str, str], ...] = (
    ("street", "Numéro et rue", "1 Rue de Paris"),
    ("city", "Ville", "Saint-Denis"),
    ("state", "Région ou état", "RE (La Réunion), CA, Île-de-France…"),
    ("zip", "Code postal", "97400"),
    ("country", "Pays (2 lettres)", "US"),
)

#: Ce que le navigateur peut pré-remplir dans chaque champ.
AUTOCOMPLETE = {
    "street": "address-line1",
    "city": "address-level2",
    "state": "address-level1",
    "zip": "postal-code",
    "country": "country",
}

#: Pays retenu quand le visiteur laisse le champ vide : la même valeur par défaut
#: que l'API payante (`app/schemas.py`, `AddressInput.country`).
PAYS_PAR_DEFAUT = "US"

#: D86 : longueur maximale d'une adresse tapée sur une ligne.
LIGNE_MAX = 200
#: États et territoires américains : une ligne qui finit par l'un d'eux (avec ou
#: sans ZIP) est américaine, même si elle contient un nombre à 5 chiffres.
ETATS_US = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
    "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR VI GU AS MP".split()
)
#: Codes postaux d'outre-mer -> pays vérifiés par la BAN (`ban.PAYS_BAN`), du plus
#: précis au plus large : Saint-Barthélemy et Saint-Martin avant la Guadeloupe.
OUTRE_MER = {"97133": "BL", "97150": "MF", "971": "GP", "972": "MQ", "973": "GF", "974": "RE", "975": "PM", "976": "YT"}


def decouper(ligne: str) -> dict[str, str]:
    """D86 : une adresse tapée sur une ligne, rangée dans les champs de l'API."""
    texte = " ".join(ligne.split()).strip(" ,")
    pays_dit = re.search(r",?\s*\b(france|usa|united states)\s*$", texte, re.I)
    if pays_dit:
        texte = texte[:pays_dit.start()].strip(" ,")
    dit = (pays_dit.group(1).lower() == "france" and "FR" or "US") if pays_dit else ""
    fin = re.search(r"\b([A-Za-z]{2})(?:\s+\d{5}(?:-\d{4})?)?$", texte)
    finit_par_un_etat = fin is not None and fin.group(1).upper() in ETATS_US
    francaise = re.search(r"^(.+?)[\s,]+(\d{5})\s+([^\d,][^,]*)$", texte)
    if francaise and (dit == "FR" or (not dit and not finit_par_un_etat)):
        code = francaise.group(2)
        pays = next((p for prefixe, p in OUTRE_MER.items() if code.startswith(prefixe)), "FR")
        return {"street": francaise.group(1).strip(" ,"), "city": francaise.group(3).strip(), "state": "", "zip": code, "country": pays}
    americaine = re.search(r"^(.+?),\s*([^,]+?),?\s+([A-Za-z]{2})(?:\s+(\d{5}(?:-\d{4})?))?$", texte)
    if americaine and americaine.group(3).upper() in ETATS_US:
        rue, ville, etat, zip_code = americaine.groups()
        return {"street": rue, "city": ville, "state": etat, "zip": zip_code or "", "country": "US"}
    return {"street": texte, "city": "", "state": "", "zip": "", "country": PAYS_PAR_DEFAUT}

#: Repères de la jauge de confiance : ce que le barème (`app/validation/scorer.py`)
#: rend à une adresse inexistante, inconnue (DPV « X ») et résidentielle (DPV « Y »).
#: Calculés à chaque réponse, jamais recopiés (D48). `(valide, dpv, couleur, point)`.
REPERES = (
    (False, "N", "#e5484d", "#b42318"),
    (True, "X", "#ffad3b", "#9a5b00"),
    (True, "Y", "#18b47c", "#0a7350"),
)

#: Messages de la page, par langue. Une valeur `dict` s'accorde au nombre
#: (catégories de `_categorie`, repli sur `other`). Les textes sont du HTML de
#: confiance ; toute valeur insérée (`{...}`) est échappée par l'appelant. Les
#: erreurs de saisie (`requis`, `pays_invalide`) sont du texte brut, échappé au rendu.
TEXTES: dict[str, dict] = {
    "fr": {
        "champs": CHAMPS,
        "titre_form": "Votre adresse",
        "indice": "Pour un résultat complet, prenez une adresse des États-Unis : ailleurs, la vérification reste partielle.",
        "aide_pays": "Laissé vide : {pays}.",
        "bouton": "Vérifier l'adresse",
        "note_form": "Sans compte, sans carte bancaire. Le résultat s'affiche sous le formulaire.",
        "requis": "{libelle} : champ requis.",
        "pays_invalide": "Pays : deux lettres attendues (US, FR, RE…).",
        "essais": {"one": "{n} essai", "other": "{n} essais"},
        "resultat": "Résultat",
        "ok": "Adresse reconnue",
        "ok_verdict": "Le transporteur devrait livrer à cette adresse.",
        "ko": "Adresse non reconnue",
        "ko_verdict": "Vérifiez le numéro, la voie et le code postal avant d'expédier à cette adresse.",
        "degrade": "Vérification impossible pour l'instant",
        "degrade_verdict": "Le fournisseur d'adresses n'a pas répondu. La réponse ci-dessous n'est <strong>pas</strong> un jugement sur votre adresse.",
        "degrade_note": "<strong>Résultat dégradé :</strong> le fournisseur d'adresses n'a pas pu être interrogé (panne, délai dépassé ou disjoncteur ouvert). Cette réponse ne dit rien de l'adresse : elle n'est pas une preuve d'inexistence. Réessayez plus tard.",
        "sans_portee": "Valeurs sans portée : le fournisseur n'a pas vérifié l'adresse {adresse}.",
        "standardisee": "Adresse standardisée",
        "saisie": "Votre saisie",
        "corrige": "corrigé par la standardisation",
        "coordonnees": "Coordonnées",
        "coordonnees_sr": "Coordonnées : ",
        "geocodage": "Obtenues par géocodage, à titre indicatif.",
        "geocodage_suite": ", obtenues par géocodage, à titre indicatif.",
        "livrabilite": "Livrabilité",
        "confiance": "Indice de confiance",
        "jauge": "indice de confiance, de 0 à 1",
        "reperes": "Repères de l'indice de confiance",
        "cette_adresse": ", cette adresse",
        "reste": {
            "one": "Il vous reste {n} essai gratuit aujourd'hui.",
            "other": "Il vous reste {n} essais gratuits aujourd'hui.",
        },
        "suite": "Pour un usage régulier : <a href=\"{signup}\">créez un compte</a> ({jours} jours et {validations} validations offertes), ou appelez l'API payante ({prix} la validation).",
        "dernier": "C'était votre dernier essai gratuit de la journée.",
        "dernier_suite": "La fenêtre de {heures} h se referme au plus tard {heures} h après votre premier essai : <a href=\"{signup}\">créez un compte</a> pour continuer maintenant ({validations} validations offertes, sans carte bancaire).",
        "pied": "Résultat de l'essai gratuit, non enregistré.",
        "plafond_titre": "Plafond d'essais gratuits atteint",
        "plafond_texte": {
            "one": "{n} essai gratuit par jour et par visiteur : c'est le garde-fou qui permet d'offrir l'essai sans ouvrir le fournisseur payant à tout Internet.",
            "other": "{n} essais gratuits par jour et par visiteur : c'est le garde-fou qui permet d'offrir l'essai sans ouvrir le fournisseur payant à tout Internet.",
        },
        "attente": "{n} min",
        "fenetre": "avant la fin de votre fenêtre de {heures} h",
        "minutes": {"one": "{n} minute", "other": "{n} minutes"},
        "rappel": "<strong>Votre fenêtre de {heures} h se referme dans environ {minutes}.</strong> Elle démarre au premier essai et ne se prolonge pas à chaque tentative. Vous pouvez aussi continuer tout de suite avec un compte : {validations} validations offertes, {jours} jours, sans carte bancaire.",
        "creer": "Créer un compte",
        "tarifs": "Voir les tarifs",
        "legitime": "Un cas légitime bloqué (adresses de test, volume ponctuel) ? <a href=\"mailto:{contact}\">Écrivez-nous</a> : le compteur se lève à la main.",
        "pied_plafond": "Page servie sans cache, non indexée.",
    },
    "en": {
        "champs": (
            ("street", "Street and number", "100 Stewart St"),
            ("city", "City", "Seattle"),
            ("state", "State or region", "WA"),
            ("zip", "ZIP / postal code", "98101"),
            ("country", "Country (2 letters)", "US"),
        ),
        "titre_form": "Your address",
        "indice": "For a complete result, use a United States address: elsewhere, verification is partial.",
        "aide_pays": "Left blank: {pays}.",
        "bouton": "Check the address",
        "note_form": "No account, no credit card. The result appears below the form.",
        "requis": "{libelle}: required field.",
        "pays_invalide": "Country: two letters expected (US, FR, RE…).",
        "essais": {"one": "{n} try", "other": "{n} tries"},
        "resultat": "Result",
        "ok": "Address recognized",
        "ok_verdict": "The carrier should be able to deliver to this address.",
        "ko": "Address not recognized",
        "ko_verdict": "Check the number, the street and the ZIP code before shipping to this address.",
        "degrade": "Verification not possible right now",
        "degrade_verdict": "The address provider did not answer. The response below is <strong>not</strong> a verdict on your address.",
        "degrade_note": "<strong>Degraded result:</strong> the address provider could not be queried (outage, timeout or open circuit breaker). This response says nothing about the address: it is not proof that it does not exist. Try again later.",
        "sans_portee": "Values with no meaning: the provider did not verify the address {adresse}.",
        "standardisee": "Standardized address",
        "saisie": "As you typed it",
        "corrige": "corrected by standardization",
        "coordonnees": "Coordinates",
        "coordonnees_sr": "Coordinates: ",
        "geocodage": "Obtained by geocoding, for reference only.",
        "geocodage_suite": ", obtained by geocoding, for reference only.",
        "livrabilite": "Deliverability",
        "confiance": "Confidence score",
        "jauge": "confidence score, from 0 to 1",
        "reperes": "Confidence score reference points",
        "cette_adresse": ", this address",
        "reste": {
            "one": "You have {n} free try left today.",
            "other": "You have {n} free tries left today.",
        },
        "suite": "For regular use: <a href=\"{signup}\">create an account</a> ({jours} days and {validations} free validations), or call the paid API ({prix} per validation).",
        "dernier": "That was your last free try of the day.",
        "dernier_suite": "The {heures}-hour window closes at the latest {heures} hours after your first try: <a href=\"{signup}\">create an account</a> to continue now ({validations} free validations, no credit card).",
        "pied": "Free trial result, not stored.",
        "plafond_titre": "Free-trial cap reached",
        "plafond_texte": "Free tries are capped at {n} per day per visitor: this is the safeguard that lets us offer the trial without opening the paid provider to the whole internet.",
        "attente": "{n} min",
        "fenetre": "until your {heures}-hour window ends",
        "minutes": {"one": "{n} minute", "other": "{n} minutes"},
        "rappel": "<strong>Your {heures}-hour window closes in about {minutes}.</strong> It starts at the first try and does not extend with each attempt. You can also continue right away with an account: {validations} free validations, {jours} days, no credit card.",
        "creer": "Create an account",
        "tarifs": "See pricing",
        "legitime": "A legitimate case blocked (test addresses, one-off volume)? <a href=\"mailto:{contact}\">Write to us</a>: the counter can be lifted by hand.",
        "pied_plafond": "Page served without cache, not indexed.",
    },
    "zh": {
        "champs": (
            ("street", "门牌号和街道", "100 Stewart St"),
            ("city", "城市", "Seattle"),
            ("state", "州或地区", "WA"),
            ("zip", "邮政编码", "98101"),
            ("country", "国家（2 位字母）", "US"),
        ),
        "titre_form": "您的地址",
        "indice": "想看到完整结果，请使用美国地址：其他地区的验证只是部分验证。",
        "aide_pays": "留空则默认为 {pays}。",
        "bouton": "验证地址",
        "note_form": "无需账户，无需信用卡。结果显示在表单下方。",
        "requis": "{libelle}：必填项。",
        "pays_invalide": "国家：需要两位字母（US、FR、RE…）。",
        "essais": "{n} 次",
        "resultat": "结果",
        "ok": "地址已识别",
        "ok_verdict": "承运商应能送达该地址。",
        "ko": "地址未识别",
        "ko_verdict": "寄往该地址之前，请先核对门牌号、街道和邮政编码。",
        "degrade": "暂时无法验证",
        "degrade_verdict": "地址服务商没有响应。下面的响应<strong>不是</strong>对您地址的判断。",
        "degrade_note": "<strong>降级结果：</strong>无法查询地址服务商（故障、超时或熔断器已打开）。该响应不说明地址本身的任何情况，也不能证明地址不存在。请稍后重试。",
        "sans_portee": "这些数值没有参考意义：服务商并未验证地址 {adresse}。",
        "standardisee": "标准化地址",
        "saisie": "您的输入",
        "corrige": "已通过标准化修正",
        "coordonnees": "坐标",
        "coordonnees_sr": "坐标：",
        "geocodage": "通过地理编码获得，仅供参考。",
        "geocodage_suite": "，通过地理编码获得，仅供参考。",
        "livrabilite": "可送达性",
        "confiance": "置信度",
        "jauge": "置信度，范围 0 到 1",
        "reperes": "置信度参考值",
        "cette_adresse": "，即该地址",
        "reste": "今天还剩 {n} 次免费测试。",
        "suite": "经常使用的话，请<a href=\"{signup}\">创建账户</a>（免费试用 {jours} 天，{validations} 次验证），或调用按次付费的 API（每次验证 {prix}）。",
        "dernier": "这是您今天最后一次免费测试。",
        "dernier_suite": "{heures} 小时窗口最迟在您第一次测试后 {heures} 小时结束：<a href=\"{signup}\">创建账户</a>即可马上继续（免费 {validations} 次验证，无需信用卡）。",
        "pied": "免费测试结果，不作保存。",
        "plafond_titre": "已达免费测试上限",
        "plafond_texte": "免费测试按天、按访客设有上限，每位访客每天 {n} 次：有了这道保护，才能提供试用，而不会把付费服务商开放给整个互联网。",
        "attente": "{n} 分钟",
        "fenetre": "{heures} 小时窗口结束前的剩余时间",
        "minutes": "{n} 分钟",
        "rappel": "<strong>您的 {heures} 小时窗口大约 {minutes}后结束。</strong>窗口从第一次测试开始计时，不会因每次尝试而延长。您也可以马上用账户继续：免费 {validations} 次验证，{jours} 天，无需信用卡。",
        "creer": "创建账户",
        "tarifs": "查看价格",
        "legitime": "正当用途被拦住了（测试地址、一次性的用量）？<a href=\"mailto:{contact}\">请写信给我们</a>：我们可以手动解除计数。",
        "pied_plafond": "页面不缓存，也不被收录。",
    },
    "hi": {
        "champs": (
            ("street", "मकान नंबर और सड़क", "100 Stewart St"),
            ("city", "शहर", "Seattle"),
            ("state", "राज्य या क्षेत्र", "WA"),
            ("zip", "ज़िप कोड", "98101"),
            ("country", "देश (2 अक्षर)", "US"),
        ),
        "titre_form": "आपका पता",
        "indice": "पूरा नतीजा चाहिए तो अमेरिका का पता लें: बाकी जगह जाँच आंशिक रहती है।",
        "aide_pays": "खाली छोड़ने पर: {pays}।",
        "bouton": "पता जाँचें",
        "note_form": "बिना खाते के, क्रेडिट कार्ड की ज़रूरत नहीं। नतीजा फ़ॉर्म के नीचे दिखेगा।",
        "requis": "{libelle}: यह फ़ील्ड ज़रूरी है।",
        "pays_invalide": "देश: दो अक्षर चाहिए (US, FR, RE…)।",
        "essais": "{n} प्रयास",
        "resultat": "नतीजा",
        "ok": "पता पहचाना गया",
        "ok_verdict": "इस पते पर डिलीवरी हो जानी चाहिए।",
        "ko": "पता नहीं पहचाना गया",
        "ko_verdict": "इस पते पर भेजने से पहले मकान नंबर, सड़क और ज़िप कोड जाँच लें।",
        "degrade": "अभी जाँच संभव नहीं",
        "degrade_verdict": "पता प्रदाता ने उत्तर नहीं दिया। नीचे का उत्तर आपके पते के बारे में कोई निर्णय <strong>नहीं</strong> है।",
        "degrade_note": "<strong>डिग्रेडेड नतीजा:</strong> पता प्रदाता से पूछताछ नहीं हो सकी (खराबी, समय सीमा पार या सर्किट ब्रेकर खुला)। यह उत्तर पते के बारे में कुछ नहीं कहता: यह पते के न होने का सबूत नहीं है। बाद में फिर कोशिश करें।",
        "sans_portee": "ये मान बेमानी हैं: प्रदाता ने {adresse} की जाँच नहीं की।",
        "standardisee": "मानकीकृत पता",
        "saisie": "आपकी प्रविष्टि",
        "corrige": "मानकीकरण से सुधारा गया",
        "coordonnees": "निर्देशांक",
        "coordonnees_sr": "निर्देशांक: ",
        "geocodage": "जियोकोडिंग से प्राप्त, केवल संकेत के लिए।",
        "geocodage_suite": ", जियोकोडिंग से प्राप्त, केवल संकेत के लिए।",
        "livrabilite": "डिलीवरी योग्यता",
        "confiance": "भरोसा स्कोर",
        "jauge": "भरोसा स्कोर, 0 से 1 तक",
        "reperes": "भरोसा स्कोर के पैमाने",
        "cette_adresse": ", यही पता",
        "reste": {
            "one": "आज आपका {n} निःशुल्क प्रयास बचा है।",
            "other": "आज आपके {n} निःशुल्क प्रयास बचे हैं।",
        },
        "suite": "नियमित उपयोग के लिए: <a href=\"{signup}\">खाता बनाएँ</a> ({jours} दिन और {validations} वैलिडेशन निःशुल्क), या सशुल्क API कॉल करें ({prix} प्रति वैलिडेशन)।",
        "dernier": "यह आज का आपका आख़िरी निःशुल्क प्रयास था।",
        "dernier_suite": "{heures} घंटे की अवधि आपके पहले प्रयास के ज़्यादा से ज़्यादा {heures} घंटे बाद पूरी होती है: अभी जारी रखने के लिए <a href=\"{signup}\">खाता बनाएँ</a> ({validations} वैलिडेशन निःशुल्क, क्रेडिट कार्ड की ज़रूरत नहीं)।",
        "pied": "निःशुल्क ट्रायल का नतीजा, सहेजा नहीं गया।",
        "plafond_titre": "निःशुल्क प्रयासों की सीमा पूरी हुई",
        "plafond_texte": "निःशुल्क प्रयास हर विज़िटर के लिए रोज़ {n} तक सीमित हैं: यही सुरक्षा-सीमा है जिससे ट्रायल दिया जा सकता है, बिना सशुल्क प्रदाता को पूरे इंटरनेट के लिए खोले।",
        "attente": "{n} मिनट",
        "fenetre": "शेष: आपकी {heures} घंटे की अवधि खत्म होने तक",
        "minutes": "{n} मिनट",
        "rappel": "<strong>आपकी {heures} घंटे की अवधि लगभग {minutes} में पूरी हो जाएगी।</strong> वह पहले प्रयास से शुरू होती है और हर कोशिश पर आगे नहीं बढ़ती। आप चाहें तो खाता बनाकर तुरंत जारी रख सकते हैं: {validations} वैलिडेशन निःशुल्क, {jours} दिन, क्रेडिट कार्ड की ज़रूरत नहीं।",
        "creer": "खाता बनाएँ",
        "tarifs": "कीमतें देखें",
        "legitime": "कोई वैध मामला रुक गया (टेस्ट पते, एक बार का वॉल्यूम)? <a href=\"mailto:{contact}\">हमें लिखें</a>: काउंटर हाथ से हटाया जाता है।",
        "pied_plafond": "पृष्ठ बिना कैश के परोसा गया, इंडेक्स नहीं।",
    },
    "es": {
        "champs": (
            ("street", "Número y calle", "100 Stewart St"),
            ("city", "Ciudad", "Seattle"),
            ("state", "Estado o región", "WA"),
            ("zip", "Código postal", "98101"),
            ("country", "País (2 letras)", "US"),
        ),
        "titre_form": "Tu dirección",
        "indice": "Para un resultado completo, usa una dirección de Estados Unidos: en otros países, la verificación es parcial.",
        "aide_pays": "Si se deja vacío: {pays}.",
        "bouton": "Verificar la dirección",
        "note_form": "Sin cuenta, sin tarjeta de crédito. El resultado aparece debajo del formulario.",
        "requis": "{libelle}: campo obligatorio.",
        "pays_invalide": "País: se esperan dos letras (US, FR, RE…).",
        "essais": {"one": "{n} prueba", "other": "{n} pruebas"},
        "resultat": "Resultado",
        "ok": "Dirección reconocida",
        "ok_verdict": "El transportista debería poder entregar en esta dirección.",
        "ko": "Dirección no reconocida",
        "ko_verdict": "Revisa el número, la calle y el código postal antes de enviar a esta dirección.",
        "degrade": "Verificación imposible por ahora",
        "degrade_verdict": "El proveedor de direcciones no respondió. La respuesta de abajo <strong>no</strong> es un juicio sobre tu dirección.",
        "degrade_note": "<strong>Resultado degradado:</strong> no se pudo consultar al proveedor de direcciones (caída, tiempo de espera agotado o cortacircuitos abierto). Esta respuesta no dice nada de la dirección: no prueba que no exista. Inténtalo más tarde.",
        "sans_portee": "Valores sin alcance: el proveedor no verificó la dirección {adresse}.",
        "standardisee": "Dirección estandarizada",
        "saisie": "Lo que escribiste",
        "corrige": "corregido por la estandarización",
        "coordonnees": "Coordenadas",
        "coordonnees_sr": "Coordenadas: ",
        "geocodage": "Obtenidas por geocodificación, solo como referencia.",
        "geocodage_suite": ", obtenidas por geocodificación, solo como referencia.",
        "livrabilite": "Entregabilidad",
        "confiance": "Índice de confianza",
        "jauge": "índice de confianza, de 0 a 1",
        "reperes": "Referencias del índice de confianza",
        "cette_adresse": ", esta dirección",
        "reste": {
            "one": "Te queda {n} prueba gratuita hoy.",
            "other": "Te quedan {n} pruebas gratuitas hoy.",
        },
        "suite": "Para un uso regular: <a href=\"{signup}\">crea una cuenta</a> ({jours} días y {validations} validaciones gratis) o llama a la API de pago ({prix} por validación).",
        "dernier": "Era tu última prueba gratuita del día.",
        "dernier_suite": "La ventana de {heures} h se cierra como muy tarde {heures} h después de tu primera prueba: <a href=\"{signup}\">crea una cuenta</a> para seguir ahora ({validations} validaciones gratis, sin tarjeta de crédito).",
        "pied": "Resultado de la prueba gratuita, sin guardar.",
        "plafond_titre": "Límite de pruebas gratuitas alcanzado",
        "plafond_texte": "Las pruebas gratuitas tienen un tope de {n} al día por visitante: es la salvaguarda que permite ofrecer la prueba sin abrir el proveedor de pago a todo Internet.",
        "attente": "{n} min",
        "fenetre": "para que termine tu ventana de {heures} h",
        "minutes": {"one": "{n} minuto", "other": "{n} minutos"},
        "rappel": "<strong>Tu ventana de {heures} h se cierra en aproximadamente {minutes}.</strong> Empieza con la primera prueba y no se alarga con cada intento. También puedes seguir ahora mismo con una cuenta: {validations} validaciones gratis, {jours} días, sin tarjeta de crédito.",
        "creer": "Crear cuenta",
        "tarifs": "Ver los precios",
        "legitime": "¿Un caso legítimo bloqueado (direcciones de prueba, volumen puntual)? <a href=\"mailto:{contact}\">Escríbenos</a>: el contador se desbloquea a mano.",
        "pied_plafond": "Página servida sin caché y sin indexar.",
    },
    "ar": {
        "champs": (
            ("street", "الرقم والشارع", "100 Stewart St"),
            ("city", "المدينة", "Seattle"),
            ("state", "المنطقة أو الولاية", "WA"),
            ("zip", "الرمز البريدي", "98101"),
            ("country", "البلد (حرفان)", "US"),
        ),
        "titre_form": "عنوانك",
        "indice": "للحصول على نتيجة كاملة، استخدم عنوانًا في الولايات المتحدة: خارجها يبقى التحقق جزئيًا.",
        "aide_pays": "إذا تُرك فارغًا: {pays}.",
        "bouton": "تحقق من العنوان",
        "note_form": "دون حساب، ودون بطاقة بنكية. تظهر النتيجة أسفل النموذج.",
        "requis": "{libelle}: حقل مطلوب.",
        "pays_invalide": "البلد: يلزم حرفان، مثل US أو FR أو RE.",
        "essais": {"one": "تجربة واحدة", "two": "تجربتين", "few": "{n} تجارب", "other": "{n} تجربة"},
        "resultat": "النتيجة",
        "ok": "تم التعرّف على العنوان",
        "ok_verdict": "يُفترض أن تسلّم شركة الشحن إلى هذا العنوان.",
        "ko": "لم يُتعرَّف على العنوان",
        "ko_verdict": "تحقق من الرقم والشارع والرمز البريدي قبل الشحن إلى هذا العنوان.",
        "degrade": "يتعذّر التحقق في الوقت الحالي",
        "degrade_verdict": "لم يستجب مزوّد العناوين. الرد أدناه <strong>ليس</strong> حكمًا على عنوانك.",
        "degrade_note": "<strong>نتيجة منقوصة:</strong> تعذّر الاستعلام لدى مزوّد العناوين (عطل، أو تجاوز المهلة، أو قاطع دائرة مفتوح). هذا الرد لا يقول شيئًا عن العنوان: إنه ليس دليلًا على عدم وجوده. أعد المحاولة لاحقًا.",
        "sans_portee": "قيم بلا دلالة: لم يتحقق المزوّد من العنوان {adresse}.",
        "standardisee": "العنوان بصيغة موحّدة",
        "saisie": "ما أدخلته",
        "corrige": "صُحِّح عند التوحيد",
        "coordonnees": "الإحداثيات",
        "coordonnees_sr": "الإحداثيات: ",
        "geocodage": "ناتجة عن الترميز الجغرافي، على سبيل الإرشاد.",
        "geocodage_suite": "، ناتجة عن الترميز الجغرافي، على سبيل الإرشاد.",
        "livrabilite": "قابلية التسليم",
        "confiance": "مؤشر الثقة",
        "jauge": "مؤشر الثقة، من 0 إلى 1",
        "reperes": "علامات مؤشر الثقة",
        "cette_adresse": "، هذا العنوان",
        "reste": {
            "one": "بقيت لك تجربة مجانية واحدة اليوم.",
            "two": "بقيت لك تجربتان مجانيتان اليوم.",
            "few": "بقيت لك {n} تجارب مجانية اليوم.",
            "other": "بقيت لك {n} تجربة مجانية اليوم.",
        },
        "suite": "للاستخدام المنتظم: <a href=\"{signup}\">أنشئ حسابًا</a> ({jours} يومًا و{validations} عملية تحقق مجانًا)، أو استدعِ واجهة API المدفوعة ({prix} لكل عملية تحقق).",
        "dernier": "كانت هذه آخر تجربة مجانية لك اليوم.",
        "dernier_suite": "تُغلق نافذتك البالغة {heures} ساعة بعد {heures} ساعة على الأكثر من أول تجربة لك: <a href=\"{signup}\">أنشئ حسابًا</a> للمتابعة الآن ({validations} عملية تحقق مجانًا، دون بطاقة بنكية).",
        "pied": "نتيجة الاختبار المجاني، غير محفوظة.",
        "plafond_titre": "بلغت الحد الأقصى للتجارب المجانية",
        "plafond_texte": "الحد الأقصى للتجارب المجانية {n} يوميًا لكل زائر: هذا هو صمام الأمان الذي يتيح تقديم الاختبار دون فتح المزوّد المدفوع أمام الإنترنت كله.",
        "attente": {
            "one": "دقيقة واحدة",
            "two": "دقيقتان",
            "few": "<bdi class=\"lat\">{n}</bdi> دقائق",
            "other": "<bdi class=\"lat\">{n}</bdi> دقيقة",
        },
        "fenetre": "قبل انتهاء نافذتك البالغة {heures} ساعة",
        "minutes": {"one": "دقيقة واحدة", "two": "دقيقتين", "few": "{n} دقائق", "other": "{n} دقيقة"},
        "rappel": "<strong>تُغلق نافذتك البالغة {heures} ساعة بعد نحو {minutes}.</strong> تبدأ مع أول تجربة ولا تُمدَّد مع كل محاولة. ويمكنك أيضًا المتابعة فورًا بحساب: {validations} عملية تحقق مجانية، {jours} يومًا، دون بطاقة بنكية.",
        "creer": "أنشئ حسابًا",
        "tarifs": "اطّلع على الأسعار",
        "legitime": "حالة مشروعة محجوبة (عناوين اختبار، حجم مؤقت)؟ <a href=\"mailto:{contact}\">راسلنا</a>: يُرفع العدّاد يدويًا.",
        "pied_plafond": "صفحة تُقدَّم دون ذاكرة تخزين مؤقت، وغير مفهرسة.",
    },
}

FLECHE = (
    '<svg class="arr" viewBox="0 0 10 10" aria-hidden="true"><path class="l" d="M1 5h7"/>'
    '<path class="h" d="M3.5 1.5 7 5 3.5 8.5"/></svg>'
)
PASTILLES = {
    "ok": '<path d="m5.2 8.2 1.9 1.9 3.8-3.9"/>',
    "ko": '<path d="m5.8 5.8 4.4 4.4m0-4.4-4.4 4.4"/>',
    "info": '<path d="M8 4.6v4M8 11.2v.2"/>',
}
#: Le plan décoratif de la maquette : des rues et un repère, pas la vraie carte du lieu.
CARTE = """<svg viewBox="0 0 240 150" aria-hidden="true" preserveAspectRatio="xMidYMid slice">
                <rect width="240" height="150" fill="#eef2f7"/>
                <g transform="rotate(-28 120 75)">
                  <g fill="#e2e8f1">
                    <rect x="25" y="20" width="30" height="25" rx="2"/><rect x="65" y="55" width="30" height="25" rx="2"/>
                    <rect x="105" y="20" width="30" height="25" rx="2"/><rect x="145" y="125" width="30" height="25" rx="2"/>
                    <rect x="185" y="55" width="30" height="25" rx="2"/><rect x="25" y="90" width="30" height="25" rx="2"/>
                    <rect x="105" y="125" width="30" height="25" rx="2"/><rect x="225" y="90" width="30" height="25" rx="2"/>
                  </g>
                  <rect x="185" y="20" width="30" height="25" rx="3" fill="#d3ead6"/>
                  <rect x="65" y="125" width="30" height="25" rx="3" fill="#d3ead6"/>
                  <g stroke="#dde4ee" stroke-width="9" fill="none">
                    <path d="M-160 15H420M-160 50H420M-160 85H420M-160 120H420M-160 155H420M-160 190H420M-160 -20H420"/>
                    <path d="M20-160V340M60-160V340M100-160V340M140-160V340M180-160V340M220-160V340M260-160V340M-20-160V340"/>
                  </g>
                  <g stroke="#fff" fill="none">
                    <path stroke-width="6.5" d="M-160 15H420M-160 50H420M-160 85H420M-160 120H420M-160 155H420M-160 190H420M-160 -20H420"/>
                    <path stroke-width="6.5" d="M20-160V340M60-160V340M100-160V340M140-160V340M180-160V340M220-160V340M260-160V340M-20-160V340"/>
                  </g>
                </g>
                <path d="M0 0H62C55 22 46 42 33 62C22 79 10 92 0 100Z" fill="#cfe2f4"/>
                <path d="M10 18c6-3 12 3 18 0M8 40c6-3 12 3 18 0M6 62c5-3 10 3 15 0" stroke="#b7d1ea" stroke-width="1.5" fill="none" stroke-linecap="round"/>
                <g transform="translate(150 92)">
                  <ellipse cx="0" cy="1" rx="7" ry="2.4" fill="#0b1b34" opacity=".26"/>
                  <circle cx="0" cy="0" r="11" fill="none" stroke="#1f3bff" stroke-width="1.5" opacity=".3"/>
                  <g class="pin">
                    <path d="M0 0C-2.4-6.5-12-12-12-21.5a12 12 0 0 1 24 0C12-12 2.4-6.5 0 0Z" fill="#1f3bff"/>
                    <circle cx="0" cy="-21.5" r="4.6" fill="#fff"/>
                  </g>
                </g>
              </svg>"""

#: Repère d'un bloc posé après `i18n.rendre` (NUL : aucun gabarit n'en contient).
_BLOC = "\0{}\0"
_BLOCS = re.compile("\0(essai|resultat)\0")


def _categorie(lang: str, n: int | None) -> str:
    """Catégorie de pluriel (règles CLDR, réduites à ce que ces six langues écrivent ici)."""
    if n is None:
        return "other"
    if lang == "ar":
        if n in (0, 1, 2):
            return ("zero", "one", "two")[n]
        cent = n % 100
        return "few" if 3 <= cent <= 10 else "many" if cent >= 11 else "other"
    if lang in ("fr", "hi"):
        return "one" if n <= 1 else "other"
    if lang in ("en", "es"):
        return "one" if n == 1 else "other"
    return "other"  # zh : pas de pluriel


def _texte(lang: str, cle: str, n: int | None = None, **valeurs: str) -> str:
    """Le message `cle` dans la langue, accordé à `n` ; les valeurs sont déjà échappées."""
    texte = TEXTES[lang][cle]
    if isinstance(texte, dict):
        texte = texte.get(_categorie(lang, n), texte["other"])
    if n is not None:
        valeurs["n"] = i18n.nombre(lang, n)
    return texte.format(**valeurs)


def _erreurs(adresse: dict[str, str], lang: str = i18n.DEFAUT) -> list[str]:
    """Ce qui manque pour interroger le pipeline — mêmes champs que l'API payante.

    Les cinq champs sont obligatoires côté API payante (`app/schemas.py`) : les
    exiger ici aussi évite d'afficher un verdict calculé sur une adresse
    incomplète, qui serait un faux verdict. Texte brut, échappé au rendu.
    """
    erreurs = [
        _texte(lang, "requis", libelle=libelle)
        for nom, libelle, _exemple in TEXTES[lang]["champs"]
        if nom != "country" and not adresse.get(nom)
    ]
    if len(adresse.get("country", "")) != 2:
        erreurs.append(_texte(lang, "pays_invalide"))
    return erreurs


def _formulaire(lang: str, valeurs: dict[str, str], erreurs: list[str]) -> str:
    """Le formulaire de la maquette, réaffiché tel que le visiteur l'a saisi.

    Les règles du navigateur sont celles de `_erreurs` (quatre champs requis, pays
    de deux lettres ou vide) ; la saisie est échappée, comme partout ailleurs.
    """
    t = TEXTES[lang]
    action = html.escape(i18n.chemin(lang, "validate-form"))
    alerte = ""
    if erreurs:
        items = "".join(f"<li>{html.escape(erreur)}</li>" for erreur in erreurs)
        alerte = f'\n      <div class="callout" role="alert"><ul>{items}</ul></div>'
    champs = []
    for nom, libelle, exemple in t["champs"]:
        regles, aide = "required", ""
        if nom == "country":
            regles = 'minlength="2" maxlength="2" aria-describedby="country-aide"'
            pays = f'<span class="lat">{html.escape(PAYS_PAR_DEFAUT)}</span>'
            aide = f'<small id="country-aide">{_texte(lang, "aide_pays", pays=pays)}</small>'
        large = " f-wide" if nom == "street" else ""
        champs.append(
            f'<div class="field{large}"><label for="{nom}">{html.escape(libelle)}</label>'
            f'<input id="{nom}" name="{nom}" type="text" dir="ltr" '
            f'placeholder="{html.escape(exemple, quote=True)}" autocomplete="{AUTOCOMPLETE[nom]}" '
            f'{regles} value="{html.escape(valeurs.get(nom, ""), quote=True)}">{aide}</div>'
        )
    lignes = "\n      ".join(champs)
    return f"""<form class="form" method="post" action="{action}">
      <div class="form-head"><h2>{t["titre_form"]}</h2><span dir="ltr">POST {action}</span></div>{alerte}
      <p class="form-hint">{t["indice"]}</p>
      {lignes}
      <button class="btn btn-accent" type="submit">{t["bouton"]} {FLECHE}</button>
      <p class="form-note">{t["note_form"]}</p>
    </form>"""


def _adresse(standard: dict, saisie: dict[str, str] | None = None) -> list[str]:
    """Lignes de l'adresse standardisée, échappées.

    Avec la saisie, ce que la standardisation a changé est marqué (`<mark>`),
    élément par élément, comme sur la maquette.
    """

    def element(cle: str, origine: str) -> str:
        valeur = str(standard.get(cle) or "")
        if not valeur:
            return ""
        texte = html.escape(valeur)
        change = saisie is not None and valeur != saisie.get(origine, "")
        return f"<mark>{texte}</mark>" if change else texte

    lignes = [
        element("line1", "street"),
        element("line2", ""),
        " ".join(filter(None, (element("zip", "zip"), element("city", "city")))),
        element("state", "state"),
        element("country", "country"),
    ]
    return [ligne for ligne in lignes if ligne]


def _sur_arc(valeur: float, rayon: float) -> tuple[float, float]:
    """Point du demi-cercle de la jauge (0 à gauche, 1 à droite)."""
    angle = math.pi * (1 - valeur)
    return 70 + rayon * math.cos(angle), 72 - rayon * math.sin(angle)


def _jauge(valeur: float, inconnu: float, point: str) -> str:
    """La jauge de la maquette, remplie jusqu'à la vraie valeur ; trait blanc au repère « unknown »."""
    v = min(max(valeur, 0.0), 1.0)
    x, y = _sur_arc(v, 52)
    (x1, y1), (x2, y2) = _sur_arc(inconnu, 45), _sur_arc(inconnu, 59)
    return (
        '<svg class="gauge" viewBox="0 0 140 80" aria-hidden="true">'
        '<defs><linearGradient id="g-conf" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="#e5484d"/>'
        '<stop offset=".5" stop-color="#ffad3b"/><stop offset="1" stop-color="#18b47c"/></linearGradient></defs>'
        '<path d="M18 72A52 52 0 0 1 122 72" fill="none" stroke="#eaeef5" stroke-width="10" stroke-linecap="round"/>'
        f'<path class="g-val" d="M18 72A52 52 0 0 1 122 72" pathLength="1" stroke-dasharray="1" '
        f'stroke-dashoffset="{1 - v:.3f}" fill="none" stroke="url(#g-conf)" stroke-width="10" stroke-linecap="round"/>'
        f'<path d="M{x1:.2f} {y1:.2f} {x2:.2f} {y2:.2f}" stroke="#fff" stroke-width="2"/>'
        f'<circle class="g-end" cx="{x:.2f}" cy="{y:.2f}" r="7.5" fill="#fff" stroke="{point}" stroke-width="2.5"/>'
        "</svg>"
    )


def _horloge(secondes: int) -> str:
    """Le cadran du `429` : l'arc orange est la part de la fenêtre qui reste."""
    part = min(max(secondes / FENETRE_ESSAI_SECONDES, 0.0), 0.999)
    angle = 2 * math.pi * part
    x, y = 36 + 30 * math.sin(angle), 36 - 30 * math.cos(angle)
    return (
        '<svg viewBox="0 0 72 72" aria-hidden="true">'
        '<circle cx="36" cy="36" r="30" stroke="#eaeef5" stroke-width="6"/>'
        f'<path d="M36 6A30 30 0 {int(part > 0.5)} 1 {x:.2f} {y:.2f}" stroke="#e0951b" stroke-width="6"/>'
        '<path d="M36 22v14l8 6" stroke="#0b1b34" stroke-width="3" stroke-linejoin="round"/></svg>'
    )


def _barre(restants: int, plafond: int) -> str:
    """Un trait par essai de la journée : pleins ceux qui restent."""
    if plafond > 20:  # ponytail: au-delà la barre devient illisible ; la phrase dit le nombre exact
        return ""
    restants = min(max(restants, 0), plafond)
    pleins, utilises = "<b></b>" * restants, '<b class="u"></b>' * (plafond - restants)
    return f'<i aria-hidden="true">{pleins}{utilises}</i>'


def _ecran(lang: str, classe: str, statut: str, corps: str, pied: str) -> str:
    """Une carte « écran » de la maquette : barre de requête, corps, pied."""
    chemin = html.escape(i18n.chemin(lang, "validate-form"))
    alerte = " st-429" if statut == "429" else ""
    return (
        f'<div class="{f"screen {classe}".strip()}">\n'
        f'        <div class="s-top" dir="ltr" aria-hidden="true"><span class="dots"><i></i><i></i><i></i></span>'
        f'<span class="verb">POST</span><span class="path">{chemin}</span>'
        f'<span class="status{alerte}">{statut}</span></div>\n'
        f'        <div class="s-body">{corps}</div>\n'
        f'        <div class="s-foot"><span>{pied}</span></div>\n'
        "      </div>"
    )


def _pastille(lang: str, genre: str, cle: str) -> str:
    return (
        f'<p class="badge b-{genre}"><svg viewBox="0 0 16 16" aria-hidden="true">'
        f'<circle cx="8" cy="8" r="6.5"/>{PASTILLES[genre]}</svg>{TEXTES[lang][cle]}</p>'
    )


def _resultat(lang: str, adresse: dict[str, str], resultat: dict, restants: int, plafond: int) -> str:
    """Le vrai résultat de l'essai, dans l'écran de la maquette qui correspond à son état.

    `degraded` passe **avant** `validated` : une panne du fournisseur n'est pas un
    verdict, et ses valeurs (aujourd'hui `invalid` et une confiance basse, puisque
    rien n'a été vérifié) sont montrées barrées, dites « sans portée ».
    """
    t = TEXTES[lang]
    reglages = get_settings()
    standard = resultat.get("standardized") or {}
    confiance = float(resultat.get("confidence") or 0.0)
    livrabilite = html.escape(str(resultat.get("deliverability") or "unknown"))
    latitude, longitude = resultat.get("latitude"), resultat.get("longitude")
    coordonnees = ""
    if latitude is not None and longitude is not None:
        coordonnees = f"{float(latitude):.5f}, {float(longitude):.5f}"
    drapeaux = (
        f'<p class="flags"><code dir="ltr">validated: {str(bool(resultat.get("validated"))).lower()}</code>'
        f'<code dir="ltr">degraded: {str(bool(resultat.get("degraded"))).lower()}</code></p>'
    )
    geo = {"lat": latitude, "lng": longitude}
    reperes = [
        (score_deliverability({"valid": valide, "deliverability": dpv}, geo), couleur, point)
        for valide, dpv, couleur, point in REPERES
    ]
    _proche, couleur, point = min(reperes, key=lambda r: abs(r[0]["confidence"] - confiance))

    if resultat.get("degraded"):
        classe = ""
        une_ligne = html.escape(", ".join(_adresse(standard)) or "—")
        portee = _texte(lang, "sans_portee", adresse=f'<bdi class="lat" dir="ltr">{une_ligne}</bdi>')
        corps = f"""
          {_pastille(lang, "info", "degrade")}
          <p class="verdict">{t["degrade_verdict"]}</p>
          <div class="callout">{t["degrade_note"]}</div>
          <div class="muted-box">
            {drapeaux}
            <div class="facts2">
              <div><p class="k">{t["livrabilite"]}</p><p class="big">{livrabilite}</p></div>
              <div><p class="k">{t["confiance"]}</p><p class="big">{confiance:.2f}</p></div>
            </div>
            <p class="small" style="margin-top:10px">{portee}</p>
          </div>"""
    elif resultat.get("validated"):
        classe = "screen-ok"
        saisie = html.escape(", ".join(v for v in (adresse.get(nom, "") for nom, _l, _e in CHAMPS) if v))
        lignes = _adresse(standard, adresse)
        corrige = f'<p class="fixnote">{t["corrige"]}</p>' if any("<mark>" in ligne for ligne in lignes) else ""
        carte = ""
        if coordonnees:
            carte = f"""
            <div class="ok-cell ok-map">
              {CARTE}
              <div class="map-meta">
                <p><span><span class="sr">{t["coordonnees_sr"]}</span><span dir="ltr">{coordonnees}</span></span><span class="tag">GPS</span></p>
                <p class="small">{t["geocodage"]}</p>
              </div>
            </div>"""
        echelle = []
        for score, teinte, _point in reperes:
            courant = score["deliverability"] == resultat.get("deliverability")
            attribut = ' class="cur"' if courant else ""
            suffixe = t["cette_adresse"] if courant else ""
            echelle.append(
                f'<li{attribut} style="--c:{teinte}"><b>{score["confidence"]:.2f}</b>'
                f'<span><span class="mono">{score["deliverability"]}</span>{suffixe}</span></li>'
            )
        inconnu = reperes[1][0]["confidence"]
        grille = "ok-grid" if coordonnees else "ok-grid no-map"
        corps = f"""
          <div class="ok-head">
            {_pastille(lang, "ok", "ok")}
            <p class="verdict-t">{t["ok_verdict"]}</p>
            {drapeaux}
          </div>
          <div class="{grille}">
            <div class="ok-cell ok-addr">
              <p class="k">{t["standardisee"]}</p>
              <p class="saisie"><span>{t["saisie"]}</span><bdi dir="ltr">{saisie}</bdi></p>
              <svg class="down" viewBox="0 0 12 16" aria-hidden="true"><path d="M6 1v13M1.5 9.5 6 14l4.5-4.5"/></svg>
              <p class="std" dir="ltr">{"<br>".join(lignes) or "—"}</p>
              {corrige}
            </div>{carte}
            <div class="ok-cell ok-deliv">
              <div>
                <p class="k">{t["livrabilite"]}</p>
                <p class="chip">{livrabilite}</p>
                <div class="gauge-wrap">
                  {_jauge(confiance, inconnu, point)}
                  <p class="g-num">{confiance:.2f}</p>
                </div>
                <p class="g-cap">{t["jauge"]}</p>
              </div>
              <ul class="scale" aria-label="{html.escape(t["reperes"], quote=True)}">{"".join(echelle)}</ul>
            </div>
          </div>"""
    else:
        classe = ""
        position = ""
        if coordonnees:
            position = (
                f'<div class="f-wide" style="grid-column:1/-1"><p class="k">{t["coordonnees"]}</p>'
                f'<p class="small"><span class="mono" dir="ltr">{coordonnees}</span>{t["geocodage_suite"]}</p></div>'
            )
        corps = f"""
          {_pastille(lang, "ko", "ko")}
          <p class="verdict">{t["ko_verdict"]}</p>
          {drapeaux}
          <div>
            <p class="k">{t["standardisee"]}</p>
            <p class="std" dir="ltr">{"<br>".join(_adresse(standard)) or "—"}</p>
          </div>
          <div class="facts2">
            <div><p class="k">{t["livrabilite"]}</p><p class="big ko">{livrabilite}</p></div>
            <div><p class="k">{t["confiance"]}</p><p class="big">{confiance:.2f}</p><span class="track"><i style="--v:{min(max(confiance, 0.0), 1.0) * 100:.0f}%;--c:{couleur}"></i></span></div>
            {position}
          </div>"""

    valeurs = {
        "signup": html.escape(i18n.chemin(lang, "signup")),
        "jours": i18n.nombre(lang, accounts.JOURS_ESSAI),
        "validations": i18n.nombre(lang, accounts.VALIDATIONS_ESSAI),
        "heures": i18n.nombre(lang, FENETRE_ESSAI_SECONDES // 3600),
        "prix": f'<span class="lat" dir="ltr">{i18n.nombre(lang, reglages.x402_price)}&nbsp;USDC</span>',
    }
    if restants > 0:
        phrase, suite = _texte(lang, "reste", restants), _texte(lang, "suite", **valeurs)
    else:
        phrase, suite = _texte(lang, "dernier"), _texte(lang, "dernier_suite", **valeurs)
    corps += f"""
          <div class="ok-foot">
            <p class="tries">{_barre(restants, plafond)}{phrase}</p>
            <p>{suite}</p>
          </div>
        """
    return f"""<section class="examples" aria-labelledby="h-res">
  <div class="wrap">
    <div class="divider"><h2 id="h-res">{t["resultat"]}</h2></div>
    <article class="ex reveal">
      {_ecran(lang, classe, "200 OK", corps, t["pied"])}
    </article>
  </div>
</section>"""


def _plafond_atteint(lang: str, plafond: int, secondes: int) -> str:
    """`429` : l'écran de la maquette à la place du formulaire, avec l'attente exacte."""
    t = TEXTES[lang]
    reglages = get_settings()
    minutes = max(1, (secondes + 59) // 60)
    heures = i18n.nombre(lang, FENETRE_ESSAI_SECONDES // 3600)
    rappel = _texte(
        lang,
        "rappel",
        minutes=_texte(lang, "minutes", minutes),
        heures=heures,
        jours=i18n.nombre(lang, accounts.JOURS_ESSAI),
        validations=i18n.nombre(lang, accounts.VALIDATIONS_ESSAI),
    )
    corps = f"""
          <div class="wait">
            {_horloge(secondes)}
            <strong>{_texte(lang, "attente", minutes)}</strong>
            <span>{_texte(lang, "fenetre", heures=heures)}</span>
            <code dir="ltr">Retry-After: {secondes}</code>
          </div>
          <div class="p429">
            <h2>{t["plafond_titre"]}</h2>
            <p>{_texte(lang, "plafond_texte", plafond)}</p>
            <div class="callout">{rappel}</div>
            <div class="cta">
              <a class="btn btn-accent" href="{html.escape(i18n.chemin(lang, "signup"))}">{t["creer"]} {FLECHE}</a>
              <a class="btn btn-glass" href="{html.escape(i18n.chemin(lang, "pricing"))}">{t["tarifs"]}</a>
            </div>
            <p class="fine">{_texte(lang, "legitime", contact=html.escape(reglages.contact_email, quote=True))}</p>
          </div>
        """
    return _ecran(lang, "screen-429", "429", corps, t["pied_plafond"])


def _page(
    lang: str,
    essai: str,
    *,
    resultat: str = "",
    statut: int = 200,
    public: bool = False,
    retry_after: int | None = None,
) -> HTMLResponse:
    """La page d'essai dans une langue : deux régimes d'en-têtes.

    Le formulaire vide ne contient aucune donnée client : il est indexable et
    cachable quelques minutes. Dès qu'un `POST` a eu lieu, la réponse contient une
    adresse — elle porte `PRIVATE_HEADERS` (`no-store`, `noindex`), parce qu'un
    proxy qui garderait cette page garderait l'adresse du visiteur.

    `i18n.rendre` remplit ce qui vient du code (liens, plafond, contact, prix).
    Le formulaire et le résultat, qui portent la saisie du visiteur, sont du HTML
    construit ici, saisie échappée : `rendre` l'échapperait une seconde fois. Ils
    sont posés à leur repère en un seul passage, une saisie n'est jamais relue.
    """
    reglages = get_settings()
    texte = i18n.rendre(
        GABARITS / f"{lang}.html",
        {
            **i18n.liens(lang, "validate-form"),
            "robots": "index, follow" if public else "noindex, nofollow",
            "essais": _texte(lang, "essais", reglages.validate_form_rate_limit_per_day),
            "contact": reglages.contact_email,
            "prix_x402": i18n.nombre(lang, reglages.x402_price),
            "essai": _BLOC.format("essai"),
            "resultat": _BLOC.format("resultat"),
        },
    )
    blocs = {"essai": essai, "resultat": resultat}
    texte = _BLOCS.sub(lambda trouve: blocs[trouve.group(1)], texte)
    if public:
        # La page vide est indexable (D65) ; une réponse à un essai, privée, ne l'est pas.
        texte = seo.enrichir(texte, lang, "validate-form", reglages)
    entetes = public_headers(form=True, max_age=300) if public else dict(PRIVATE_HEADERS)
    if retry_after is not None:
        # Le client sait quand réessayer sans le deviner.
        entetes["Retry-After"] = str(retry_after)
    return HTMLResponse(content=texte, status_code=statut, headers=entetes)


def _formulaire_vide(lang: str) -> HTMLResponse:
    """Le formulaire public : indexable et cachable, sans aucune donnée client."""
    return _page(lang, _formulaire(lang, {}, []), public=True)


async def _essai(request: Request, lang: str) -> HTMLResponse:
    """Un essai gratuit : plafonné par IP (toutes langues confondues), sans écriture."""
    reglages = get_settings()
    plafond = reglages.validate_form_rate_limit_per_day
    cle = f"validate-form:{client_ip(request)}"
    metrics.incr("validate_form_requests")

    autorise, restants = ratelimit.allow(cle, plafond, FENETRE_ESSAI_SECONDES)
    if not autorise:
        # Un refus n'est pas compté dans la fenêtre par `ratelimit.allow` : le
        # visiteur bloqué ne repousse donc pas lui-même la fin de sa fenêtre.
        metrics.incr("validate_form_denied")
        secondes = ratelimit.retry_after(cle, FENETRE_ESSAI_SECONDES)
        return _page(lang, _plafond_atteint(lang, plafond, secondes), statut=429, retry_after=secondes)

    donnees = await request.form()
    ligne = str(donnees.get("address", "") or "").strip()[:LIGNE_MAX]
    if ligne:
        # D86 : l'adresse tapée sur une ligne (champ unique de la page d'accueil).
        adresse = decouper(ligne)
    else:
        adresse = {
            nom: str(donnees.get(nom, "") or "").strip() for nom, _libelle, _ex in CHAMPS
        }
        adresse["country"] = adresse["country"].upper() or PAYS_PAR_DEFAUT
        erreurs = _erreurs(adresse, lang)
        if erreurs:
            return _page(lang, _formulaire(lang, adresse, erreurs), statut=400)

    # Même pipeline que la route payante (`perform_validation`, D5/D34) : une
    # seule validation, un seul cache, donc aucun écart possible entre ce que
    # voit le visiteur ici et ce que rend l'API vendue. L'essai ne touche ni le
    # registre des paiements (D29) ni la base des comptes : il n'y a rien à
    # débiter, et rien à inscrire (D40).
    resultat, _cle_cache = perform_validation(adresse)
    metrics.incr("validate_form_validations")
    return _page(
        lang,
        _formulaire(lang, adresse, []),
        resultat=_resultat(lang, adresse, resultat, restants, plafond),
    )


@router.get("/validate-form")
async def validate_form_page() -> Response:
    """Le formulaire public : indexable et cachable, sans aucune donnée client."""
    return _formulaire_vide(i18n.DEFAUT)


@router.post("/validate-form")
async def validate_form_submit(request: Request) -> Response:
    """Un essai gratuit : plafonné, sans aucune écriture, pipeline partagé."""
    return await _essai(request, i18n.DEFAUT)


def _routes(lang: str):
    async def page() -> Response:
        return _formulaire_vide(lang)

    async def soumettre(request: Request) -> Response:
        return await _essai(request, lang)

    return page, soumettre


# Chemins **littéraux** (`/en/validate-form`…), jamais `/{lang}/…` : un paramètre de
# chemin avalerait d'autres routes. Un `POST` répond dans la langue de son adresse.
for _lang in i18n.PREFIXEES:
    _lire_page, _soumettre = _routes(_lang)
    router.add_api_route(i18n.chemin(_lang, "validate-form"), _lire_page, methods=["GET"], include_in_schema=False)
    router.add_api_route(i18n.chemin(_lang, "validate-form"), _soumettre, methods=["POST"], include_in_schema=False)
