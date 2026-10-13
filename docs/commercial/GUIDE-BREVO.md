# Guide Brevo — envoyer depuis `address-validator@kder-ai.com`

Brevo (ex-Sendinblue) sert de **relais SMTP** au script
`scripts/envoyer_email_tiitoune.py` : Zoho reçoit le courrier, Brevo le fait
partir (Zoho gratuit ne donne pas de SMTP sortant fiable pour ce volume).

| Question | Réponse |
|----------|---------|
| **Qui** | l'exploitant (Kder), depuis un navigateur puis PowerShell |
| **Quoi** | compte Brevo, expéditeur vérifié, clé SMTP, variables d'environnement, domaine authentifié (DKIM) |
| **Comment** | interface Brevo pour les clés, Namecheap pour les enregistrements, `.env` / variables pour le script |
| **Quand** | après `DNS-A-CONFIGURER.md` (les MX et le SPF d'abord) |
| **Où** | <https://www.brevo.com/>, `app.brevo.com`, Namecheap, `.env` du poste |
| **Fait quoi exactement** | fait accepter par Brevo les emails du script au nom de `kder-ai.com`, sans qu'ils tombent en spam |

> ⚠️ `GUIDE-BREVO.md` ne contient **aucun secret** : les clés se collent dans le
> `.env` (ignoré par git) ou dans les variables d'environnement de la session.

---

## 1. Créer le compte Brevo

1. Aller sur <https://www.brevo.com/> → **Sign up free**.
2. Renseigner : email de contact (utiliser `address-validator@kder-ai.com`, pas
   une adresse Gmail — c'est l'adresse qui recevra les alertes de compte),
   prénom/nom, entreprise (« Address Validator »), pays France.
3. Confirmer l'email de bienvenue (lien reçu dans la boîte Zoho).
4. Vérifier le plafond affiché dans le compte : l'offre gratuite Brevo est de
   **300 emails/jour**, largement suffisant (une campagne Tiitoune = quelques
   dizaines de messages). *Contrôler le chiffre à l'inscription : c'est un
   chiffre commercial, il peut changer.*
5. Renseigner la société dans **Senders, Domains & Dedicated IPs → Domains**
   (§ 6) : l'authentification du domaine est ce qui évite le dossier spam.

---

## 2. Vérifier l'adresse d'expéditeur

1. `app.brevo.com` → **Senders, Domains & Dedicated IPs** → onglet **Senders**
   → **Add a sender**.
2. Renseigner exactement :
   - **From name** : `Kder Daoud - Address Validator`
   - **From email** : `address-validator@kder-ai.com`
3. Brevo envoie un **code de confirmation** à cette adresse (elle existe dans
   Zoho, donc elle le reçoit) : ouvrir le message et saisir le code dans Brevo.
4. Tant que l'adresse n'est pas confirmée, Brevo refuse tout envoi avec elle
   (erreur côté API : expéditeur non valide).

> L'email de bienvenue Tiitoune doit arriver **en réponse au prospect** :
> vérifier dans la fiche d'envoi que `SMTP_REPLY_TO` (facultatif) est bien
> `address-validator@kder-ai.com`, pour que le prospect qui clique « Répondre »
> écrive à une adresse relevée.

---

## 3. Récupérer la clé SMTP — et **pas** la clé API

1. `app.brevo.com` → **SMTP & API** (menu du compte) ou directement
   <https://app.brevo.com/settings/keys/smtp>.
2. Rester sur l'onglet **SMTP** (l'onglet **API** affiche une clé
   `xkeysib-…` qui **ne fonctionne pas** en SMTP).
3. **Generate a new SMTP key** (nom : `address-validator-poste`).
4. Noter les **deux** valeurs, affichées une seule fois :
   - **Login** : de la forme `XXXXXXX@smtp-brevo.com` → variable `SMTP_USER` ;
   - **Clé SMTP** : de la forme `xsmtpsib-…` → variable `SMTP_PASSWORD`.
5. La clé API n'est pas nécessaire ici : le script parle SMTP, pas l'API REST.

