# Guide d'envoi d'email — Address Validator

État au 13/10/2026. Ce document dit **comment envoyer l'email Tiitoune Art**, de
l'ouverture de la boîte professionnelle jusqu'au message parti. Le contenu du message
n'est pas ici : il vit dans
[`docs/commercial/checklist-tiitoune-art.md`](checklist-tiitoune-art.md) § 2, et le
script le relit à chaque exécution — il ne recopie rien.

| Qui | Quoi | Comment | Quand | Où | Fait quoi exactement |
|---|---|---|---|---|---|
| `scripts/envoyer_email_tiitoune.py`, lancé par Kder | envoie l'email de bienvenue | SMTP direct (`smtplib` : STARTTLS sur 587, TLS implicite sur 465) | le jour J, après contrôle des 5 liens | `address-validator/scripts/` + `docs/commercial/checklist-tiitoune-art.md` | relit objet, pré-en-tête et corps dans la checklist, envoie, puis marque la fiche `Tiitoune Art` en `envoye` dans le CRM |

Trois commandes, dans cet ordre : simulation → essai vers soi-même → envoi réel.
Rien ne part sans `--send`.

## 1. Configuration SMTP (Zoho Mail)

### 1.1 Créer le compte Zoho
1. Aller sur https://www.zoho.com/mail/
2. Cliquer sur « Sign Up Now ».
3. Choisir « Forever Free Plan » (gratuit, sans carte bancaire).
4. Ajouter le domaine `kder-ai.com`.
5. Vérifier la propriété du domaine avec l'enregistrement **TXT** fourni par Zoho
   (à coller dans Namecheap, voir § 1.2).
6. Créer l'utilisateur `contact@kder-ai.com`.

⚠️ **Limite vérifiée le 13/10/2026** (page tarifs officielle : « Up to 5 users, 5 GB
mail storage per user, custom email for one domain, **IMAP/POP/Active Sync not
included** » ; centre d'aide Zoho : « For newly signed-up users (Free plan), the
**IMAP Access feature will not be available** ») : le plan gratuit **ne permet pas
l'envoi SMTP depuis un script** pour un compte récent. Le gratuit sert à **recevoir**
`contact@kder-ai.com` (webmail, 5 Go). Pour envoyer, voir § 7 (Zoho Lite payant, ou
relais transactionnel gratuit Brevo/Resend) : le script ne change pas, seules les
variables d'environnement changent.

### 1.2 Configurer les DNS sur Namecheap
Le domaine est chez Namecheap (`dns1`/`dns2.registrar-servers.com`), donc dans
**Domain List → Manage → Advanced DNS**. Ajouter :

| Type | Hôte | Valeur | Priorité |
|---|---|---|---|
| MX | @ | `mx.zoho.eu` | 10 |
| MX | @ | `mx2.zoho.eu` | 20 |
| MX | @ | `mx3.zoho.eu` | 50 |
| TXT | @ | `v=spf1 include:zoho.eu include:spf.brevo.com ~all` (un **seul** TXT) | — |
| TXT | @ | `zoho-verification=zbXXXXXXXX.zmverify.zoho.com` (fourni par Zoho) | — |

Le tableau complet (à supprimer, à coller, sources, checklist Namecheap) vit dans
[`docs/commercial/DNS-A-CONFIGURER.md`](DNS-A-CONFIGURER.md) § 3 ; ici, seulement ce
qu'il faut savoir pour envoyer. État DNS **relevé le 13/10/2026** : MX
`eforward1..5.registrar-servers.com` et SPF `include:spf.efwd.registrar-servers.com`
(redirection gratuite Namecheap) — donc à **remplacer**, pas à compléter ; preuve de
domaine déjà publiée (`zoho-verification=zb82668052.zmverify.zoho.com`) ; DMARC déjà
posé par Brevo (`v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com`) ; aucun DKIM.

