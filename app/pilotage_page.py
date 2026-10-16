"""Tableau de bord d'exploitation, version 2 (D69) : le dessin validé par Fzed, sur les vrais chiffres.

Barre latérale (sections, état, déconnexion), grande carte du résultat du mois, cartes de l'encaissé,
des abonnements, des appels et des visites, puis les sections Argent, API, Clients, Site et SEO (carte
du monde en points, sources, robots, pages) et le registre des paiements. Tout vient de
`app/exploitation.py` ; **aucun chiffre n'est écrit ici**. Les évolutions (« +18 % ») ne s'affichent
que si la période précédente a des données. Aucun script (CSP de D32) : animations en CSS seul,
coupées par `prefers-reduced-motion` ; chaque valeur affichée est échappée.
"""

from __future__ import annotations

import calendar
import html
import math

from app import accounts, i18n
from app.carte_monde import HAUTEUR, LARGEUR, POINTS
from app.exploitation import PERIODES

CSS = """
:root{--bg:#050a16;--s1:#0a1326;--s2:#0e1a33;--ln:rgba(120,150,255,.12);--ln2:rgba(120,150,255,.22);--tx:#eaf0ff;--mu:#8b9bc0;--fa:#5d6d93;
--c1:#ff8a3d;--c2:#5b7cff;--c3:#2fd4c4;--ok:#34d399;--ko:#fb7185;--wa:#fbbf24;--r:20px;color-scheme:dark}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Inter,Roboto,sans-serif;-webkit-font-smoothing:antialiased}
body::before{content:"";position:fixed;inset:0;z-index:-1;background:radial-gradient(900px 520px at 85% -10%,rgba(91,124,255,.16),transparent 60%),radial-gradient(700px 420px at -5% 30%,rgba(47,212,196,.08),transparent 60%),radial-gradient(600px 400px at 60% 110%,rgba(255,138,61,.07),transparent 60%)}
.ico{fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex:none}
.app{display:grid;grid-template-columns:248px minmax(0,1fr);min-height:100vh}
/* barre latérale */
.side{position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:6px;padding:26px 18px;border-right:1px solid var(--ln);background:rgba(7,13,28,.7);backdrop-filter:blur(14px)}
.logo{display:flex;align-items:center;gap:12px;padding:4px 10px 26px}
.orbe{width:34px;height:34px;border-radius:11px;background:conic-gradient(from 200deg,var(--c1),var(--c2),var(--c3),var(--c1));box-shadow:0 0 24px rgba(91,124,255,.45),inset 0 0 0 1px rgba(255,255,255,.25);position:relative;animation:tourne 12s linear infinite}
.orbe::after{content:"";position:absolute;inset:9px;border-radius:5px;background:var(--bg)}
@keyframes tourne{to{filter:hue-rotate(360deg)}}
.logo b{display:block;font-size:15px;letter-spacing:.01em}.logo small{color:var(--mu);font-size:12px}
.nav a{display:flex;align-items:center;gap:12px;padding:11px 12px;border-radius:12px;color:var(--mu);text-decoration:none;font-size:14.5px;position:relative;transition:background .2s,color .2s}
.nav a:hover{background:rgba(120,150,255,.07);color:var(--tx)}
.nav a[aria-current]{color:var(--tx);background:linear-gradient(90deg,rgba(91,124,255,.18),rgba(91,124,255,0))}
.nav a[aria-current]::before{content:"";position:absolute;left:-18px;top:10px;bottom:10px;width:3px;border-radius:0 3px 3px 0;background:linear-gradient(var(--c2),var(--c3));box-shadow:0 0 12px var(--c3)}
.side .bas{margin-top:auto;display:grid;gap:10px}
.etat{display:flex;align-items:center;gap:10px;padding:12px;border-radius:14px;border:1px solid var(--ln);background:var(--s1);font-size:13px}
.etat i{width:9px;height:9px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 0 rgba(52,211,153,.6);animation:pouls 2s infinite}
@keyframes pouls{0%{box-shadow:0 0 0 0 rgba(52,211,153,.55)}70%,100%{box-shadow:0 0 0 10px rgba(52,211,153,0)}}
.etat small{display:block;color:var(--mu);font-size:12px}
/* contenu */
main{padding:34px clamp(18px,3.4vw,48px) 60px;max-width:1480px;width:100%}
.top{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:18px 24px;margin-bottom:26px}
.sur{margin:0 0 6px;color:var(--mu);font-size:13px;letter-spacing:.04em}
h1{margin:0;font-size:clamp(26px,3vw,36px);line-height:1.15;letter-spacing:-.02em;font-weight:680}
h1 span{background:linear-gradient(90deg,var(--c1),#ff6fb0 35%,var(--c2) 65%,var(--c3));-webkit-background-clip:text;background-clip:text;color:transparent;background-size:200% 100%;animation:onde 8s ease-in-out infinite}
@keyframes onde{50%{background-position:100% 0}}
.outils{display:flex;flex-wrap:wrap;align-items:center;gap:12px}
.seg{display:flex;padding:4px;border-radius:14px;border:1px solid var(--ln);background:var(--s1)}
.seg a{padding:8px 14px;min-height:36px;border-radius:10px;color:var(--mu);text-decoration:none;font-size:13.5px;transition:color .2s,background .2s}
.seg a:hover{color:var(--tx)}
.seg a[aria-current]{background:var(--s2);color:var(--tx);box-shadow:inset 0 0 0 1px var(--ln2),0 6px 18px rgba(0,0,0,.35)}
.live{display:inline-flex;align-items:center;gap:8px;padding:9px 14px;border-radius:999px;border:1px solid rgba(52,211,153,.3);background:rgba(52,211,153,.07);color:#bdf5dc;font-size:13px}
.live i{width:8px;height:8px;border-radius:50%;background:var(--ok);animation:pouls 2s infinite}
.exemple{display:inline-block;margin-left:10px;padding:3px 9px;border-radius:999px;background:rgba(251,191,36,.12);color:#fde68a;font-size:11.5px;letter-spacing:.04em;vertical-align:middle}
/* alertes */
.alertes{display:grid;gap:10px;margin-bottom:28px}
.alerte{display:flex;align-items:center;gap:14px;padding:14px 18px;border-radius:16px;border:1px solid rgba(251,191,36,.28);background:linear-gradient(90deg,rgba(251,191,36,.10),rgba(251,191,36,.02));color:#fde9b0;font-size:14px;animation:entre .6s both}
.alerte .ico{color:var(--wa)}.alerte b{color:#fff}.alerte a{margin-left:auto;color:#fde68a;font-size:13px}
/* cartes */
.carte{position:relative;padding:24px;border-radius:var(--r);background:linear-gradient(180deg,rgba(16,28,56,.78),rgba(10,19,38,.78));border:1px solid var(--ln);box-shadow:0 1px 0 rgba(255,255,255,.04) inset,0 20px 40px -24px rgba(0,0,0,.6);animation:entre .7s cubic-bezier(.2,.75,.25,1) both;animation-delay:calc(var(--i,0)*70ms);transition:border-color .25s,transform .25s}
.carte:hover{border-color:var(--ln2)}
@keyframes entre{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.lbl{display:flex;align-items:center;gap:8px;margin:0;color:var(--mu);font-size:12.5px;letter-spacing:.08em;text-transform:uppercase}
.lbl .ico{color:var(--c3)}
.val{margin:10px 0 2px;font-size:clamp(26px,2.6vw,34px);font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.1}
.val small{font-size:.5em;color:var(--mu);font-weight:600;margin-left:4px}
.sous{color:var(--mu);font-size:13.5px}
.delta{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:999px;font-size:12.5px;font-weight:600}
.delta.up{background:rgba(52,211,153,.12);color:var(--ok)}.delta.dn{background:rgba(251,113,133,.12);color:var(--ko)}
.spark{display:block;width:100%;height:44px;margin-top:14px}
.spark polyline{fill:none;stroke-width:2;vector-effect:non-scaling-stroke;filter:drop-shadow(0 0 6px currentColor);animation:revele 1.6s ease-out both}
@keyframes revele{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
/* grille bento */
.bento{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px}
.heros{grid-column:span 2;grid-row:span 2;display:flex;flex-direction:column;overflow:hidden}
.heros::after{content:"";position:absolute;inset:-1px;border-radius:inherit;padding:1px;pointer-events:none;background:linear-gradient(140deg,var(--c1),transparent 30%,transparent 70%,var(--c3));-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;opacity:.55}
.heros .val{font-size:clamp(44px,5.4vw,68px);margin-top:16px;background:linear-gradient(180deg,#d6ffef,var(--ok));-webkit-background-clip:text;background-clip:text;color:transparent;filter:drop-shadow(0 0 22px rgba(52,211,153,.35))}
.repart{display:flex;gap:28px;margin:18px 0 6px;flex-wrap:wrap}
.repart div{font-size:13px;color:var(--mu)}.repart b{display:block;color:var(--tx);font-size:18px;font-variant-numeric:tabular-nums}
.jauge{display:flex;height:10px;border-radius:99px;overflow:hidden;background:rgba(120,150,255,.1);margin:10px 0 0}
.jauge span{display:block;height:100%;transform-origin:left;animation:pousse 1.2s cubic-bezier(.2,.75,.25,1) both}
.jauge .r{background:linear-gradient(90deg,var(--c2),var(--c3))}.jauge .c{background:var(--c1)}
@keyframes pousse{from{transform:scaleX(0)}}
.heros .chart{margin-top:auto;height:150px}
/* sections */
.sec{margin-top:52px;scroll-margin-top:20px}
.sec-h{display:flex;align-items:center;gap:12px;margin:0 0 18px}
.sec-h h2{margin:0;font-size:20px;letter-spacing:-.01em}
.sec-h .pastille{display:grid;place-items:center;width:36px;height:36px;border-radius:12px;background:rgba(91,124,255,.12);color:#b9c6ff;border:1px solid var(--ln)}
.sec-h p{margin:0 0 0 auto;color:var(--mu);font-size:13px}
.duo{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(0,1fr);gap:20px}
.trio{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}
.quad{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px}
.carte h3{margin:0 0 4px;font-size:15.5px;font-weight:620}
.carte .aide{margin:0 0 16px;color:var(--mu);font-size:13px}
.chart{display:block;width:100%;height:auto;overflow:visible}
.axes line{stroke:rgba(120,150,255,.1);stroke-dasharray:3 5}
.axes text{fill:var(--fa);font-size:11px}
.serie{fill:none;stroke-width:2.4;stroke-linejoin:round}
.serie.cout{stroke:var(--c1);stroke-dasharray:6 6;stroke-width:1.8}
.trace{animation:revele 1.8s ease-out both}
.pt{fill:transparent;cursor:crosshair}.pt:hover{fill:rgba(47,212,196,.25)}
.legende{display:flex;gap:18px;margin-top:10px;color:var(--mu);font-size:12.5px}
.legende i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.leve{animation:leve 1.3s cubic-bezier(.2,.75,.25,1) both}
@keyframes leve{from{clip-path:inset(100% 0 0 0)}to{clip-path:inset(0 0 0 0)}}
.b.ok{fill:#4b6bff}.b.pay{fill:#2fd4c4}.b.err{fill:var(--ko)}
@keyframes monte{from{transform:scaleY(0)}}
.rings{display:flex;align-items:center;gap:18px}
.ring{position:relative;width:84px;height:84px;flex:none}
.ring svg{width:100%;height:100%;transform:rotate(-90deg)}
.ring-fond{fill:none;stroke:rgba(120,150,255,.12);stroke-width:7}
.ring-val{fill:none;stroke-width:7;stroke-linecap:round;filter:drop-shadow(0 0 6px currentColor);animation:anneau 1.4s cubic-bezier(.2,.75,.25,1) both}
@keyframes anneau{from{stroke-dasharray:0 999}}
.ring span{position:absolute;inset:0;display:grid;place-items:center;font-weight:700;font-size:20px;font-variant-numeric:tabular-nums}
.ring small{font-size:11px;color:var(--mu)}
/* listes */
.pays,.barres{list-style:none;margin:0;padding:0;display:grid;gap:12px}
.pays li{display:grid;grid-template-columns:34px minmax(0,1fr) minmax(60px,1fr) auto 46px;align-items:center;gap:12px;font-size:14px;animation:entre .6s both;animation-delay:calc(var(--i)*60ms + .2s)}
.cc{display:grid;place-items:center;height:24px;border-radius:7px;background:rgba(120,150,255,.1);border:1px solid var(--ln);font:600 11px ui-monospace,"SF Mono",monospace;color:#c9d4ff}
.trk{height:6px;border-radius:99px;background:rgba(120,150,255,.1);overflow:hidden}
.trk span{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,var(--c2),var(--c3));transform-origin:left;animation:pousse 1.1s cubic-bezier(.2,.75,.25,1) both;animation-delay:.3s}
.pays b,.barres b{font-variant-numeric:tabular-nums;font-weight:600;text-align:right}
.pays em{color:var(--fa);font-style:normal;font-size:12.5px;text-align:right}
.barres li{display:grid;grid-template-columns:minmax(0,9em) 1fr auto;align-items:center;gap:12px;font-size:14px}
/* entonnoir */
.funnel{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0;margin-top:6px}
.etape{position:relative;padding:18px 18px 20px;border-right:1px dashed var(--ln)}
.etape:last-child{border-right:0}
.etape .n{font-size:30px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.etape .t{color:var(--mu);font-size:13px}
.etape .bar{height:64px;margin-top:14px;border-radius:12px;background:linear-gradient(180deg,rgba(91,124,255,.55),rgba(47,212,196,.18));transform-origin:bottom;animation:monte 1s cubic-bezier(.2,.75,.25,1) both}
.etape .taux{display:inline-block;margin-top:6px;padding:3px 9px;border-radius:999px;background:rgba(47,212,196,.1);border:1px solid rgba(47,212,196,.25);font-size:12px;color:var(--c3);font-weight:650}
/* tableaux */
.tab{width:100%;border-collapse:collapse;font-size:14px}
.tab td{padding:11px 0;border-bottom:1px solid rgba(120,150,255,.08)}
.tab td:last-child{text-align:right}
.badge{display:inline-block;padding:3px 9px;border-radius:999px;font-size:12px;font-weight:650}
.badge.chaud{background:rgba(251,113,133,.14);color:#fecdd3}.badge.tiede{background:rgba(251,191,36,.14);color:#fde68a}.badge.froid{background:rgba(120,150,255,.12);color:#c7d2fe}
.mini{height:6px;border-radius:99px;background:rgba(120,150,255,.12);overflow:hidden;margin-top:6px}
.mini span{display:block;height:100%;background:linear-gradient(90deg,var(--wa),var(--c1));animation:pousse 1.1s both}
/* carte du monde */
.monde{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(0,1fr);gap:24px;align-items:center}
.map{display:block;width:100%;height:auto}
.d{fill:rgba(120,150,255,.16);animation:scintille 6s ease-in-out infinite;animation-delay:calc(var(--k)*-150ms)}
.d.n1{fill:rgba(91,124,255,.55)}.d.n2{fill:#5b7cff}.d.n3{fill:#3fb8e6}.d.n4{fill:#2fd4c4;filter:drop-shadow(0 0 3px #2fd4c4)}
.d.top{animation:phare 2.4s ease-in-out infinite;animation-delay:calc(var(--k)*-60ms)}
@keyframes scintille{50%{opacity:.55}}
@keyframes phare{50%{fill:#9ff7ee}}
.robots{display:flex;flex-wrap:wrap;gap:10px}
.robot{display:inline-flex;align-items:center;gap:8px;padding:9px 13px;border-radius:12px;border:1px solid var(--ln);background:var(--s1);font-size:13.5px}
.robot .ico{color:var(--c3)}.robot b{font-variant-numeric:tabular-nums}
footer{margin-top:56px;color:var(--fa);font-size:12.5px}
a:focus-visible{outline:2px solid var(--c3);outline-offset:2px}
@media (max-width:1180px){.bento{grid-template-columns:repeat(2,minmax(0,1fr))}.quad{grid-template-columns:repeat(2,minmax(0,1fr))}.duo,.monde{grid-template-columns:minmax(0,1fr)}}
@media (max-width:860px){.app{grid-template-columns:minmax(0,1fr)}.side{position:static;height:auto;flex-direction:row;flex-wrap:wrap;align-items:center;padding:14px 16px;border-right:0;border-bottom:1px solid var(--ln)}
.logo{padding:0 12px 0 0}.nav{display:flex;overflow-x:auto;gap:4px;width:100%}.nav a{padding:9px 12px;white-space:nowrap}.nav a[aria-current]::before{display:none}.side .bas{display:none}
.trio,.funnel{grid-template-columns:minmax(0,1fr)}.etape{border-right:0;border-bottom:1px dashed var(--ln)}}
@media (max-width:560px){.bento,.quad{grid-template-columns:minmax(0,1fr)}.heros{grid-column:auto}.pays li{grid-template-columns:34px minmax(0,1fr) auto 40px}.pays .trk{display:none}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}

.nav button{all:unset;box-sizing:border-box;display:flex;align-items:center;gap:12px;width:100%;padding:11px 12px;border-radius:12px;color:var(--mu);font-size:14.5px;cursor:pointer}
.nav button:hover{background:rgba(120,150,255,.07);color:var(--tx)}.nav button:focus-visible{outline:2px solid var(--c3)}
.etat.wa i{background:var(--wa);animation:none}.etat.ko i{background:var(--ko);animation:none}
.alerte.ko{border-color:rgba(251,113,133,.35);background:linear-gradient(90deg,rgba(251,113,133,.12),rgba(251,113,133,.02));color:#ffd1d9}.alerte.ko .ico{color:var(--ko)}
.calme{display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:16px;border:1px solid rgba(52,211,153,.25);background:rgba(52,211,153,.06);color:#bdf5dc;font-size:14px}
.heros.neutre .val{background:linear-gradient(180deg,#fff,#b9c6ff);-webkit-background-clip:text;background-clip:text;filter:drop-shadow(0 0 22px rgba(91,124,255,.35))}
.heros.perte .val{background:linear-gradient(180deg,#ffe4e8,var(--ko));-webkit-background-clip:text;background-clip:text;filter:drop-shadow(0 0 22px rgba(251,113,133,.35))}
.vide{color:var(--fa);font-size:13.5px;margin:6px 0 0}
.tab .mono{font:12px ui-monospace,"SF Mono",monospace;color:#c9d4ff}
"""

