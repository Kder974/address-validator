"""Le tableau de bord de l'espace client (D64), façon page d'accueil de Shopify.

En haut, les chiffres du mois en une ligne ; puis un message d'accueil, **la
prochaine étape** que le code déduit de l'état réel du compte, des raccourcis,
trois cartes (la courbe du mois jour par jour, les colis évités, un conseil du
jour), six conseils d'utilisation, et enfin la clé et l'offre.

Rien n'est décoratif : chaque chiffre vient de `customer_validations`
(`accounts.daily_usage`, `accounts.plan_status`), de la configuration ou des
tables de prix (`i18n`). La « prochaine étape » suit cet ordre, le premier qui
s'applique gagne :

1. essai terminé (`403` côté API) ;
2. quota du mois atteint (`402`) ;
3. plan payant : le bandeau du plan, et l'épuisement prévu s'il tombe avant la
   remise à zéro ;
4. aucune validation encore : faire le premier appel ;
5. essai en cours : sa fin, ce qui reste, et l'épuisement prévu au rythme du mois
   s'il tombe avant la fin de l'essai ou du mois.

La prévision est une règle de trois sur le mois en cours (validations ÷ jours
écoulés), dite comme telle : « au rythme actuel ».
"""

from __future__ import annotations

import html
import math
from datetime import datetime, timedelta
from functools import partial
from urllib.parse import quote, urlencode

from fastapi.responses import HTMLResponse

from app import accounts, i18n
from app.config import get_settings
from app.routes import signup as s

FLECHE = '<svg class="arr" viewBox="0 0 10 10" aria-hidden="true"><path class="l" d="M1 5h7"/><path class="h" d="M3.5 1.5 7 5 3.5 8.5"/></svg>'