⚠️ **À remplacer, pas à ajouter.** Le domaine reçoit aujourd'hui son courrier par la
redirection gratuite de Namecheap (relevé le 13/10/2026 : MX `eforward1..5.registrar-servers.com`,
SPF `include:spf.efwd.registrar-servers.com`). Ces cinq MX et ce SPF doivent être
**supprimés** : un domaine n'a qu'un jeu de MX et **un seul** enregistrement SPF. Les
alias existants (`contact@…` et les autres) sont à recréer dans Zoho, sinon le
courrier qui leur est adressé part en erreur.

Les enregistrements exacts sont affichés dans Zoho → **Email > Domaines >
vérification** (datacenter `.eu` ou `.com` selon le compte) : ce sont eux qui font foi.

Puis, toujours dans Zoho, **DKIM** (Email > Domaines > DKIM) : coller le TXT/CNAME
fourni dans Advanced DNS. Vérification après propagation :

```powershell
# le script du dépôt contrôle MX + SPF + preuve + DKIM + DMARC, et nomme ce qui manque
address-validator\.venv\Scripts\python.exe address-validator\scripts\verifier_dns_kder.py
Resolve-DnsName -Name 'zmail._domainkey.kder-ai.com' -Type TXT
Resolve-DnsName -Name 'kder-ai.com' -Type MX
```

**DMARC** : il est déjà en place, posé par Brevo —
`v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com` (relevé sur `_dmarc.kder-ai.com`
le 13/10/2026). C'est une politique valable (`p=none` + adresse de rapport) : lire les
rapports avant de durcir en `p=quarantine`. Mettre une boîte du domaine à la place de
`rua@dmarc.brevo.com` est possible, mais fait perdre l'agrégation faite par Brevo.

### 1.3 Récupérer les identifiants SMTP
Dans **Zoho Mail > Settings > Mail Accounts** (section SMTP / IMAP), ou dans
**Server Configurations Details** à l'intérieur du compte :

| Réglage | Valeur documentée par Zoho |
|---|---|
| Hôte | `smtp.zoho.com` (organisation gratuite) / `smtppro.zoho.com` (payant) / `smtp.zoho.eu` (datacenter européen) |
| Port | `587` + STARTTLS (ce que fait le script) ou `465` + TLS implicite |
| Chiffrement | TLS (obligatoire) |
| Utilisateur | `contact@kder-ai.com` |
| Mot de passe | mot de passe du compte, ou **mot de passe d'application** (obligatoire si la validation en deux étapes est activée) |

Sonde du 13/10/2026 depuis ce poste : `smtp.zoho.eu:587` répond EHLO 250, accepte
**STARTTLS** et annonce `AUTH=LOGIN PLAIN` — le chemin exact du script est joignable
avant même de créer le compte. Même résultat pour `smtp-relay.brevo.com:587` et
`smtp.resend.com:587`.

## 2. Variables d'environnement

Le mot de passe ne peut venir que de l'environnement : un argument de ligne de
commande finirait dans l'historique du terminal, un fichier du dépôt finirait dans Git.

```powershell
$env:SMTP_HOST = "smtp.zoho.eu"
$env:SMTP_PORT = "587"
$env:SMTP_USER = "contact@kder-ai.com"
$env:SMTP_PASSWORD = "LE_MOT_DE_PASSE_ZOHO"
$env:SMTP_FROM = "contact@kder-ai.com"
$env:SMTP_FROM_NAME = "Kder Daoud"
```

| Variable | Requis | Rôle | Défaut |
|---|---|---|---|
| `SMTP_HOST` | oui | serveur SMTP | — |
| `SMTP_USER` (ou `SMTP_USERNAME`) | oui | identifiant de connexion | — |
| `SMTP_PASSWORD` | oui | mot de passe, ou mot de passe d'application | — |
| `SMTP_PORT` | non | `587` → STARTTLS ; `465` → TLS implicite | `587` |
| `SMTP_FROM` | non | adresse d'expédition | `SMTP_USER` |
| `SMTP_FROM_NAME` | non | nom affiché du signataire | `Kder - Address Validator` |
| `SMTP_REPLY_TO` | non | adresse des réponses (et du `List-Unsubscribe`) | `SMTP_FROM` |