#: Noms des pays les plus probables ; les autres gardent leur code.
NOMS_PAYS = {
    "US": "États-Unis", "FR": "France", "RE": "La Réunion", "DE": "Allemagne", "GB": "Royaume-Uni", "CA": "Canada",
    "IN": "Inde", "BR": "Brésil", "ES": "Espagne", "IT": "Italie", "NL": "Pays-Bas", "BE": "Belgique", "CH": "Suisse",
    "IE": "Irlande", "PT": "Portugal", "SE": "Suède", "NO": "Norvège", "DK": "Danemark", "FI": "Finlande",
    "PL": "Pologne", "AT": "Autriche", "CZ": "Tchéquie", "RO": "Roumanie", "GR": "Grèce", "UA": "Ukraine",
    "RU": "Russie", "TR": "Turquie", "IL": "Israël", "AE": "Émirats arabes unis", "SA": "Arabie saoudite",
    "EG": "Égypte", "MA": "Maroc", "DZ": "Algérie", "TN": "Tunisie", "SN": "Sénégal", "CI": "Côte d'Ivoire",
    "NG": "Nigeria", "KE": "Kenya", "ZA": "Afrique du Sud", "MG": "Madagascar", "MU": "Maurice", "YT": "Mayotte",
    "CN": "Chine", "JP": "Japon", "KR": "Corée du Sud", "TW": "Taïwan", "HK": "Hong Kong", "SG": "Singapour",
    "MY": "Malaisie", "TH": "Thaïlande", "VN": "Viêt Nam", "ID": "Indonésie", "PH": "Philippines",
    "PK": "Pakistan", "BD": "Bangladesh", "AU": "Australie", "NZ": "Nouvelle-Zélande", "MX": "Mexique",
    "AR": "Argentine", "CL": "Chili", "CO": "Colombie", "PE": "Pérou", "VE": "Venezuela", "LU": "Luxembourg",
    "HU": "Hongrie", "BG": "Bulgarie", "RS": "Serbie", "HR": "Croatie", "LT": "Lituanie", "LV": "Lettonie",
    "EE": "Estonie", "SK": "Slovaquie", "SI": "Slovénie", "IS": "Islande", "QA": "Qatar", "KW": "Koweït",
    "GP": "Guadeloupe", "MQ": "Martinique", "GF": "Guyane", "NC": "Nouvelle-Calédonie", "PF": "Polynésie française",
}