TEXTES = {
    "fr": {
        "bonjour": "Bonjour, {boutique} !",
        "titre_inv": ("{n} adresse fausse arrêtée ce mois-ci.", "{n} adresses fausses arrêtées ce mois-ci."),
        "sous_inv": ("Sans vérification, ce colis serait parti vers une adresse incorrecte.",
                     "Sans vérification, ces {n} colis seraient partis vers une adresse incorrecte."),
        "titre_ok": ("{n} adresse vérifiée ce mois-ci, reconnue.", "{n} adresses vérifiées ce mois-ci, toutes reconnues."),
        "sous_ok": "Aucune adresse fausse n'est passée ce mois-ci : vos colis partent à la bonne adresse.",
        "titre_vide": "Votre espace est prêt.",
        "sous_vide": "Faites votre premier appel : vos validations s'afficheront ici, jour par jour.",
        "prochaine": "Prochaine étape",
        "premier": "Envoyez votre première adresse à POST /validate, avec votre clé dans l'en-tête X-API-Key. Le guide montre l'appel complet.",
        "rythme_essai": "Votre essai se termine le {fin} et il vous reste {restant} validations. Au rythme actuel, elles seront épuisées vers le {date}.",
        "essai": "Votre essai se termine le {fin}. Il vous reste {restant} validations sur {quota}.",
        "rythme_payant": "Au rythme actuel, le quota du mois sera épuisé vers le {date}, avant sa remise à zéro le {remise}.",
        "passer": "Passer à Starter, {prix} par mois",
        "passer_pro": "Passer à Pro, {prix} par mois", "gerer": "Gérer mon abonnement (Stripe)",
        "comparer": "Comparer les offres",
        "guide": "Ouvrir le guide",
        "changer": "Demander un changement de plan",
        "sujet_plan": "Changement de plan",
        "kpi_quand": "{periode} (UTC) · votre clé API",
        "kpi_essai": "Essai", "kpi_plan": "Plan", "jours": ("{n} jour", "{n} jours"), "termine": "terminé",
        "courbe_titre": ("{n} validation depuis le {debut}", "{n} validations depuis le {debut}"),
        "courbe_txt": "En bleu, vos validations ; en rouge, les adresses invalides. Le pointillé montre la suite au même rythme.",
        "courbe_quota": " Le quota de {quota} serait atteint vers le {date}.",
        "ligne_quota": "ligne du haut : quota de {quota}",
        "eco_zero": "Aucune adresse fausse ce mois-ci",
        "eco_titre": ("{n} colis qui ne reviendra pas", "{n} colis qui ne reviendront pas"),
        "eco_suite": "C'est une estimation, pas une mesure : le bas de la fourchette du secteur pour un colis raté.",
        "depuis": "Depuis la création du compte : {v} validations au total, dont {i} invalides.",
        "conseil": "Conseil du jour", "voir_guide": "Voir dans le guide",
        "conseils": (
            ('"degraded": true', "Ce champ veut dire « réessayez plus tard »",
             "Quand degraded vaut true, notre fournisseur n'a pas répondu. Ce n'est pas une adresse fausse : ne bloquez pas la commande, revalidez-la un peu plus tard."),
            ("X-API-Key", "Votre clé va dans l'en-tête, jamais dans l'adresse de l'appel",
             "Envoyez-la dans l'en-tête X-API-Key de chaque appel. Une clé écrite dans une URL finit dans des journaux."),
            ('"country": "RE"', "Le pays s'écrit en deux lettres",
             "FR pour la France, RE pour La Réunion, US pour les États-Unis. Un pays mal écrit fait échouer la vérification."),
            ("10 / s", "Pas plus de 10 appels par seconde",
             "Au-delà, l'API répond 429. Pour un gros fichier, envoyez les adresses une par une, à votre rythme."),
        ),
        "offre_jusquau": "jusqu'au {date}", "offre_fini": "terminé le {date}", "offre_mois": "{quota} validations par mois",
        "offre_essai": "{quota} validations, sans carte bancaire. Ensuite : Starter, {prix_starter} par mois pour {quota_starter} validations, ou Pro, {prix_pro} par mois pour {quota_pro}.",
        "offre_payant": "Votre quota se remet à zéro le {remise} (le 1er de chaque mois, en heure UTC).",
    },
    "en": {
        "bonjour": "Hello, {boutique}!",
        "titre_inv": ("{n} wrong address stopped this month.", "{n} wrong addresses stopped this month."),
        "sous_inv": ("Without the check, this parcel would have left for a wrong address.",
                     "Without the check, these {n} parcels would have left for a wrong address."),
        "titre_ok": ("{n} address checked this month, recognised.", "{n} addresses checked this month, all recognised."),
        "sous_ok": "No wrong address got through this month: your parcels leave for the right address.",
        "titre_vide": "Your space is ready.",
        "sous_vide": "Make your first call: your validations will show here, day by day.",
        "prochaine": "Next step",
        "premier": "Send your first address to POST /validate, with your key in the X-API-Key header. The guide shows the full call.",
        "rythme_essai": "Your trial ends on {fin} and you have {restant} validations left. At the current pace, they will run out around {date}.",
        "essai": "Your trial ends on {fin}. You have {restant} of {quota} validations left.",
        "rythme_payant": "At the current pace, this month's quota will run out around {date}, before it resets on {remise}.",
        "passer": "Switch to Starter, {prix} per month",
        "passer_pro": "Switch to Pro, {prix} per month", "gerer": "Manage my subscription (Stripe)",
        "comparer": "Compare the plans",
        "guide": "Open the guide",
        "changer": "Ask for a plan change",
        "sujet_plan": "Plan change",
        "kpi_quand": "{periode} (UTC) · your API key",
        "kpi_essai": "Trial", "kpi_plan": "Plan", "jours": ("{n} day", "{n} days"), "termine": "ended",
        "courbe_titre": ("{n} validation since {debut}", "{n} validations since {debut}"),
        "courbe_txt": "In blue, your validations; in red, the invalid addresses. The dotted line shows what follows at the same pace.",
        "courbe_quota": " The {quota} quota would be reached around {date}.",
        "ligne_quota": "top line: {quota} quota",
        "eco_zero": "No wrong address this month",
        "eco_titre": ("{n} parcel that will not come back", "{n} parcels that will not come back"),
        "eco_suite": "This is an estimate, not a measurement: the low end of the industry range for a failed parcel.",
        "depuis": "Since the account was created: {v} validations in total, {i} of them invalid.",
        "conseil": "Tip of the day", "voir_guide": "See it in the guide",
        "conseils": (
            ('"degraded": true', "This field means “try again later”",
             "When degraded is true, our provider did not answer. It is not a wrong address: do not block the order, validate it again a little later."),
            ("X-API-Key", "Your key goes in the header, never in the call address",
             "Send it in the X-API-Key header of every call. A key written in a URL ends up in logs."),
            ('"country": "RE"', "The country is written in two letters",
             "FR for France, RE for Réunion, US for the United States. A misspelled country makes the check fail."),
            ("10 / s", "No more than 10 calls per second",
             "Beyond that, the API answers 429. For a large file, send the addresses one by one, at your own pace."),
        ),
        "offre_jusquau": "until {date}", "offre_fini": "ended on {date}", "offre_mois": "{quota} validations per month",
        "offre_essai": "{quota} validations, no credit card. Then: Starter, {prix_starter} per month for {quota_starter} validations, or Pro, {prix_pro} per month for {quota_pro}.",
        "offre_payant": "Your quota resets on {remise} (the 1st of each month, UTC time).",
    },
    "zh": {
        "bonjour": "{boutique}，您好！",
        "titre_inv": ("本月拦截了 {n} 个错误地址。", "本月拦截了 {n} 个错误地址。"),
        "sous_inv": ("如果没有验证，这个包裹就会寄往错误的地址。", "如果没有验证，这 {n} 个包裹就会寄往错误的地址。"),
        "titre_ok": ("本月已验证 {n} 个地址，全部通过。", "本月已验证 {n} 个地址，全部通过。"),
        "sous_ok": "本月没有错误地址漏过：您的包裹都寄往正确的地址。",
        "titre_vide": "您的客户空间已就绪。",
        "sous_vide": "发起第一次调用吧：您的验证记录会按天显示在这里。",
        "prochaine": "下一步",
        "premier": "把您的第一个地址发送到 POST /validate，并把密钥放在 X-API-Key 请求头中。指南里有完整的调用示例。",
        "rythme_essai": "您的试用期将于 {fin} 结束，还剩 {restant} 次验证。按目前的速度，大约会在 {date} 用完。",
        "essai": "您的试用期将于 {fin} 结束。{quota} 次验证中还剩 {restant} 次。",
        "rythme_payant": "按目前的速度，本月配额大约会在 {date} 用完，早于 {remise} 的重置。",
        "passer": "升级到 Starter，每月 {prix}",
        "passer_pro": "升级到 Pro，每月 {prix}", "gerer": "管理我的订阅（Stripe）",
        "comparer": "比较套餐",
        "guide": "打开指南",
        "changer": "申请更换套餐",
        "sujet_plan": "更换套餐",
        "kpi_quand": "{periode}（UTC）· 您的 API 密钥",
        "kpi_essai": "试用", "kpi_plan": "套餐", "jours": ("{n} 天", "{n} 天"), "termine": "已结束",
        "courbe_titre": ("自 {debut} 起 {n} 次验证", "自 {debut} 起 {n} 次验证"),
        "courbe_txt": "蓝线是您的验证次数，红线是无效地址。虚线表示按同样速度的后续走势。",
        "courbe_quota": "大约会在 {date} 达到 {quota} 次的配额。",
        "ligne_quota": "顶线：配额 {quota} 次",
        "eco_zero": "本月没有错误地址",
        "eco_titre": ("{n} 个不会被退回的包裹", "{n} 个不会被退回的包裹"),
        "eco_suite": "这是估算，不是实测：取行业中一个失败包裹成本区间的下限。",
        "depuis": "自开户以来：共 {v} 次验证，其中 {i} 次无效。",
        "conseil": "今日提示", "voir_guide": "在指南中查看",
        "conseils": (
            ('"degraded": true', "这个字段的意思是“稍后重试”",
             "当 degraded 为 true 时，表示我们的数据提供方没有响应。这不是错误地址：不要拦截订单，稍后再验证一次。"),
            ("X-API-Key", "密钥放在请求头里，绝不放在调用地址中",
             "每次调用都通过 X-API-Key 请求头发送密钥。写在 URL 里的密钥最终会出现在日志中。"),
            ('"country": "RE"', "国家用两个字母表示",
             "FR 代表法国，RE 代表留尼汪，US 代表美国。国家写错会导致验证失败。"),
            ("10 / s", "每秒不超过 10 次调用",
             "超过后 API 会返回 429。处理大文件时，请按自己的节奏逐个发送地址。"),
        ),
        "offre_jusquau": "至 {date}", "offre_fini": "已于 {date} 结束", "offre_mois": "每月 {quota} 次验证",
        "offre_essai": "{quota} 次验证，无需银行卡。之后可选 Starter（每月 {prix_starter}，{quota_starter} 次验证）或 Pro（每月 {prix_pro}，{quota_pro} 次）。",
        "offre_payant": "您的配额将于 {remise} 重置（每月 1 日，UTC 时间）。",
    },
    "hi": {
        "bonjour": "नमस्ते, {boutique}!",
        "titre_inv": ("इस महीने {n} गलत पता रोका गया।", "इस महीने {n} गलत पते रोके गए।"),
        "sous_inv": ("जाँच के बिना यह पार्सल गलत पते पर चला जाता।", "जाँच के बिना ये {n} पार्सल गलत पते पर चले जाते।"),
        "titre_ok": ("इस महीने {n} पते की जाँच हुई, वह सही निकला।", "इस महीने {n} पतों की जाँच हुई, सभी सही निकले।"),
        "sous_ok": "इस महीने कोई गलत पता नहीं निकला: आपके पार्सल सही पते पर जा रहे हैं।",
        "titre_vide": "आपका क्षेत्र तैयार है।",
        "sous_vide": "पहली कॉल करें: आपके वैलिडेशन यहाँ दिन-ब-दिन दिखेंगे।",
        "prochaine": "अगला कदम",
        "premier": "अपना पहला पता POST /validate पर भेजें, X-API-Key हेडर में अपनी कुंजी के साथ। गाइड में पूरी कॉल दिखाई गई है।",
        "rythme_essai": "आपका ट्रायल {fin} को खत्म होगा और आपके पास {restant} वैलिडेशन बचे हैं। अभी की रफ़्तार से वे लगभग {date} तक खत्म हो जाएँगे।",
        "essai": "आपका ट्रायल {fin} को खत्म होगा। {quota} में से {restant} वैलिडेशन बचे हैं।",
        "rythme_payant": "अभी की रफ़्तार से इस महीने का कोटा लगभग {date} तक खत्म हो जाएगा, {remise} को रीसेट होने से पहले।",
        "passer": "Starter पर जाएँ, {prix} प्रति माह",
        "passer_pro": "Pro पर जाएँ, {prix} प्रति माह", "gerer": "मेरी सदस्यता प्रबंधित करें (Stripe)",
        "comparer": "प्लान की तुलना करें",
        "guide": "गाइड खोलें",
        "changer": "प्लान बदलने का अनुरोध करें",
        "sujet_plan": "प्लान बदलना",
        "kpi_quand": "{periode} (UTC) · आपकी API कुंजी",
        "kpi_essai": "ट्रायल", "kpi_plan": "प्लान", "jours": ("{n} दिन", "{n} दिन"), "termine": "खत्म",
        "courbe_titre": ("{debut} से {n} वैलिडेशन", "{debut} से {n} वैलिडेशन"),
        "courbe_txt": "नीली रेखा आपके वैलिडेशन हैं, लाल रेखा अमान्य पते। बिंदुदार रेखा उसी रफ़्तार से आगे का अनुमान दिखाती है।",
        "courbe_quota": " {quota} का कोटा लगभग {date} तक पूरा हो जाएगा।",
        "ligne_quota": "ऊपर की रेखा: {quota} का कोटा",
        "eco_zero": "इस महीने कोई गलत पता नहीं",
        "eco_titre": ("{n} पार्सल जो वापस नहीं आएगा", "{n} पार्सल जो वापस नहीं आएँगे"),
        "eco_suite": "यह अनुमान है, माप नहीं: असफल पार्सल की उद्योग-सीमा का निचला सिरा।",
        "depuis": "खाता बनने से अब तक: कुल {v} वैलिडेशन, जिनमें {i} अमान्य।",
        "conseil": "आज का सुझाव", "voir_guide": "गाइड में देखें",
        "conseils": (
            ('"degraded": true', "इस फ़ील्ड का मतलब है “बाद में फिर कोशिश करें”",
             "जब degraded का मान true हो, तो हमारे सेवा-प्रदाता ने जवाब नहीं दिया। यह गलत पता नहीं है: ऑर्डर न रोकें, थोड़ी देर बाद फिर जाँचें।"),
            ("X-API-Key", "आपकी कुंजी हेडर में जाती है, कॉल के पते में कभी नहीं",
             "हर कॉल में उसे X-API-Key हेडर में भेजें। URL में लिखी कुंजी लॉग में पहुँच जाती है।"),
            ('"country": "RE"', "देश दो अक्षरों में लिखा जाता है",
             "फ़्रांस के लिए FR, रेयूनियों के लिए RE, अमेरिका के लिए US। गलत लिखा देश जाँच को विफल कर देता है।"),
            ("10 / s", "प्रति सेकंड 10 से ज़्यादा कॉल नहीं",
             "इससे ज़्यादा पर API 429 देता है। बड़ी फ़ाइल के लिए पते एक-एक करके, अपनी रफ़्तार से भेजें।"),
        ),
        "offre_jusquau": "{date} तक", "offre_fini": "{date} को खत्म", "offre_mois": "प्रति माह {quota} वैलिडेशन",
        "offre_essai": "{quota} वैलिडेशन, बैंक कार्ड नहीं चाहिए। उसके बाद: Starter, {prix_starter} प्रति माह में {quota_starter} वैलिडेशन, या Pro, {prix_pro} प्रति माह में {quota_pro}।",
        "offre_payant": "आपका कोटा {remise} को रीसेट होगा (हर महीने की 1 तारीख, UTC समय)।",
    },
    "es": {
        "bonjour": "¡Hola, {boutique}!",
        "titre_inv": ("{n} dirección errónea detenida este mes.", "{n} direcciones erróneas detenidas este mes."),
        "sous_inv": ("Sin la verificación, este paquete habría salido hacia una dirección incorrecta.",
                     "Sin la verificación, estos {n} paquetes habrían salido hacia una dirección incorrecta."),
        "titre_ok": ("{n} dirección verificada este mes, reconocida.", "{n} direcciones verificadas este mes, todas reconocidas."),
        "sous_ok": "Ninguna dirección errónea ha pasado este mes: tus paquetes salen hacia la dirección correcta.",
        "titre_vide": "Tu área está lista.",
        "sous_vide": "Haz tu primera llamada: tus validaciones aparecerán aquí, día a día.",
        "prochaine": "Siguiente paso",
        "premier": "Envía tu primera dirección a POST /validate, con tu clave en la cabecera X-API-Key. La guía muestra la llamada completa.",
        "rythme_essai": "Tu prueba termina el {fin} y te quedan {restant} validaciones. Al ritmo actual, se agotarán hacia el {date}.",
        "essai": "Tu prueba termina el {fin}. Te quedan {restant} de {quota} validaciones.",
        "rythme_payant": "Al ritmo actual, la cuota del mes se agotará hacia el {date}, antes de reiniciarse el {remise}.",
        "passer": "Pasar a Starter, {prix} al mes",
        "passer_pro": "Pasar a Pro, {prix} al mes", "gerer": "Gestionar mi suscripción (Stripe)",
        "comparer": "Comparar planes",
        "guide": "Abrir la guía",
        "changer": "Pedir un cambio de plan",
        "sujet_plan": "Cambio de plan",
        "kpi_quand": "{periode} (UTC) · tu clave de API",
        "kpi_essai": "Prueba", "kpi_plan": "Plan", "jours": ("{n} día", "{n} días"), "termine": "terminada",
        "courbe_titre": ("{n} validación desde el {debut}", "{n} validaciones desde el {debut}"),
        "courbe_txt": "En azul, tus validaciones; en rojo, las direcciones no válidas. La línea de puntos muestra lo que sigue al mismo ritmo.",
        "courbe_quota": " La cuota de {quota} se alcanzaría hacia el {date}.",
        "ligne_quota": "línea superior: cuota de {quota}",
        "eco_zero": "Ninguna dirección errónea este mes",
        "eco_titre": ("{n} paquete que no volverá", "{n} paquetes que no volverán"),
        "eco_suite": "Es una estimación, no una medición: el extremo bajo de la horquilla del sector para un paquete fallido.",
        "depuis": "Desde que se creó la cuenta: {v} validaciones en total, {i} de ellas no válidas.",
        "conseil": "Consejo del día", "voir_guide": "Verlo en la guía",
        "conseils": (
            ('"degraded": true', "Este campo significa «vuelve a intentarlo más tarde»",
             "Cuando degraded vale true, nuestro proveedor no ha respondido. No es una dirección errónea: no bloquees el pedido, vuelve a validarlo un poco más tarde."),
            ("X-API-Key", "Tu clave va en la cabecera, nunca en la dirección de la llamada",
             "Envíala en la cabecera X-API-Key de cada llamada. Una clave escrita en una URL acaba en los registros."),
            ('"country": "RE"', "El país se escribe con dos letras",
             "FR para Francia, RE para La Reunión, US para Estados Unidos. Un país mal escrito hace fallar la verificación."),
            ("10 / s", "No más de 10 llamadas por segundo",
             "Por encima, la API responde 429. Para un archivo grande, envía las direcciones una a una, a tu ritmo."),
        ),
        "offre_jusquau": "hasta el {date}", "offre_fini": "terminada el {date}", "offre_mois": "{quota} validaciones al mes",
        "offre_essai": "{quota} validaciones, sin tarjeta bancaria. Después: Starter, {prix_starter} al mes por {quota_starter} validaciones, o Pro, {prix_pro} al mes por {quota_pro}.",
        "offre_payant": "Tu cuota se reinicia el {remise} (el día 1 de cada mes, en hora UTC).",
    },
    "ar": {
        "bonjour": "مرحبًا، ⁨{boutique}⁩!",
        "titre_inv": ("أُوقف هذا الشهر عنوان خاطئ واحد.", "عناوين خاطئة أُوقفت هذا الشهر: {n}."),
        "sous_inv": ("لولا التحقق، لذهب هذا الطرد إلى عنوان غير صحيح.", "لولا التحقق، لذهبت هذه الطرود ({n}) إلى عناوين غير صحيحة."),
        "titre_ok": ("تحقّقنا هذا الشهر من عنوان واحد، وهو صحيح.", "عناوين تحقّقنا منها هذا الشهر، وكلها صحيحة: {n}."),
        "sous_ok": "لم يمرّ أي عنوان خاطئ هذا الشهر: طرودك تذهب إلى العنوان الصحيح.",
        "titre_vide": "مساحتك جاهزة.",
        "sous_vide": "أجرِ أول استدعاء: ستظهر عمليات التحقق هنا، يومًا بيوم.",
        "prochaine": "الخطوة التالية",
        "premier": "أرسل أول عنوان إلى POST /validate، مع مفتاحك في الترويسة X-API-Key. يعرض الدليل الاستدعاء كاملًا.",
        "rythme_essai": "تنتهي تجربتك في {fin} وتبقى لك عمليات تحقق عددها {restant}. بالوتيرة الحالية، ستنفد نحو {date}.",
        "essai": "تنتهي تجربتك في {fin}. يبقى لك {restant} من أصل {quota} عملية تحقق.",
        "rythme_payant": "بالوتيرة الحالية، ستنفد حصة الشهر نحو {date}، قبل إعادة ضبطها في {remise}.",
        "passer": "الانتقال إلى Starter، {prix} شهريًا",
        "passer_pro": "الانتقال إلى Pro، {prix} شهريًا", "gerer": "إدارة اشتراكي (Stripe)",
        "comparer": "قارن الباقات",
        "guide": "افتح الدليل",
        "changer": "اطلب تغيير الباقة",
        "sujet_plan": "تغيير الباقة",
        "kpi_quand": "{periode} (UTC) · مفتاح API الخاص بك",
        "kpi_essai": "التجربة", "kpi_plan": "الباقة", "jours": ("يوم واحد", "الأيام: {n}"), "termine": "انتهت",
        "courbe_titre": ("عملية تحقق واحدة منذ {debut}", "عمليات التحقق منذ {debut}: {n}"),
        "courbe_txt": "بالأزرق عمليات التحقق، وبالأحمر العناوين غير الصالحة. يُظهر الخط المنقّط ما يلي بالوتيرة نفسها.",
        "courbe_quota": " ستُبلغ حصة {quota} نحو {date}.",
        "ligne_quota": "الخط العلوي: حصة {quota}",
        "eco_zero": "لا عناوين خاطئة هذا الشهر",
        "eco_titre": ("طرد واحد لن يعود", "طرود لن تعود: {n}"),
        "eco_suite": "هذا تقدير وليس قياسًا: الحد الأدنى من نطاق القطاع لكلفة طرد فاشل.",
        "depuis": "منذ إنشاء الحساب: {v} عملية تحقق إجمالًا، منها {i} غير صالحة.",
        "conseil": "نصيحة اليوم", "voir_guide": "اطّلع عليها في الدليل",
        "conseils": (
            ('"degraded": true', "هذا الحقل يعني «أعد المحاولة لاحقًا»",
             "عندما تكون قيمة degraded هي true، فهذا يعني أن مزوّدنا لم يُجب. ليس عنوانًا خاطئًا: لا توقف الطلب، وأعد التحقق منه بعد قليل."),
            ("X-API-Key", "مفتاحك يوضع في الترويسة، لا في عنوان الاستدعاء أبدًا",
             "أرسله في الترويسة X-API-Key مع كل استدعاء. المفتاح المكتوب في عنوان URL ينتهي في السجلات."),
            ('"country": "RE"', "يُكتب البلد بحرفين",
             "FR لفرنسا، وRE لريونيون، وUS للولايات المتحدة. البلد المكتوب خطأً يُفشل التحقق."),
            ("10 / s", "لا أكثر من 10 استدعاءات في الثانية",
             "فوق ذلك، تُجيب الواجهة بالرمز 429. للملفات الكبيرة، أرسل العناوين واحدًا تلو الآخر، على وتيرتك."),
        ),
        "offre_jusquau": "حتى {date}", "offre_fini": "انتهت في {date}", "offre_mois": "{quota} عملية تحقق شهريًا",
        "offre_essai": "{quota} عملية تحقق، دون بطاقة بنكية. بعد ذلك: Starter بسعر {prix_starter} شهريًا مقابل {quota_starter} عملية تحقق، أو Pro بسعر {prix_pro} شهريًا مقابل {quota_pro}.",
        "offre_payant": "تُعاد حصتك إلى الصفر في {remise} (اليوم الأول من كل شهر، بتوقيت UTC).",
    },
}