Ces variables ne vivent que dans la fenêtre PowerShell courante : la fermer les
oublie, et c'est voulu. Pour les garder (poste personnel) :
`[Environment]::SetEnvironmentVariable("SMTP_HOST","smtp.zoho.eu","User")` — elles
sont alors stockées en clair dans le profil Windows, jamais dans le dépôt.

**Depuis le 13/10/2026, le script lit d'abord `address-validator/.env.smtp`** —
fichier ignoré par Git, analysé par `address-validator/scripts/charger_env_smtp.py`
(modèle et procédure : `docs/commercial/GUIDE-BREVO.md` § 4). La clé SMTP y vit
alors au lieu de la fenêtre PowerShell, et le fichier fait foi : un écrasement de
variable d'environnement est affiché au démarrage. Contrôle de la connexion, avant
tout envoi :

```powershell
address-validator\.venv\Scripts\python.exe address-validator\scripts\test_smtp_connection.py
```

Le script **refuse** un expéditeur grand public (`gmail.com`, `orange.fr`,
`wanadoo.fr`, `outlook.fr`… — liste dans `prospection/scripts/envoyer_smtp.py`) :
`SMTP_FROM` doit être une adresse du domaine, d'où le § 1. L'option
`--accepter-domaine-public` existe, mais elle abîme la délivrabilité du domaine : ne
pas l'utiliser pour un premier contact commercial.

## 3. Vérifier avant d'envoyer

### 3.1 Les 5 liens dont l'email dépend

```powershell
address-validator\.venv\Scripts\python.exe address-validator\scripts\envoyer_email_tiitoune.py --dry-run --verifier-liens
```

Attendu : `200` `/pricing`, `200` `/signup`, **`401` `/compte`**, `200`
`/validate-form`, `200` `/guide`. Mesures du 13/10/2026 : 12 685 o, 6 845 o, 124 o,
7 026 o, 23 049 o (checklist § 1). Un `200` sur `/compte` serait une **régression de
confidentialité** ; un `404` sur une des quatre autres pages signifie que le
déploiement n'a pas été fait → **ne pas envoyer**. En envoi réel, le script refait ce
contrôle de lui-même et **bloque** le départ si une page ne répond pas comme attendu
(`ENVOI FAIL les pages de l'email ne repondent pas comme attendu`).

### 3.2 Relire le message tel qu'il partira

La simulation est le mode par défaut (aucune option) : elle imprime l'objet, le
pré-en-tête, le corps complet et la fiche visée. Rien n'est envoyé, aucune connexion
n'est ouverte, le CRM n'est pas touché. Le script lit le texte dans la checklist § 2
et **avertit** si le corps diffère de `address-validator/EMAIL_BIENVENUE.md` § 2
(garde-fou D49). L'email du 13/10/2026 est aligné sur la décision D52
(« … un colis à réexpédier et une cliente qui attend … »).

L'avertissement est donc **attendu** aujourd'hui, et il est mesurable : au 13/10/2026,
le seul écart entre les deux textes est cette phrase de la décision D52 — la checklist
dit « un colis à réexpédier et une cliente qui attend », l'exemplaire de référence dit
encore « sur une pièce fragile […] un client qui attend ». **C'est la checklist qui
part** (elle fait foi) ; l'avertissement disparaît en réalignant `EMAIL_BIENVENUE.md`
§ 2 sur D52.

### 3.3 Essai réel vers soi-même

```powershell
address-validator\.venv\Scripts\python.exe address-validator\scripts\envoyer_email_tiitoune.py --test --vers contact@kder-ai.com
```

Une seule copie, objet préfixé `[TEST]`, **CRM non modifié** : c'est la commande qui
valide les identifiants SMTP et la remise sans « brûler » le prospect.

## 4. Envoi réel (le jour J)

```powershell
address-validator\.venv\Scripts\python.exe address-validator\scripts\envoyer_email_tiitoune.py --send
```