I = {
    "vue": '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    "argent": '<path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h14a2 2 0 0 1 2 2v4h-4a2 2 0 0 0 0 4h4v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5"/>',
    "api": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "clients": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    "site": '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "registre": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    "sortie": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    "alerte": '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01"/>',
    "ok": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14M22 4 12 14.01l-3-3"/>',
    "haut": '<path d="m18 15-6-6-6 6"/>', "bas": '<path d="m6 9 6 6 6-6"/>',
    "horloge": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "bouclier": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "robot": '<rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4M8 16h.01M16 16h.01"/>',
    "cadenas": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
}
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")


def e(valeur) -> str:
    return html.escape(str(valeur))


def ico(nom: str, taille: int = 18) -> str:
    return f'<svg class="ico" width="{taille}" height="{taille}" viewBox="0 0 24 24" aria-hidden="true">{I[nom]}</svg>'


def fr(valeur: float, decimales: int = 0) -> str:
    return f"{valeur:,.{decimales}f}".replace(",", " ").replace(".", ",")


def euros(valeur, decimales: int = 2) -> str:
    return "—" if valeur is None else f"{fr(valeur, decimales)} €"


def signe(valeur: float) -> str:
    return ("+" if valeur > 0 else "") + euros(valeur)


def pays(code: str) -> str:
    return "Inconnu" if not code or code == "??" else NOMS_PAYS.get(code, code)