def _cumul(jours: list[tuple[int, int]], rang: int) -> list[int]:
    total, sortie = 0, []
    for jour in jours:
        total += jour[rang]
        sortie.append(total)
    return sortie


def etincelle(valeurs: list[int], mauvais: bool = False) -> str:
    """Petite courbe (40 × 16) des valeurs du jour, du 1er à aujourd'hui."""
    haut = max(valeurs + [1])
    pas = 40 / max(len(valeurs) - 1, 1)
    points = " ".join(f"{round(rang * pas, 1)},{round(15 - valeur * 13 / haut, 1)}" for rang, valeur in enumerate(valeurs))
    classe = ' class="bad"' if mauvais else ""
    return f'<svg viewBox="0 0 40 16" aria-hidden="true"><polyline{classe} points="{points}"/></svg>'


def courbe(jours: list[tuple[int, int]], jours_du_mois: int, quota: int | None, rythme: float) -> str:
    """La courbe du mois : cumul des validations (bleu), des invalides (rouge), suite au même rythme (pointillé)."""
    cumul, cumul_inv = _cumul(jours, 0), _cumul(jours, 1)
    aujourdhui = len(jours)
    fin_prevue = cumul[-1] + rythme * (jours_du_mois - aujourdhui) if cumul else 0
    plafond = quota if quota else max(fin_prevue, cumul[-1] if cumul else 0, 1) * 1.15
    x = lambda jour: round((jour - 1) * 520 / max(jours_du_mois - 1, 1), 1)  # noqa: E731
    y = lambda valeur: round(140 - min(valeur, plafond) * 120 / plafond, 1)  # noqa: E731
    bleue = " ".join(f"{x(j)},{y(v)}" for j, v in enumerate(cumul, 1))
    rouge = " ".join(f"{x(j)},{y(v)}" for j, v in enumerate(cumul_inv, 1))
    suite = [f"{x(aujourdhui)},{y(cumul[-1])}"]
    if quota and rythme > 0 and cumul[-1] + rythme * (jours_du_mois - aujourdhui) > quota:
        atteint = aujourdhui + (quota - cumul[-1]) / rythme
        suite.append(f"{x(atteint)},{y(quota)}")
    suite.append(f"{x(jours_du_mois)},{y(fin_prevue)}")
    ligne = '<line x1="0" y1="20" x2="520" y2="20" stroke="#e2e7f0"/>' if quota else ""
    return (
        '<svg viewBox="0 0 520 150" preserveAspectRatio="none">' + ligne
        + f'<polyline points="{bleue}" fill="none" stroke="#1f3bff" stroke-width="2.4" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'
        + f'<polyline points="{rouge}" fill="none" stroke="#ff6f61" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'
        + f'<line x1="{x(aujourdhui)}" y1="{y(cumul[-1])}" x2="{x(aujourdhui)}" y2="150" stroke="#c3cce0" vector-effect="non-scaling-stroke"/>'
        + f'<polyline points="{" ".join(suite)}" fill="none" stroke="#1f3bff" stroke-width="2" stroke-dasharray="4 6" vector-effect="non-scaling-stroke" opacity=".55"/>'
        + "</svg>"
    )


