# DKIM Brevo à configurer — `kder-ai.com`

Guide de **copier-coller** pour finir l'authentification du domaine d'envoi chez
Brevo. Établi le 15/10/2026 à partir du DNS public (`nslookup`) et de la
documentation Brevo.

| Question | Réponse |
|----------|---------|
| **Qui** | l'exploitant (Kder), depuis Namecheap et la console Brevo |
| **Quoi** | publier les **deux** CNAME DKIM que Brevo attribue à `kder-ai.com`, pour que Brevo signe les messages du domaine |
| **Comment** | 2 × `CNAME` (`mail._domainkey`, `brevo._domainkey`) vers des cibles propres au compte Brevo |
| **Quand** | avant la première campagne d'e-mailing ; contrôle 1 h puis 24 h après la pose (propagation) |
| **Où** | Brevo → *Senders, Domains & Dedicated IPs* → **Domains** → `kder-ai.com` → *Authenticate* ; puis Namecheap → `kder-ai.com` → **Advanced DNS** → *Host Records* |
| **Fait quoi exactement** | ajoute les deux enregistrements que Brevo réclame, puis vérifie le résultat avec `scripts/verifier_dkim_brevo.py` |

> Ce document ne contient **aucun secret**. Les cibles CNAME sont propres à ton
> compte Brevo mais elles sont **publiques** par nature (elles vivent dans le DNS) :
> les recopier ici n'expose rien. Les identifiants SMTP, eux, restent dans le
> `.env` local et n'apparaissent jamais dans le dépôt.

---

## 1. Où en est le DKIM (relevé du 15/10/2026)

Mesuré par la porte de cette tâche :

```powershell
.\.venv\Scripts\python.exe scripts\verifier_dkim_brevo.py
```

| Sélecteur | État publié le 15/10/2026 | Constat |
|-----------|---------------------------|---------|
| `mail._domainkey` | *absent* | ❌ aucun CNAME ni TXT |
| `brevo._domainkey` | *absent* | ❌ aucun CNAME ni TXT |

Verdict rendu : **MANQUANT** (0/2 sélecteur publié), code de retour `1`.

Ce qui est **déjà** en place (voir `DNS-A-CONFIGURER.md` § 0) :

| Enregistrement | Valeur | État |
|---|---|---|
| `TXT @` | `v=spf1 include:zoho.com include:spf.brevo.com ~all` | ✅ SPF autorise Brevo à envoyer |
| `TXT @` | `brevo-code:b9cabf23d325d0beaa396b99cf018d1c` | ✅ domaine déclaré chez Brevo |
| `TXT _dmarc` | `v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com` | ✅ rapport DMARC chez Brevo |
| `TXT zmail._domainkey` | clé DKIM Zoho | ✅ DKIM de la messagerie Zoho |

Autrement dit : SPF et DMARC sont posés, **DKIM d'envoi ne l'est pas**. C'est
exactement l'état que Brevo appelle « DKIM non conforme » dans sa page Domains :
un message envoyé par Brevo passe le SPF, n'a pas de signature DKIM, et le DMARC
n'est alors aligné que sur un seul mécanisme.

---

## 2. Les deux enregistrements à créer

La console Brevo affiche, pour `kder-ai.com`, **deux** entrées DKIM. Recopie ce
qu'elle affiche ; les formes attendues :

| Type | Hôte (Host) | Cible (Target / Value) | TTL |
|---|---|---|---|
| `CNAME Record` | `mail._domainkey` | `<cible 1 affichée par Brevo>` — de la forme `b1.kder-ai-com.dkim.brevo.com` | Automatic |
| `CNAME Record` | `brevo._domainkey` | `<cible 2 affichée par Brevo>` — de la forme `b2.kder-ai-com.dkim.brevo.com` | Automatic |

Deux remarques :

- **Les deux sont nécessaires.** Brevo signe avec les deux sélecteurs : un seul
  publié n'authentifie rien. Le vérificateur le dit explicitement
  (« 1 sélecteur sur 2 publié(s) » ⇒ verdict `different`).
- **La cible est propre au compte.** Elle contient un identifiant Brevo : elle
  n'est pas devinable, il faut la lire dans la console (ou dans le courrier que
  Brevo envoie avec les instructions). Si Brevo propose un **TXT** au lieu d'un
  CNAME, publie le TXT : le vérificateur accepte les deux formes et contrôle
  qu'un TXT est bien une clé DKIM (`v=DKIM1; …`).

---

## 3. Procédure Namecheap

1. Namecheap → *Domain List* → `kder-ai.com` → **Manage** → onglet **Advanced DNS**.
2. *Host Records* → **Add New Record**.
   - `Type` : `CNAME Record`
   - `Host` : `mail._domainkey`
   - `Target` : la première cible affichée par Brevo
   - `TTL` : `Automatic`