Ce que le script fait, dans l'ordre : relit la checklist § 2 ; lit la fiche
`Tiitoune Art` dans `prospection/data/prospects.db` (base ouverte en **lecture
seule**) ; refuse si la fiche a déjà un `date_envoi` (sauf `--renvoyer`) ; envoie ;
puis marque le prospect `statut = envoye` avec `date_envoi` et un événement `intro`
(c'est cette date qui déclenche les relances J+3 / J+7 de la stratégie).

Succès attendu : `ENVOI OK destinataire=… objet="…"` suivi de la ligne du CRM.
L'option `--destinataire` n'est acceptée qu'avec la simulation — elle n'écrit jamais
dans le CRM. Autres options : `--renvoyer`, `--pre-en-tete-corps`,
`--accepter-domaine-public`, `--db`, `--checklist`.

Codes de retour : `0` message parti (ou simulation réussie), `1` envoi impossible
(configuration, base, serveur), `2` usage invalide.

## 5. Dépannage

| Message | Cause | Solution |
|---|---|---|
| `configuration SMTP absente de l'environnement : …` | variables non posées dans **cette** fenêtre PowerShell | refaire le bloc du § 2 dans la fenêtre qui lance le script |
| `expediteur grand public refuse (gmail.com)` | `SMTP_FROM`/`SMTP_USER` sur une boîte personnelle | mettre `contact@kder-ai.com` (§ 1.3) |
| `authentification refusee par <hote> (code 5xx)` | mot de passe faux ; mot de passe d'application absent (2FA) ; ou **plan gratuit sans accès IMAP/SMTP** | vérifier l'accès IMAP/SMTP du plan (§ 1.1) ; sinon Zoho Lite ou relais Brevo/Resend (§ 7) |
| `SMTPAuthenticationError` avec Brevo | **clé API** utilisée à la place de la **clé SMTP** | app.brevo.com → SMTP & API → onglet SMTP |
| `Connection unexpectedly closed` / délai dépassé | port `587` filtré par le réseau | essayer `SMTP_PORT=465` (TLS implicite) ou un autre réseau |
| `page … : 404 au lieu de 200` | déploiement absent ou incomplet | ne pas envoyer (§ 3.1) |
| `AVERTISSEMENT corps different de EMAIL_BIENVENUE.md` | écart **D52 connu** : la checklist § 2 a été recorrigée, l'exemplaire de référence § 2 ne l'a pas encore été (mesuré le 13/10/2026 : une seule phrase) | normal, tant que l'écart ne porte que sur D52 — le texte qui part est celui de la checklist ; lever l'écart en réalignant `EMAIL_BIENVENUE.md` § 2 |
| `ENVOI FAIL … a deja recu un email le …` | doublon | `--renvoyer` seulement en connaissance de cause |
| `CRM non mis a jour : …` | base absente ou verrouillée — **l'email est déjà parti** | corriger le CRM à la main : `date_envoi` conditionne les relances |

## 6. Secrets

- Aucun identifiant dans le dépôt, aucun `SMTP_PASSWORD` en argument de ligne de commande.
- Mot de passe **d'application** plutôt que le mot de passe principal : il se révoque
  d'un clic s'il fuite.
- Le script n'affiche jamais le mot de passe (il écrit seulement « present (jamais
  affiche) ») : une capture d'écran de la sortie ne le divulgue pas.
- Le message part avec un en-tête `List-Unsubscribe` (`mailto:…?subject=stop`) : une
  demande de désinscription est un usage normal, pas une erreur.

## 7. Choisir le fournisseur d'email (analyse au 13/10/2026)

Le script ne dépend que de `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` :
changer de fournisseur ne demande **aucune** modification de code.