def delta(valeur: float | None, texte: str = "") -> str:
    """Badge d'évolution, seulement quand la période précédente a des données."""
    if valeur is None:
        return ""
    sens = "up" if valeur >= 0 else "dn"
    return (f'<span class="delta {sens}">{ico("haut" if valeur >= 0 else "bas", 14)} {fr(abs(valeur) * 100)} %</span>'
            f'{f" <span class=sous>{e(texte)}</span>" if texte else ""}')


def spark(valeurs: list[float], h: int = 44) -> str:
    haut = max(valeurs or [0]) or 1
    pas = 200 / max(len(valeurs) - 1, 1)
    points = " ".join(f"{k * pas:.1f},{h - 4 - (h - 8) * v / haut:.1f}" for k, v in enumerate(valeurs))
    return f'<svg class="spark" viewBox="0 0 200 {h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="{points}"/></svg>'


def _date_courte(debut, k: int) -> str:
    from datetime import timedelta

    jour = debut + timedelta(days=k)
    return f"{jour.day} {MOIS[jour.month - 1][:4].rstrip('.')}."


def courbe(nom: str, valeurs: list[float], moment, cout_jour: float | None = None, unite: str = "€", axes: bool = True) -> str:
    """Courbe des 30 derniers jours (aire dégradée), et la ligne des coûts quand ils sont connus."""
    from datetime import timedelta

    L, H = 720, (240 if axes else 150)
    g, b = (52, 26) if axes else (4, 6)
    haut = max(valeurs + [cout_jour or 0] + [0]) * 1.15 or 1
    n = len(valeurs)
    x = lambda k: g + k * (L - g - 8) / max(n - 1, 1)  # noqa: E731
    y = lambda v: 10 + (H - b - 10) * (1 - v / haut)  # noqa: E731
    debut = moment - timedelta(days=n - 1)
    axe = ""
    if axes:
        axe = "".join(f'<line x1="{g}" x2="{L - 8}" y1="{y(v):.1f}" y2="{y(v):.1f}"/><text x="{g - 10}" y="{y(v) + 4:.1f}" text-anchor="end">{e(fr(v, 0 if haut > 30 else 2))} {e(unite)}</text>'
                      for v in (0, haut / 3, 2 * haut / 3))
        axe += "".join(f'<text x="{x(k):.1f}" y="{H - 6}" text-anchor="middle">{e(_date_courte(debut, k))}</text>' for k in range(0, n, 7))
    pts = " ".join(f"{x(k):.1f},{y(v):.1f}" for k, v in enumerate(valeurs))
    cout = f'<polyline points="{g},{y(cout_jour):.1f} {L - 8},{y(cout_jour):.1f}" class="serie cout"/>' if cout_jour else ""
    survol = "".join(f'<circle class="pt" cx="{x(k):.1f}" cy="{y(v):.1f}" r="9"><title>{e(_date_courte(debut, k))} : {e(fr(v, 2))} {e(unite)}</title></circle>' for k, v in enumerate(valeurs)) if axes else ""
    return (f'<svg class="chart" viewBox="0 0 {L} {H}" role="img" aria-label="{e(nom)}"><defs>'
            f'<linearGradient id="a-{nom}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#2fd4c4" stop-opacity=".35"/><stop offset="1" stop-color="#2fd4c4" stop-opacity="0"/></linearGradient>'
            f'<linearGradient id="t-{nom}" x1="0" x2="1"><stop offset="0" stop-color="#5b7cff"/><stop offset="1" stop-color="#2fd4c4"/></linearGradient>'
            f'<filter id="l-{nom}" x="-10%" y="-30%" width="120%" height="160%"><feGaussianBlur stdDeviation="4" result="f"/><feMerge><feMergeNode in="f"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>'
            f'<g class="axes">{axe}</g><g class="trace"><polygon points="{g},{H - b} {pts} {L - 8},{H - b}" fill="url(#a-{nom})"/>'
            f'<polyline points="{pts}" class="serie" stroke="url(#t-{nom})" filter="url(#l-{nom})"/>{cout}</g>{survol}</svg>')