3. Recommencer pour `brevo._domainkey` avec la deuxième cible.
4. **Ne rien toucher d'autre** : les MX Zoho, le SPF, la clé `zmail._domainkey` et
   le `_dmarc` restent tels quels. Un DKIM s'ajoute, il ne remplace rien.
5. Revenir dans Brevo → **Domains** → `kder-ai.com` → *Authenticate* / *Verify*.

---

## 4. Vérification

```powershell
# verdict lisible (deux constats, un verdict)
.\.venv\Scripts\python.exe scripts\verifier_dkim_brevo.py

# verdict lisible par un script
.\.venv\Scripts\python.exe scripts\verifier_dkim_brevo.py --json

# comparer à ce que la console Brevo affiche, au lieu de se fier à la forme
.\.venv\Scripts\python.exe scripts\verifier_dkim_brevo.py --attendu "mail._domainkey=b1.kder-ai-com.dkim.brevo.com"
.\.venv\Scripts\python.exe scripts\verifier_dkim_brevo.py --attendu "mail._domainkey=..." --attendu "brevo._domainkey=..."
```

Un serveur DNS précis peut être interrogé (`--serveur 1.1.1.1`) : utile quand le
résolveur du poste garde une réponse en cache — le DNS public fait foi.

| Verdict | Code | Ce que ça veut dire | Ce qu'il faut faire |
|---|---|---|---|
| `ok` | `0` | les deux sélecteurs publiés et conformes | rien, c'est fini |
| `manquant` | `1` | aucun des deux sélecteurs n'existe | publier les deux CNAME (§ 3) |
| `different` | `2` | un sélecteur publié mais pas conforme, **ou** un seul des deux publié | corriger la cible, publier le second |
| `non-verifie` | `3` | le DNS n'a pas répondu (ni `dig`, ni `nslookup`, ou délai dépassé) | vérifier la connexion et relancer — **aucun verdict n'est rendu** |
| — | `4` | erreur d'appel (`--attendu` sans `=`) | relire la commande |

Le contrôle croisé du domaine entier reste
`scripts/verifier_dns_kder.py` : il vérifie MX, SPF, preuve de domaine, DKIM Zoho,
DKIM Brevo et DMARC dans une seule sortie.

---

## 5. Pourquoi ce n'est pas cosmétique

- **Le DMARC est en `p=none`.** Une politique `none` ne demande aucune sanction :
  elle observe. Elle n'aura d'intérêt qu'une fois SPF **et** DKIM alignés — c'est
  le préalable pour passer à `p=quarantine`, puis `p=reject`.
- **Le rapport DMARC de Brevo (`rua=mailto:rua@dmarc.brevo.com`) ne verra pas de
  `dkim=pass`** tant que les CNAME ne sont pas posés : la page Domains de Brevo
  continuera d'afficher « authenticate » plutôt que « authenticated ».
- **Sans DKIM, un message relayé perd son authentification** : Gmail et Outlook
  jugent plus sévèrement un message signé par un domaine qui ne l'est pas —
  constat classique documenté dans `GUIDE-BREVO.md` § 6.
- **Ce n'est pas bloquant pour le reste** : les MX Zoho, la réception, le SPF, la
  clé `zmail._domainkey` et le `_dmarc` fonctionnent sans. C'est pourquoi la porte
  DNS de la session précédente n'annonçait qu'un **écart** (`different`), pas une
  panne.

---

## 6. Checklist

- [ ] Lire dans Brevo les deux cibles DKIM de `kder-ai.com` (*Domains* → *Authenticate*)
- [ ] Créer `CNAME mail._domainkey` → cible 1 (Namecheap, TTL Automatic)
- [ ] Créer `CNAME brevo._domainkey` → cible 2 (Namecheap, TTL Automatic)
- [ ] Attendre 1 h, relancer `scripts/verifier_dkim_brevo.py`
- [ ] Vérifier que le verdict est `ok` (code `0`) — sinon relire les deux cibles
- [ ] Cliquer *Verify* dans Brevo, puis vérifier la page Domains
- [ ] Contrôle croisé : `.\.venv\Scripts\python.exe scripts\verifier_dns_kder.py`
- [ ] Envoyer un message de test à une adresse Gmail et lire l'en-tête
      `Authentication-Results` : `spf=pass`, `dkim=pass`, `dmarc=pass`

---

## 7. Ce qui n'est pas dans ce document

- **aucun identifiant** Brevo (SMTP, API) : `GUIDE-BREVO.md` § 4 dit où ils vivent ;
- **aucune valeur inventée** : les cibles portent `<cible 1 affichée par Brevo>`
  tant que la console ne les a pas montrées. Un document de copier-coller qui
  devine une cible ferait perdre une heure pour un résultat `different` ;
- **aucun réglage du service** : le DKIM est une affaire de DNS et de console
  Brevo. Le service (`address-validator`) ne change pas d'une ligne.