Sources : documentation officielle Brevo, *SMTP relay integration* —
« Use an SMTP key, not an API key, for SMTP relay connections. »
(<https://developers.brevo.com/docs/smtp-integration>)

---

## 4. Variables d'environnement à configurer

Depuis le 13/10/2026, le script charge d'abord `address-validator/.env.smtp` s'il
existe — fichier ignoré par Git (`.gitignore`, motif `.env.*`), analysé par
`scripts/charger_env_smtp.py` (lignes `CLE=VALEUR`, `#` en début de ligne pour un
commentaire). **C'est le mode recommandé** : la clé ne passe ni par l'historique du
terminal, ni par un fichier du dépôt. Le fichier est livré avec un texte à
remplacer : tant que `SMTP_PASSWORD` contient `REMPLACER`, l'envoi réel est refusé,
et `scripts/test_smtp_connection.py` (section 8) le dit avant tout essai.

```text
# address-validator/.env.smtp — ne jamais committer (ignoré : motif `.env.*`)
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USER=XXXXXXX@smtp-brevo.com                # le login Brevo, PAS l'adresse du domaine
SMTP_PASSWORD=REMPLACER_PAR_TA_CLE_SMTP_BREVO   # à remplacer par la clé xsmtpsib-…
SMTP_FROM=address-validator@kder-ai.com
SMTP_FROM_NAME=Address Validator
```

Sans ce fichier, le script lit l'environnement du processus (`os.environ`) comme
avant : poser alors les variables **dans la session PowerShell** avant de lancer la
commande, ou les rendre permanentes (fin de section). Quand le fichier est là, sa
valeur fait foi : l'écrasement d'une variable d'environnement est **affiché** au
démarrage, jamais silencieux.

```powershell
$env:SMTP_HOST = "smtp-relay.brevo.com"
$env:SMTP_PORT = "587"
$env:SMTP_USER = "ton-login@smtp-brevo.com"   # le login Brevo, PAS l'adresse du domaine
$env:SMTP_PASSWORD = "ta-cle-smtp-brevo"      # xsmtpsib-… (clé SMTP, jamais la clé API)
$env:SMTP_FROM = "address-validator@kder-ai.com"
$env:SMTP_FROM_NAME = "Kder Daoud - Address Validator"
```

| Variable | Obligatoire | Valeur |
|----------|-------------|--------|
| `SMTP_HOST` | oui | `smtp-relay.brevo.com` |
| `SMTP_PORT` | non (défaut `587`) | `587` |
| `SMTP_USER` | oui | `<login>@smtp-brevo.com` — le login Brevo, **pas** l'adresse du domaine (`SMTP_USERNAME` est également accepté par le script) |
| `SMTP_PASSWORD` | oui | la clé SMTP `xsmtpsib-…` |
| `SMTP_FROM` | non | `address-validator@kder-ai.com` (défaut du script : `SMTP_USER`) |
| `SMTP_FROM_NAME` | non | `Kder Daoud - Address Validator` |
| `SMTP_REPLY_TO` | non | `address-validator@kder-ai.com` (les réponses du prospect repartent vers une adresse relevée) |

Pour les rendre **permanentes** (session suivante incluse) :

```powershell
[Environment]::SetEnvironmentVariable("SMTP_HOST", "smtp-relay.brevo.com", "User")
# … une ligne par variable ; le mot de passe SMTP reste dans le stockage
# sécurisé de Windows, jamais dans un fichier du dépôt.
```

Rappel projet (`.clinerules`) : **aucun** secret en clair dans un fichier du
dépôt — ni dans un `.md`, ni dans un `.py`, ni dans `.env.example`. Le `.env`
réel est ignoré par git.

---

## 5. Réglages de connexion (documentation officielle Brevo)

| Paramètre | Valeur |
|-----------|--------|
| Hôte | `smtp-relay.brevo.com` |
| Port | `587` (ou `2525`) — en clair ; `465` si SSL/TLS |
| Chiffrement | vide en `587` ; **obligatoire** en `465` |
| Utilisateur | le login `…@smtp-brevo.com` |
| Mot de passe | la clé SMTP `xsmtpsib-…` |

Le script utilise `SMTP_PORT` (587 par défaut) : rien d'autre à régler.

---

## 6. Authentifier le domaine `kder-ai.com` (DKIM Brevo)

1. `app.brevo.com` → **Senders, Domains & Dedicated IPs** → onglet **Domains**
   → **Add a domain** → `kder-ai.com`.
2. Brevo affiche **2 à 3 enregistrements** à coller dans Namecheap (*Advanced
   DNS*) :
   - un **TXT SPF** contenant `include:spf.brevo.com` — **déjà posé** par
     `DNS-A-CONFIGURER.md` § 3 ligne 4 : vérifier que la valeur correspond, et
     s'il en propose un autre texte, garder **une seule** ligne SPF ;
   - un ou deux **CNAME DKIM** (hôtes du type `brevo._domainkey` /
     `mail._domainkey`) dont la cible est propre à ton compte : les copier
     **tels qu'affichés**, jamais les deviner ;
   - éventuellement un **TXT DMARC** : déjà posé (§ 3 ligne 7) — pas de doublon
     `_dmarc`.
3. Cliquer **Verify / Authenticate** : Brevo contrôle le DNS, ce qui peut
   prendre de quelques minutes à 1 h après les changements Namecheap.
4. Erreurs Brevo possibles : `DNS records not properly configured`
   (enregistrement absent ou mal collé), `DMARC policy requires domain
   authentication` (DKIM non conforme alors que le DMARC est en place),
   `Domain already exists in your account` (domaine déjà ajouté).

Sans domaine authentifié, Brevo envoie quand même (SPF seul) : les messages
perdent DKIM et partent plus volontiers en spam. C'est l'étape à ne pas sauter
avant la campagne Tiitoune Art.

---

## 7. Pièges à éviter

1. **La clé API au lieu de la clé SMTP.** `xkeysib-…` (onglet API) ne
   s'authentifie pas en SMTP : il faut `xsmtpsib-…` (onglet SMTP) **et** le
   login `…@smtp-brevo.com`.
2. **Confondre `SMTP_USER` et l'adresse d'expédition** : `SMTP_USER` = login
   Brevo ; `SMTP_FROM` = `address-validator@kder-ai.com`.
3. **Oublier `include:spf.brevo.com`** dans le TXT SPF : les envois du script
   échouent au contrôle SPF chez les destinataires (et le DMARC `fo=1` le
   signalera par des rapports d'échec).
4. **Deux enregistrements SPF** : les fusionner en une seule ligne — deux
   `v=spf1` donnent `permerror`, donc plus aucune authentification.
5. **Envoyer avant d'avoir confirmé l'expéditeur** (§ 2) : Brevo refuse
   l'envoi.
6. **Mauvais port** : `587` sans chiffrement explicite, `465` avec SSL/TLS. Un
   `465` sans SSL reste bloqué.
7. **Vérifier le DNS trop tôt** : lancer `verifier_dns_kder.py` avant que Brevo
   ne voie les CNAME, c'est diagnostiquer un problème qui n'existe pas.
8. **Mettre la clé dans le dépôt** : ni dans un `.md`, ni dans un `.py`, ni
   dans `.env.example`.
9. **Expéditeur sur un domaine public** (Gmail, Outlook) : le script refuse
   d'envoyer sans `--accepter-domaine-public`, et il a raison — la délivrabilité
   s'écroule, pour un gain nul.

---

## 8. Tester, puis envoyer

```powershell
# 0. Poser les identifiants (§ 4 : .env.smtp, ou variables d'environnement),
#    puis vérifier le DNS
.venv\Scripts\python.exe scripts\verifier_dns_kder.py

# 1. Contrôler la connexion SMTP seule : STARTTLS + authentification, sans email.
#    Un mot de passe encore marqué REMPLACER s'arrête ici, sans ouvrir de session.
.venv\Scripts\python.exe scripts\test_smtp_connection.py

# 2. Aperçu sans aucune connexion réseau (mode par défaut)
.venv\Scripts\python.exe scripts\envoyer_email_tiitoune.py --dry-run

# 3. Envoi d'une seule copie de test, sans toucher au CRM
.venv\Scripts\python.exe scripts\envoyer_email_tiitoune.py --test --vers address-validator@kder-ai.com

# 4. Envoi réel au prospect (met le CRM à jour)
.venv\Scripts\python.exe scripts\envoyer_email_tiitoune.py --send
```

Codes de retour : `0` réussi (ou simulation), `1` envoi impossible, `2` usage
invalide. Pour `test_smtp_connection.py` : `0` la session s'ouvre et
s'authentifie, `1` sinon (fichier absent, valeur encore à remplacer, refus du
serveur ou du domaine).

Contrôle final, dans la boîte Zoho : ouvrir le message de test → **Autres
options → Afficher l'original** et vérifier `Authentication-Results` :
`spf=pass`, `dkim=pass`, `dmarc=pass`. Tant qu'un des trois n'est pas `pass`, ne
pas lancer l'envoi réel.