def barres_jour(valeurs: list[float]) -> str:
    L, H = 720, 200
    haut = max(valeurs or [0]) * 1.1 or 1
    l = (L - 20) / max(len(valeurs), 1)
    rects = "".join(f'<rect class="b ok" x="{10 + k * l + 2:.1f}" y="{H - 6 - (H - 16) * v / haut:.1f}" width="{max(l - 5, 1):.1f}" height="{(H - 16) * v / haut:.1f}" rx="2"><title>{e(fr(v))} appels</title></rect>'
                    for k, v in enumerate(valeurs))
    return f'<svg class="chart" viewBox="0 0 {L} {H}" role="img" aria-label="Appels par jour"><g class="leve">{rects}</g></svg>'


def anneau(part: float | None, couleur: str) -> str:
    c = 2 * math.pi * 34
    p = part or 0
    texte = "—" if part is None else f"{fr(p * 100)}<small>%</small>"
    return (f'<div class="ring"><svg viewBox="0 0 80 80" aria-hidden="true"><circle cx="40" cy="40" r="34" class="ring-fond"/>'
            f'<circle cx="40" cy="40" r="34" class="ring-val" style="stroke:{couleur};stroke-dasharray:{c * p:.1f} {c:.1f}"/></svg><span>{texte}</span></div>')


def carte_monde(visites: list[tuple[str, int]]) -> str:
    compte = {code: n for code, n in visites if code != "??"}
    maxi = max(compte.values() or [0]) or 1
    tete = sorted(compte, key=compte.get, reverse=True)[:3]
    points = []
    for code, cellules in POINTS.items():
        n = compte.get(code, 0)
        niveau = 0 if not n else 1 + min(3, int(4 * math.sqrt(n / maxi)))
        titre = f"<title>{e(pays(code))} : {e(fr(n))} visite{'s' if n > 1 else ''}</title>" if n else ""
        for cell in cellules:
            cx, cy = divmod(cell, 100)
            classe = f"d n{niveau}" + (" top" if code in tete and n else "")
            points.append(f'<circle class="{classe}" cx="{cx * 10 + 5}" cy="{cy * 10 + 5}" r="{4.2 if code == "RE" else 3.6}" style="--k:{(cx * 7 + cy * 13) % 40}">{titre}</circle>')
    return f'<svg class="map" viewBox="0 0 {LARGEUR * 10} {HAUTEUR * 10}" role="img" aria-label="Visites par pays">{"".join(points)}</svg>'


def liste_pays(items: list[tuple[str, float]], unite: str, vide: str) -> str:
    items = [(c, v) for c, v in items if v > 0][:7]
    if not items:
        return f'<p class="vide">{e(vide)}</p>'
    total, maxi = sum(v for _, v in items), items[0][1]
    lignes = "".join(
        f'<li style="--i:{i}"><span class="cc">{e(c if c != "??" else "–")}</span><span class="nom">{e(pays(c))}</span>'
        f'<span class="trk"><span style="width:{100 * v / maxi:.0f}%"></span></span>'
        f'<b>{e(euros(v) if unite == "€" else fr(v))}</b><em>{fr(100 * v / total)} %</em></li>'
        for i, (c, v) in enumerate(items)
    )
    return f'<ul class="pays">{lignes}</ul>'


def barres(items: list[tuple[str, float]], vide: str, format_valeur=None) -> str:
    items = [(n, v) for n, v in items if v > 0]
    if not items:
        return f'<p class="vide">{e(vide)}</p>'
    maxi = max(v for _, v in items)
    f = format_valeur or (lambda v: fr(v))
    return '<ul class="barres">' + "".join(
        f'<li style="--i:{i}"><span>{e(n)}</span><span class="trk"><span style="width:{100 * v / maxi:.0f}%"></span></span><b>{e(f(v))}</b></li>'
        for i, (n, v) in enumerate(items)) + "</ul>"


def tableau(lignes: list[tuple[str, str]], vide: str) -> str:
    if not lignes:
        return f'<p class="vide">{e(vide)}</p>'
    return '<table class="tab">' + "".join(f"<tr><td>{g}</td><td>{d}</td></tr>" for g, d in lignes) + "</table>"


def _jours(n: float) -> str:
    n = max(0, round(n))
    return "aujourd'hui" if n == 0 else f"{n} jour{'s' if n > 1 else ''}"


def registre(stats: dict) -> str:
    """Le registre des paiements (D31, D40) : revenu réel par fenêtre, recette à part, dernières lignes."""
    ledger, windows, recent = stats.get("ledger", {}), stats.get("windows", {}), stats.get("recent", [])
    reel = lambda bloc: float(bloc.get("real_revenue_usdc", bloc.get("revenue_usdc", 0)) or 0)  # noqa: E731
    etat = ("" if ledger.get("available") else
            f'<p class="vide">Registre indisponible ({e(ledger.get("error"))}) : les chiffres sont incomplets.</p>')
    fenetres = barres([(nom, reel(windows.get(cle, {}))) for nom, cle in (("24 h", "24h"), ("7 jours", "7d"), ("30 jours", "30d"))],
                      "Aucun encaissement réel sur 30 jours.", lambda v: f"{fr(v, 2)} USDC")
    lignes = [(f'<span class="mono">{e(str(item.get("tx_hash"))[:18])}…</span>',
               f'{e(fr(float(item.get("amount_usdc", 0) or 0), 2))} USDC · {e("recette, exclue" if item.get("test_mode") else "client")}')
              for item in recent[:6]]
    recette = ledger.get("test_validations_paid", 0)
    return (f'<section class="sec" id="registre"><div class="sec-h"><span class="pastille">{ico("registre")}</span><h2>Registre des paiements</h2>'
            f'<p>Revenu réel ; la recette est exclue et comptée à part ({e(recette)} essai{"s" if recette != 1 else ""})</p></div>'
            f'<div class="duo"><article class="carte"><h3>Encaissé réel par fenêtre</h3>{etat}{fenetres}</article>'
            f'<article class="carte"><h3>Dernières transactions</h3>{tableau(lignes, "Aucune transaction enregistrée.")}</article></div></section>')