| Critère | Zoho Mail gratuit | ImprovMX gratuit | Google Workspace Starter |
|---|---|---|---|
| Prix | 0 € | 0 € | **6,80 €/utilisateur/mois** (facturation annuelle, hors taxe ; -30 % les 3 premiers mois) |
| Recevoir `contact@kder-ai.com` | oui (webmail complet) | oui (redirige vers une boîte existante) | oui (boîte complète) |
| Envoyer en SMTP depuis un script | **non** sur un compte gratuit récent (« IMAP Access feature will not be available ») | **non** (0 envoi SMTP sur le gratuit ; 750/mois à 50 $/an ; 12 000/mois à 9 $/mois) | oui (mot de passe d'application avec validation en deux étapes) |
| Stockage | 5 Go/utilisateur | — (redirection) | 30 Go mutualisés/utilisateur |
| Capacité incluse | 5 utilisateurs, 1 domaine | 1 domaine, 25 alias | 1 utilisateur par licence |
| Mise en place | ~15 min (DNS + vérification du domaine) | ~5 min | ~15 min (DNS + propriété du domaine) |
| Verdict | ⭐⭐⭐ pour **recevoir** | ⭐ recevoir seulement (déjà en place via Namecheap) | ⭐⭐ si tu veux Gmail partout |

### 7.1 Les voies d'envoi SMTP

| Voie d'envoi | Prix | Envoi au nom de `contact@kder-ai.com` | Réglages |
|---|---|---|---|
| **Brevo** (relais transactionnel) | gratuit, jusqu'à **300 emails/jour** après validation du compte | oui | `smtp-relay.brevo.com`, port `587`, identifiants app.brevo.com → SMTP & API (**clé SMTP**, pas la clé API) |
| **Resend** | gratuit, **3 000 emails/mois** (100/jour) | oui | `smtp.resend.com`, port `587`, utilisateur `resend`, mot de passe = clé API |
| Zoho Mail Lite (payant) | à lire sur la page tarifs au moment de la souscription | oui | `smtppro.zoho.com`, port `587` |
| Google Workspace Starter | 6,80 €/utilisateur/mois | oui | `smtp.gmail.com`, port `587`, mot de passe d'application |

⚠️ **SPF : un seul enregistrement.** En ajoutant un relais d'envoi, le SPF devient
`v=spf1 include:<receveur> include:<relais> ~all` — les deux `include` dans **le même**
TXT. Deux TXT `v=spf1` cassent le SPF pour tout le domaine.

**Recommandation.** Rester à 0 € : **recevoir** avec Zoho Mail gratuit (webmail,
5 Go) et **envoyer** avec un relais transactionnel gratuit — **Brevo** (300 emails/jour,
très au-delà de la campagne) ou **Resend** (3 000/mois). Le domaine est authentifié
(SPF + DKIM + DMARC), les réponses arrivent dans la boîte `contact@kder-ai.com`, et le
script se connecte sans modification. Si tu préfères **une seule boîte** qui reçoit
*et* envoie sans bricolage, prendre Zoho Mail Lite (payant, le moins cher) ou Google
Workspace Starter (6,80 €/mois, interface Gmail connue) : là encore, seules les quatre
variables SMTP changent.

## 8. Sources et dates

Consultées le **13/10/2026** : page tarifs Zoho Mail (plan gratuit : 5 utilisateurs,
5 Go, « IMAP/POP/Active Sync not included ») ; centre d'aide Zoho « IMAP and SMTP
Configuration » (serveurs `smtp.zoho.com` / `smtppro.zoho.com` / `.eu`, ports 465 et
587, « For newly signed-up users (Free plan), the IMAP Access feature will not be
available ») ; page tarifs ImprovMX (gratuit : aucun envoi SMTP ; Premium 9 $/mois :
12 000/mois) ; page tarifs Google Workspace (Starter 6,80 €/utilisateur/mois, 30 Go) ;
page tarifs Brevo (jusqu'à 300 emails/jour sur le gratuit, relais SMTP disponible sur
toutes les offres) ; documentation SMTP Brevo (ports 587/2525 non chiffrés, 465 en
SSL/TLS, clé SMTP distincte de la clé API) ; documentation SMTP Resend
(`smtp.resend.com`, ports 25/465/587/2465/2587, utilisateur `resend`, mot de passe =
clé API). État DNS du domaine relevé le 13/10/2026 (NS Namecheap, MX eForward, SPF
eForward, **aucun DMARC**) et sonde réseau locale des trois serveurs SMTP (STARTTLS et
mécanismes AUTH annoncés). Les prix et les limites changent : la page du fournisseur
au moment de la souscription fait foi.