def lien_paiement(base: str, infos: dict) -> str:
    """D91 : lien de paiement Stripe qui dit à quel compte rattacher le paiement (`client_reference_id`)."""
    champs = {"client_reference_id": infos.get("id"), "prefilled_email": infos.get("email")}
    requete = urlencode({cle: valeur for cle, valeur in champs.items() if valeur})
    return f"{base}?{requete}" if requete else base


def _bouton(classe: str, lien: str, texte: str) -> str:
    return f'<a class="{classe}" href="{html.escape(lien, quote=True)}">{html.escape(texte)} {FLECHE}</a>'


def page(lang: str, *, infos: dict, statut: dict, maintenant: datetime, jours: list[tuple[int, int]], erreur_cle: str = "") -> HTMLResponse:
    """Le tableau de bord d'un compte, dans une langue, privé (`no-store`)."""
    textes, t = s.TEXTES[lang], TEXTES[lang]
    reglages = get_settings()
    nombre = partial(i18n.nombre, lang)
    pluriel = lambda n, formes: s._pluriel(lang, n, formes).format(n=nombre(n))  # noqa: E731
    quota, restant, mois_n = statut["quota"], statut["restant"], statut["validations_mois"]
    inv_mois = sum(invalides for _, invalides in jours)
    devise = i18n.DEVISE[lang]
    cout_unitaire = i18n.MONTANTS[devise]["colis_rate"]
    cout = i18n.prix(lang, "colis_rate")
    eco_mois = s._montant(lang, inv_mois * cout_unitaire)
    essai_plan, fin = statut["plan"] == accounts.PLAN_PAR_DEFAUT, statut["trial_ends_at"]
    plan = textes["plans"].get(statut["plan"], statut["plan"])
    debut_mois = accounts.month_start(maintenant)
    suivant = (debut_mois + timedelta(days=32)).replace(day=1)
    jours_du_mois = (suivant - debut_mois).days
    remise = s._date(lang, suivant, "courte")
    date_fin = s._date(lang, fin, "longue") if fin else ""
    periode = s._date(lang, maintenant, "mois")
    communs = s._communs(lang, "compte")
    # D91 : on paie une offre par son lien Stripe ; au-delà de Pro (Enterprise), on écrit.
    url_plan = lien_paiement(reglages.stripe_lien_starter, infos)
    url_pro = lien_paiement(reglages.stripe_lien_pro, infos)
    url_contact = f"mailto:{reglages.contact_email}?subject={quote(t['sujet_plan'])}"
    if statut["plan"] == accounts.PLAN_PAR_DEFAUT:
        offres = (_bouton("btn btn-accent", url_plan, t["passer"].format(prix=communs["prix_starter"]))
                  + _bouton("btn btn-glass", url_pro, t["passer_pro"].format(prix=communs["prix_pro"])))
    elif statut["plan"] == "starter":
        offres = _bouton("btn btn-accent", url_pro, t["passer_pro"].format(prix=communs["prix_pro"]))
    else:
        offres = _bouton("btn btn-accent", url_contact, t["changer"])

    # Au rythme du mois (règle de trois), quand le quota serait-il épuisé ?
    rythme = mois_n / max(len(jours), 1)
    epuise = None
    if quota and restant and rythme > 0:
        epuise = maintenant + timedelta(days=math.ceil(restant / rythme))
    date_epuise = s._date(lang, epuise, "longue") if epuise else ""

    # Bonjour, puis le fait du mois.
    if inv_mois:
        titre, sous_titre = pluriel(inv_mois, t["titre_inv"]), pluriel(inv_mois, t["sous_inv"])
    elif mois_n:
        titre, sous_titre = pluriel(mois_n, t["titre_ok"]), t["sous_ok"]
    else:
        titre, sous_titre = t["titre_vide"], t["sous_vide"]

    # La prochaine étape : le premier état qui s'applique gagne (voir la docstring).
    champs_etat = {
        "essai_fini": textes["essai_fini"].format(date=date_fin), "essai_aucun": textes["essai_aucun"], "plan": plan,
        "prix_starter": communs["prix_starter"], "prix_pro": communs["prix_pro"],
        "quota_starter": communs["quota_starter"], "quota_pro": communs["quota_pro"],
        "quota": s._quota(lang, quota), "motif": accounts.REFUS_QUOTA_ATTEINT, "sans_plafond": textes["sans_plafond"],
    }
    boutons = ""
    if statut["expire"] or statut["quota_atteint"]:
        corps = s._etat(lang, "expire" if statut["expire"] else "quota", champs_etat)
        boutons = offres + _bouton("link", communs["url_pricing"], t["comparer"])
    elif fin is None:
        corps = s._etat(lang, "payant", champs_etat)
        if epuise and epuise < suivant:
            corps += f'<p>{html.escape(t["rythme_payant"].format(date=date_epuise, remise=remise))}</p>'
            boutons = offres
    elif not statut["validations"]:
        corps = f'<p>{html.escape(t["premier"])}</p>'
        boutons = _bouton("btn btn-accent", communs["url_guide"], t["guide"])
    else:
        limite = min(suivant, fin) if fin else suivant
        if epuise and epuise < limite:
            phrase = t["rythme_essai"].format(fin=date_fin, restant=nombre(restant), date=date_epuise)
            boutons = _bouton("btn btn-accent", url_plan, t["passer"].format(prix=communs["prix_starter"]))
        else:
            phrase = t["essai"].format(fin=date_fin, restant=nombre(restant or 0), quota=s._quota(lang, quota))
        corps = f"<p>{html.escape(phrase)}</p>"
        boutons += _bouton("link", communs["url_pricing"], t["comparer"])
    if reglages.stripe_portail and statut["plan"] in ("starter", "pro"):
        boutons += _bouton("link", reglages.stripe_portail, t["gerer"])
    prochaine = f'<p class="next-top"><i aria-hidden="true"></i>{html.escape(t["prochaine"])}</p>{corps}<div class="next-row">{boutons}</div>'

    # Le conseil du jour change chaque jour, dans l'ordre de la liste.
    code, titre_conseil, texte_conseil = t["conseils"][maintenant.toordinal() % len(t["conseils"])]
    conseil = (
        f'<div class="viz viz-tip" aria-hidden="true"><code>{html.escape(code)}</code><span>{html.escape(t["conseil"])}</span></div>'
        f"<h3>{html.escape(titre_conseil)}</h3><p>{html.escape(texte_conseil)}</p>"
        + _bouton("link", communs["url_guide"], t["voir_guide"])
    )

    if essai_plan and fin is not None:
        kpi5_label = t["kpi_essai"]
        kpi5 = t["termine"] if statut["trial_expire"] else pluriel(statut["trial_jours_restants"], t["jours"])
        offre_date = (t["offre_fini"] if statut["trial_expire"] else t["offre_jusquau"]).format(date=date_fin)
        offre_txt = t["offre_essai"].format(quota=s._quota(lang, quota), **{k: communs[k] for k in ("prix_starter", "quota_starter", "prix_pro", "quota_pro")})
    else:
        kpi5_label, kpi5 = t["kpi_plan"], plan
        offre_date = t["offre_mois"].format(quota=s._quota(lang, quota))
        offre_txt = t["offre_payant"].format(remise=remise)

    valeurs = {
        **communs,
        "url_deconnexion": i18n.chemin(lang, "compte") + "/deconnexion",
        "url_plan": url_plan,
        "kpi_quand": t["kpi_quand"].format(periode=periode[:1].upper() + periode[1:]),
        "mois_n": nombre(mois_n),
        "inv_mois": nombre(inv_mois),
        "eco_mois": eco_mois,
        "cout": cout,
        # Isolé de gauche à droite : en arabe, « 31 / 100 » garde son ordre.
        "restantes": "∞" if quota is None else f"\u2066{nombre(restant)} / {nombre(quota)}\u2069",
        "kpi5_label": kpi5_label,
        "kpi5": kpi5,
        "bonjour": t["bonjour"].format(boutique=infos["shop_name"]),
        "titre": titre,
        "sous_titre": sous_titre,
        "email": infos["email"],
        "prefixe": infos["prefixe"],
        "date_debut": s._date(lang, debut_mois, "courte"),
        "date_fin": s._date(lang, suivant - timedelta(days=1), "courte"),
        "ligne_quota": t["ligne_quota"].format(quota=nombre(quota)) if quota else "",
        "courbe_titre": s._pluriel(lang, mois_n, t["courbe_titre"]).format(n=nombre(mois_n), debut=s._date(lang, debut_mois, "courte")),
        "courbe_txt": t["courbe_txt"] + (t["courbe_quota"].format(quota=nombre(quota), date=date_epuise) if epuise and epuise < suivant else ""),
        "eco_titre": pluriel(inv_mois, t["eco_titre"]) if inv_mois else t["eco_zero"],
        "eco_calcul": s._pluriel(lang, inv_mois, textes["calcul"]).format(n=nombre(inv_mois), cout=cout, total=eco_mois),
        "eco_suite": t["eco_suite"],
        "depuis": t["depuis"].format(v=nombre(statut["validations"]), i=nombre(statut["invalides"])),
        "plan": plan,
        "offre_date": offre_date,
        "offre_txt": offre_txt,
    }
    fragments = {
        "spark_val": etincelle([jour[0] for jour in jours]),
        "spark_inv": etincelle([jour[1] for jour in jours], mauvais=True),
        "prochaine": prochaine,
        "courbe": courbe(jours, jours_du_mois, quota, rythme),
        "conseil": conseil,
        "erreur_cle": f'<p class="regen-err" role="alert">{html.escape(erreur_cle)}</p>' if erreur_cle else "",
    }
    blocs = {"fact_essai" if essai_plan else "fact_payant"}
    texte = s._page("compte", lang, valeurs, blocs, fragments=fragments)
    return HTMLResponse(texte, status_code=200, headers=s.SECRET_HEADERS)