def page(d: dict, sante: dict, stats: dict) -> str:
    """La page complète. `d` vient de `exploitation.collecter` (ou porte `erreur`)."""
    moment = d.get("moment")
    entete_date = f"{JOURS[moment.weekday()]} {moment.day} {MOIS[moment.month - 1]} {moment.year} · {moment:%H:%M} UTC".upper() if moment else ""
    periode = d.get("periode", "mois")
    libelle = PERIODES[periode][0]
    nav = "".join(f'<a href="#{a}"{" aria-current=page" if a == "vue" else ""}>{ico(i)}{t}</a>' for a, i, t in (
        ("vue", "vue", "Vue d'ensemble"), ("argent", "argent", "Argent"), ("api", "api", "API"),
        ("clients", "clients", "Clients"), ("site", "site", "Site et SEO"), ("registre", "registre", "Registre")))
    periodes = "".join(f'<a href="?p={cle}"{" aria-current=page" if cle == periode else ""}>{e(lib)}</a>' for cle, (lib, _) in PERIODES.items())
    alertes = d.get("alertes", [])
    en_panne = [nom for nom, ok in sante.items() if not ok]
    etat = (("ko", "Problème détecté", ", ".join(en_panne) + " en panne") if en_panne else
            ("wa", "À surveiller", f"{len(alertes)} alerte{'s' if len(alertes) > 1 else ''}") if alertes else
            ("", "Tout fonctionne", "Base, cache, paiements"))
    cote = (f'<aside class="side"><div class="logo"><span class="orbe"></span><div><b>Address Validator</b><small>Pilotage</small></div></div>'
            f'<nav class="nav" aria-label="Sections">{nav}</nav><div class="bas">'
            f'<div class="etat {etat[0]}"><i></i><div>{e(etat[1])}<small>{e(etat[2])}</small></div></div>'
            f'<form class="nav" method="post" action="/dashboard/deconnexion"><button type="submit">{ico("sortie")}Se déconnecter</button></form></div></aside>')
    haut = (f'<header class="top" id="vue"><div><p class="sur">{e(entete_date)}</p><h1>Bonjour, voici <span>votre activité</span>.</h1></div>'
            f'<div class="outils"><nav class="seg" aria-label="Période">{periodes}</nav><span class="live"><i></i>En direct · actualisé chaque minute</span></div></header>')
    if "erreur" in d:
        corps = (f'<section class="alertes"><div class="alerte ko">{ico("alerte", 20)}<span><b>Chiffres indisponibles</b> : la base ne répond pas ({e(d["erreur"])}).</span></div></section>'
                 + registre(stats))
        return _document(cote + f"<main>{haut}{corps}{_pied()}</main>")

    a, api, cl, si = d["argent"], d["api"], d["clients"], d["site"]
    bloc_alertes = "".join(f'<div class="alerte">{ico("alerte", 20)}<span>{e(x)}</span></div>' for x in alertes) or f'<div class="calme">{ico("ok", 18)}Tout va bien : aucune alerte.</div>'
    bloc_alertes += "".join(f'<div class="alerte ko">{ico("alerte", 20)}<span><b>{e(nom)}</b> : en panne.</span></div>' for nom in en_panne)

    # --- grande carte : le résultat du mois quand les coûts sont connus, sinon les revenus du mois.
    jours_mois = calendar.monthrange(moment.year, moment.month)[1]
    taux = float(i18n.TAUX["USD"])
    courbe_eur = [v / taux for v in a["courbe_usdc"]]
    resultat = a["resultat_mois_eur"]
    if resultat is not None:
        classe, titre, valeur = ("heros" if resultat >= 0 else "heros perte"), "Résultat du mois", signe(resultat)
        details = (f'<div>Revenus<b>{e(euros(a["revenus_mois_eur"]))}</b></div><div>Coûts<b>{e(euros(a["couts_mois_eur"]))}</b></div>'
                   f'<div>Prévision fin de mois<b>{e(signe(a["prevision_resultat_eur"]))}</b></div>')
        total = (a["revenus_mois_eur"] or 0) + (a["couts_mois_eur"] or 0) or 1
        jauge = f'<div class="jauge"><span class="r" style="width:{100 * a["revenus_mois_eur"] / total:.0f}%"></span><span class="c" style="width:{100 * a["couts_mois_eur"] / total:.0f}%"></span></div>'
        note = ""
        cout_jour = a["couts_mois_eur"] / jours_mois if a["couts_mois_eur"] else None
    else:
        classe, titre, valeur = "heros neutre", "Revenus du mois", euros(a["revenus_mois_eur"])
        details = (f'<div>Encaissé (agents)<b>{e(euros(a["usdc_mois"] / taux))}</b></div><div>Abonnements<b>{e(euros(a["a_facturer_eur"]))}</b></div>'
                   f'<div>Prévision fin de mois<b>{e(euros(a["prevision_revenus_eur"]))}</b></div>')
        jauge, cout_jour = "", None
        note = '<p class="vide">Le résultat (revenus moins coûts) s\'affichera dès que les coûts seront renseignés (COST_* dans .env).</p>'
    abos = ", ".join(f"{n} {accounts.PLAN_LIBELLES[p]}" for p, n in a["abonnements"].items() if n) or "aucun abonné"
    parts = si["part_sources"]
    sous_visites = " · ".join(f"{fr(100 * parts[n])} % depuis {n}" for n in ("Google", "ChatGPT") if parts.get(n)) or "visiteurs humains, robots à part"
    bento = (
        f'<section class="bento"><article class="carte {classe}" style="--i:0"><p class="lbl">{ico("argent", 16)}{e(titre)}</p>'
        f'<div class="val">{e(valeur)}</div>{note}<div class="repart">{details}</div>{jauge}'
        f'{courbe("mois", courbe_eur, moment, cout_jour, axes=False)}</article>'
        f'<article class="carte" style="--i:1;color:#2fd4c4"><p class="lbl">{ico("argent", 16)}Encaissé · {e(libelle)}</p>'
        f'<div class="val" style="color:var(--tx)">{e(fr(a["usdc_periode"], 2))}<small>USDC</small></div>'
        f'<div class="sous">≈ {e(euros(a["eur_periode"]))} · {e(fr(a["paiements_periode"]))} paiement{"s" if a["paiements_periode"] != 1 else ""} {delta(a["evolution_usdc"])}</div>{spark(a["courbe_usdc"])}</article>'
        f'<article class="carte" style="--i:2;color:#5b7cff"><p class="lbl">{ico("clients", 16)}Abonnements</p>'
        f'<div class="val" style="color:var(--tx)">{e(fr(a["a_facturer_eur"]))}<small>€ / mois</small></div><div class="sous">{e(abos)} · à facturer</div></article>'
        f'<article class="carte" style="--i:3;color:#ff8a3d"><p class="lbl">{ico("api", 16)}Appels API · {e(libelle)}</p>'
        f'<div class="val" style="color:var(--tx)">{e(fr(api["total"]))}</div><div class="sous">{delta(api["evolution"])} '
        f'{e("" if api["taux_reussite"] is None else fr(100 * api["taux_reussite"]) + " % réussis")}</div>{spark(api["courbe_appels"])}</article>'
        f'<article class="carte" style="--i:4;color:#a78bfa"><p class="lbl">{ico("site", 16)}Visites · {e(libelle)}</p>'
        f'<div class="val" style="color:var(--tx)">{e(fr(si["total"]))}</div><div class="sous">{delta(si["evolution"])} {e(sous_visites)}</div>{spark(si["courbe"])}</article></section>'
    )

    argent = (f'<section class="sec" id="argent"><div class="sec-h"><span class="pastille">{ico("argent")}</span><h2>Argent</h2>'
              f'<p>USDC converti à 1 € = {e(fr(taux, 2))} $ ({e(i18n.DATE_TAUX)}) · abonnements à facturer, pas encaissés automatiquement</p></div>'
              f'<div class="duo"><article class="carte"><h3>Revenus{" et coûts" if cout_jour else ""}, jour par jour</h3><p class="aide">Encaissé des agents, en euros. Survolez la courbe pour lire chaque jour.</p>'
              f'{courbe("argent", courbe_eur, moment, cout_jour)}<div class="legende"><span><i style="background:linear-gradient(90deg,#5b7cff,#2fd4c4)"></i>Revenus</span>'
              f'{"<span><i style=background:#ff8a3d></i>Coûts par jour</span>" if cout_jour else ""}</div></article>'
              f'<article class="carte"><h3>Ce que rapporte chaque pays</h3><p class="aide">Paiements des agents et abonnements, en euros.</p>'
              f'{liste_pays(a["par_pays_eur"], "€", "Pas encore de revenu rattaché à un pays.")}</article></div></section>')

    statuts = api["statuts"]
    erreurs = statuts.get("client", 0) + statuts.get("serveur", 0)
    api_html = (f'<section class="sec" id="api"><div class="sec-h"><span class="pastille">{ico("api")}</span><h2>API</h2><p>Appels de /validate, du serveur MCP et de l\'essai gratuit</p></div>'
                f'<div class="quad"><article class="carte"><p class="lbl">Réussite</p><div class="rings">{anneau(api["taux_reussite"], "#34d399")}<div class="sous">Appels servis sans erreur · {e(fr(erreurs))} erreur{"s" if erreurs != 1 else ""}</div></div></article>'
                f'<article class="carte"><p class="lbl">Cache</p><div class="rings">{anneau(api["taux_cache"], "#2fd4c4")}<div class="sous">{e("Économie : " + euros(api["economie_cache_eur"]) if api["economie_cache_eur"] is not None else "Vérifications évitées au fournisseur")}</div></div></article>'
                f'<article class="carte"><p class="lbl">{ico("horloge", 16)}Temps de réponse</p><div class="val">{e("—" if api["duree_moyenne_ms"] is None else fr(api["duree_moyenne_ms"]))}<small>ms</small></div>'
                f'<div class="sous">{e("" if api["duree_p95_ms"] is None else "95 % des appels sous " + fr(api["duree_p95_ms"]) + " ms")}</div></article>'
                f'<article class="carte"><p class="lbl">{ico("bouclier", 16)}Refusés sans paiement</p><div class="val">{e(fr(statuts.get("paiement", 0)))}</div><div class="sous">agents qui ont trouvé le service sans payer : vos prospects</div></article></div>'
                f'<div class="duo" style="margin-top:20px"><article class="carte"><h3>Appels par jour</h3><p class="aide">Tous les appels, sur 30 jours.</p>{barres_jour(api["courbe_appels"])}</article>'
                f'<article class="carte"><h3>Par où arrivent les appels</h3><p class="aide">Canal de chaque appel sur la période.</p>{barres(api["canaux"], "Aucun appel sur la période.")}</article></div>'
                f'<div class="duo" style="margin-top:20px"><article class="carte"><h3>Pays d\'où viennent les appels</h3><p class="aide">Pays de connexion (un agent tourne souvent dans un centre de données).</p>{liste_pays(api["pays_appels"], "", "Aucun appel sur la période.")}</article>'
                f'<article class="carte"><h3>Pays des adresses vérifiées</h3><p class="aide">Le pays écrit dans l\'adresse.</p>{liste_pays([(c, n) for c, n in api["pays_adresses"] if c != "??"], "", "Aucune adresse vérifiée sur la période.")}</article></div></section>')

    etapes = d["parcours"]
    plus_grand = max([n for _, n in etapes] + [1])
    entonnoir = "".join(
        f'<div class="etape"><div class="n">{e(fr(n))}</div><div class="t">{e(nom)}</div>'
        f'<span class="taux">{e("point de départ" if k == 0 else ("—" if not etapes[k - 1][1] else fr(100 * n / etapes[k - 1][1]) + " % de l’étape d’avant"))}</span>'
        f'<div class="bar" style="height:{max(6, round(96 * n / plus_grand))}px;animation-delay:{k / 10}s"></div></div>'
        for k, (nom, n) in enumerate(etapes))
    fin_essai = [(e(nom), f'<span class="badge {"chaud" if (fin - moment).days < 2 else "tiede"}">dans {e(_jours((fin - moment).total_seconds() / 86400))}</span>') for nom, fin in cl["essais_finissant"]]
    quota = [(f'{e(nom)}<div class="mini"><span style="width:{min(100, round(100 * c / q))}%"></span></div>', e(f"{fr(c)} / {fr(q)}")) for nom, _p, c, q in cl["proches_quota"]]
    relance = [(e(nom), f'<span class="badge froid">inscrit il y a {e(_jours((moment - cree).total_seconds() / 86400)) if cree else "?"}</span>') for nom, cree in cl["jamais_appele"][:6]]
    top = [(e(nom), e(f"{fr(c)} validations")) for nom, _p, c in cl["top"]]
    payants = cl["payants"]
    clients_html = (f'<section class="sec" id="clients"><div class="sec-h"><span class="pastille">{ico("clients")}</span><h2>Clients</h2>'
                    f'<p>{e(cl["total"])} comptes · {e(sum(payants.values()))} payant{"s" if sum(payants.values()) != 1 else ""} · {e(cl["essais_en_cours"])} essai{"s" if cl["essais_en_cours"] != 1 else ""} en cours</p></div>'
                    f'<article class="carte"><h3>Le parcours, du visiteur au client</h3><p class="aide">{e(libelle)}, tous pays confondus.</p><div class="funnel">{entonnoir}</div></article>'
                    f'<div class="quad" style="margin-top:20px">'
                    f'<article class="carte"><h3>Essais qui finissent bientôt</h3><p class="aide">À contacter avant la fin.</p>{tableau(fin_essai, "Aucun essai ne finit dans les 3 jours.")}</article>'
                    f'<article class="carte"><h3>Presque au bout du quota</h3><p class="aide">Bons candidats pour l\'offre au-dessus.</p>{tableau(quota, "Personne n’est proche de son quota.")}</article>'
                    f'<article class="carte"><h3>À relancer</h3><p class="aide">Inscrits qui n\'ont jamais appelé l\'API.</p>{tableau(relance, "Tous les inscrits ont déjà appelé l’API.")}</article>'
                    f'<article class="carte"><h3>Les plus actifs ce mois</h3><p class="aide">Validations servies par clé.</p>{tableau(top, "Aucune validation par clé ce mois-ci.")}</article></div></section>')

    robots = "".join(f'<span class="robot">{ico("robot", 16)}{e(nom)} <b>{e(fr(n))}</b></span>' for nom, n in si["robots"][:10]) or '<p class="vide">Aucun robot de moteur sur la période.</p>'
    site_html = (f'<section class="sec" id="site"><div class="sec-h"><span class="pastille">{ico("site")}</span><h2>Site et SEO</h2><p>Visites humaines ; les robots des moteurs et des IA sont comptés à part</p></div>'
                 f'<article class="carte"><h3>D\'où viennent vos visiteurs</h3><p class="aide">Chaque point est une zone du monde ; plus il brille, plus il y a de visites. Survolez un pays.</p>'
                 f'<div class="monde">{carte_monde(si["pays"])}{liste_pays(si["pays"], "", "Pas encore de visite sur la période.")}</div></article>'
                 f'<div class="trio" style="margin-top:20px"><article class="carte"><h3>Sources des visites</h3><p class="aide">Le site d\'où arrive chaque visiteur.</p>{barres(si["sources"], "Pas encore de visite sur la période.")}</article>'
                 f'<article class="carte"><h3>Robots des moteurs et des IA</h3><p class="aide">Ils lisent le site pour le citer.</p><div class="robots">{robots}</div></article>'
                 f'<article class="carte"><h3>Pages les plus vues</h3><p class="aide">Toutes langues confondues.</p>{barres(si["pages"], "Pas encore de visite sur la période.")}</article></div></section>')

    corps = f'<section class="alertes">{bloc_alertes}</section>{bento}{argent}{api_html}{clients_html}{site_html}{registre(stats)}'
    return _document(cote + f"<main>{haut}{corps}{_pied()}</main>")


def _pied() -> str:
    return ("<footer>Pays : IP Geolocation by DB-IP (db-ip.com), licence CC BY 4.0. Carte : Natural Earth (domaine public). "
            "Aucune adresse IP n'est gardée. Les essais de recette (D40) sont exclus du revenu réel.</footer>")


def _document(contenu: str) -> str:
    return (f'<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex, nofollow"><meta http-equiv="refresh" content="60"><title>Pilotage · Address Validator</title>'
            f'<style>{CSS}</style></head><body><div class="app">{contenu}</div></body></html>')


CSS_CONNEXION = """
body{display:grid;place-items:center;min-height:100vh;padding:24px;overflow:hidden}
.fond{position:fixed;inset:0;z-index:-1;width:100%;height:100%;opacity:.5}
.boite{width:min(100%,420px);padding:36px 32px;border-radius:24px;background:linear-gradient(180deg,rgba(16,28,56,.85),rgba(10,19,38,.85));border:1px solid var(--ln2);box-shadow:0 30px 80px -30px rgba(0,0,0,.8);backdrop-filter:blur(16px);animation:entre .8s cubic-bezier(.2,.75,.25,1) both;position:relative}
.boite::before{content:"";position:absolute;inset:-1px;border-radius:inherit;padding:1px;background:linear-gradient(140deg,var(--c1),transparent 35%,transparent 65%,var(--c3));-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;opacity:.7;pointer-events:none}
.boite .logo{padding:0 0 26px}.boite h1{font-size:26px;margin:0 0 6px}.boite>p{color:var(--mu);margin:0 0 26px;font-size:14px}
label{display:block;margin:0 0 8px;font-size:13px;color:#c9d4ee}
input{width:100%;height:48px;padding:0 14px;margin:0 0 18px;border-radius:12px;border:1px solid var(--ln2);background:rgba(5,10,22,.7);color:var(--tx);font:inherit;font-size:16px;transition:border-color .2s,box-shadow .2s}
input:focus{outline:none;border-color:var(--c3);box-shadow:0 0 0 4px rgba(47,212,196,.15)}
button{width:100%;height:50px;margin-top:6px;border:0;border-radius:12px;cursor:pointer;color:#06111f;font:inherit;font-weight:700;font-size:15.5px;background:linear-gradient(90deg,var(--c1),#ff6fb0 30%,var(--c2) 65%,var(--c3));background-size:200% 100%;animation:onde 6s ease-in-out infinite;box-shadow:0 10px 30px -10px rgba(91,124,255,.7);transition:transform .15s}
button:hover{transform:translateY(-1px)}button:focus-visible{outline:2px solid #fff;outline-offset:3px}
.erreur{display:flex;gap:10px;align-items:center;margin:0 0 18px;padding:11px 14px;border-radius:12px;background:rgba(251,113,133,.1);border:1px solid rgba(251,113,133,.35);color:#ffd1d9;font-size:14px}
.note{display:flex;align-items:center;gap:8px;margin-top:20px;color:var(--fa);font-size:12.5px}.note .ico{color:var(--c3)}
"""


def connexion(erreur: str = "", identifiant: str = "", essais: int = 5, minutes: int = 15) -> str:
    """La page de connexion du pilotage (D69), à la place de la fenêtre grise du navigateur."""
    fond = carte_monde([]).replace('class="map"', 'class="fond" preserveAspectRatio="xMidYMid slice"', 1).replace('role="img" aria-label="Visites par pays"', 'aria-hidden="true"', 1)
    alerte = f'<p class="erreur" role="alert">{ico("alerte", 18)}{e(erreur)}</p>' if erreur else ""
    return (f'<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex, nofollow"><title>Connexion · Pilotage</title><style>{CSS}{CSS_CONNEXION}</style></head><body>{fond}'
            f'<form class="boite" method="post" action="/dashboard/connexion"><div class="logo"><span class="orbe"></span><div><b>Address Validator</b><small>Pilotage</small></div></div>'
            f'<h1>Accès réservé</h1><p>Le tableau de bord de l\'exploitation. Entrez vos identifiants.</p>{alerte}'
            f'<label for="u">Identifiant</label><input id="u" name="user" value="{e(identifiant)}" autocomplete="username" required autofocus>'
            f'<label for="p">Mot de passe</label><input id="p" name="password" type="password" autocomplete="current-password" required>'
            f'<button type="submit">Se connecter</button><div class="note">{ico("cadenas", 15)}Connexion chiffrée · {e(essais)} essais, puis {e(minutes)} minutes de pause</div></form></body></html>')
