# DNS à configurer — `kder-ai.com` (Zoho Mail + Brevo)

Guide de **copier-coller** pour Namecheap. Établi le 13/10/2026, à partir des
valeurs relevées dans le DNS public et de la documentation officielle Zoho.

| Question | Réponse |
|----------|---------|
| **Qui** | l'exploitant (Kder), depuis Namecheap |
| **Quoi** | brancher le courrier de `kder-ai.com` sur Zoho Mail, puis autoriser Brevo à envoyer au nom du domaine |
| **Comment** | 3 MX + 1 TXT SPF + 1 TXT DKIM + 1 TXT DMARC (valeurs exactes au § 3) |
| **Quand** | avant le premier envoi ; contrôle environ 1 h après la modification |
| **Où** | Namecheap → *Domain List* → `kder-ai.com` → **Manage** → onglet **Advanced DNS** → *Host Records* |
| **Fait quoi exactement** | supprime la redirection eForward (MX Namecheap), la remplace par Zoho Mail, et déclare Zoho + Brevo comme expéditeurs légitimes |

> Ce document ne contient **aucun secret**. Les clés (SMTP Brevo, API) vivent
> dans le `.env` local ou les variables d'environnement, jamais dans le dépôt.

---

## 0. Où en est le domaine (relevé du 15/10/2026)

`kder-ai.com` a été migré sur Zoho **monde** (`.com`), pas sur l'Europe : c'est
l'état publié aujourd'hui, et c'est celui que `scripts/verifier_dns_kder.py`
contrôle **par défaut** (datacenter du domaine).

| Enregistrement | Valeur publiée le 15/10/2026 | État |
|----------------|------------------------------|------|
| `MX @` | `mx.zoho.com` 10, `mx2.zoho.com` 20, `mx3.zoho.com` 50 | ✅ en place |
| `TXT @` | `v=spf1 include:zoho.com include:spf.brevo.com ~all` | ✅ en place |
| `TXT @` | `zoho-verification=zb82668052.zmverify.zoho.com` | ✅ en place |
| `TXT @` | `brevo-code:b9cabf23d325d0beaa396b99cf018d1c` | ✅ clé de domaine Brevo (publique) |
| `TXT zmail._domainkey` | clé DKIM Zoho (234 caractères) | ✅ en place |
| `TXT _dmarc` | `v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com` | ✅ en place |
| `CNAME mail._domainkey` | *absent* | ❌ à créer dans Brevo — voir `DKIM-BREVO-A-CONFIGURER.md` |

Les § 1 à 4 décrivent la variante **Europe** (`zoho.eu`) et gardent leur valeur de
référence ; le § 5 donne les valeurs **monde**, celles qui servent réellement.

---

## 1. État relevé du domaine (13/10/2026, avant modification)

| Enregistrement | Valeur publiée aujourd'hui | À faire |
|----------------|----------------------------|---------|
| `A` (racine) | `87.76.197.38` — le site / l'API | **ne pas toucher** |
| `NS` | `dns1.registrar-servers.com`, `dns2.registrar-servers.com` | ne pas toucher (c'est là que tout se règle) |
| `MX` | `eforward1` à `eforward5.registrar-servers.com` (priorités 10, 10, 10, 15, 20) | **supprimer** (redirection Namecheap) |
| `TXT @` | `v=spf1 include:spf.efwd.registrar-servers.com ~all` | **remplacer** (§ 2 et 3) |
| `TXT @` | `zoho-verification=zb82668052.zmverify.zoho.com` | **déjà en place** — vérifier qu'elle survit |
| `TXT _dmarc` | absent | **ajouter** |
| `TXT zmail._domainkey` | absent | **ajouter** (DKIM Zoho) |

---

## 2. À supprimer d'abord (obligatoire, avant tout ajout)

Dans *Advanced DNS* → *Host Records*, supprimer :

1. les **cinq** lignes `MX Record` `eforward1` … `eforward5.registrar-servers.com` ;
2. la ligne `TXT Record` `@` `v=spf1 include:spf.efwd.registrar-servers.com ~all`.

> ⚠️ **Un domaine ne peut avoir qu'un seul jeu de MX.** Ajouter les MX Zoho
> *sans* supprimer les eForward ferait livrer une partie du courrier à la
> redirection Namecheap (et l'autre à Zoho) : les messages perdus ne se
> rattrapent pas. Même logique pour le SPF : **un seul** TXT commençant par
> `v=spf1` (deux enregistrements SPF = `permerror`, plus rien n'est authentifié).

---

## 3. À ajouter — tableau de copier-coller (datacenter **Europe** : `zoho.eu`)

Namecheap : *Host* `@` = le domaine lui-même. *TTL* : laisser **Automatic**.
Ne pas mettre de point final dans les valeurs MX.

| # | Type | Host | Value | Priority | TTL |
|---|------|------|-------|----------|-----|
| 1 | `MX Record` | `@` | `mx.zoho.eu` | `10` | Automatic |
| 2 | `MX Record` | `@` | `mx2.zoho.eu` | `20` | Automatic |
| 3 | `MX Record` | `@` | `mx3.zoho.eu` | `50` | Automatic |
| 4 | `TXT Record` | `@` | `v=spf1 include:zoho.eu include:spf.brevo.com ~all` | — | Automatic |
| 5 | `TXT Record` | `@` | `zoho-verification=zb82668052.zmverify.zoho.com` | — | Automatic |
| 6 | `TXT Record` | `zmail._domainkey` | *(clé DKIM affichée par Zoho — § 4.3)* | — | Automatic |
| 7 | `TXT Record` | `_dmarc` | `v=DMARC1; p=none; rua=mailto:address-validator@kder-ai.com; fo=1` | — | Automatic |

Mêmes valeurs, une par ligne, pour copier sans se tromper de colonne :

```text
Type MX  | Host @ | Value mx.zoho.eu   | Priority 10
Type MX  | Host @ | Value mx2.zoho.eu  | Priority 20
Type MX  | Host @ | Value mx3.zoho.eu  | Priority 50
Type TXT | Host @ | Value v=spf1 include:zoho.eu include:spf.brevo.com ~all
Type TXT | Host @ | Value zoho-verification=zb82668052.zmverify.zoho.com
Type TXT | Host zmail._domainkey | Value <cle DKIM Zoho, § 4.3>
Type TXT | Host _dmarc | Value v=DMARC1; p=none; rua=mailto:address-validator@kder-ai.com; fo=1
```

- Ligne 5 : si elle est déjà présente (c'est le cas), ne rien refaire.
- Ligne 4 : c'est la **seule** ligne à modifier le jour où un autre service
  d'envoi s'ajoute (son `include:` s'ajoute dans la même valeur).
- Brevo demandera ensuite ses propres enregistrements (DKIM) : ils
  s'**ajoutent**, ils ne remplacent rien — voir `GUIDE-BREVO.md`.

---

## 4. D'où vient chaque valeur (sources, vérifiées le 13/10/2026)

### 4.1 MX (Zoho Mail, Europe)

La valeur qui fait foi est **celle affichée par ta propre console Zoho**. Zoho
l'écrit dans sa FAQ officielle (<https://www.zoho.com/toolkit/mx-lookup.html>) :

> « The MX records will be based on your account. Login as Super Administrator
> and check this section to find out the correct MX records for your domain. »

Où la lire : `mail.zoho.eu` → **Admin Console → Domaines → kder-ai.com →
Configuration e-mail → MX**. Attendu pour un compte européen : les trois hôtes
du § 3. Relevé dans le DNS public le 13/10/2026 : `mx.zoho.eu` →
`185.230.214.166`, `mx2.zoho.eu` → `185.230.214.166`, `mx3.zoho.eu` →
`185.230.212.166` (plage Zoho UE `185.230.214.0/23`) ; `mx4.zoho.eu` **n'existe
pas**, il n'y a donc que trois MX côté `zoho.eu`.

*Note : l'ancienne page publique `adminconsole/configure-dns.html` répond
aujourd'hui `404` — Zoho a déplacé ce contenu dans la console. C'est la console
qu'il faut lire, pas un tutoriel recopié.*

### 4.2 SPF

- Règle Zoho (documentation « SPF configuration ») : **un seul** enregistrement
  SPF par domaine, avec l'`include:` Zoho **plus** l'`include:` des autres
  expéditeurs autorisés, et se terminant par `~all` pour commencer.
- `include:zoho.eu` et non `zoho.com`, parce que le compte est européen :
  `include:zoho.eu` résout vers `spf.zoho.eu`, qui couvre `ip4:89.36.170.0/24`
  — l'adresse de `smtp.zoho.eu` (relevée : `89.36.170.164`). À l'inverse
  `spf.zohomail.com` **ne couvre pas** cette plage : pour un compte `zoho.eu`,
  mettre `include:zohomail.com` ferait échouer le SPF des messages envoyés par
  Zoho.
- **Cas de `kder-ai.com`** : le compte est en fait hébergé par Zoho **monde**,
  l'`include` attendu est donc `include:zoho.com` (§ 5 et § 0), pas `zoho.eu`.
  Le raisonnement ci-dessus reste valable pour un compte réellement européen :
  c'est le datacenter du compte qui décide de l'`include`, jamais l'inverse.
- `include:spf.brevo.com` : relevé dans le TXT de `spf.brevo.com`
  (`v=spf1 ip4:185.41.28.0/22 … -all`), c'est l'`include` officiel demandé par
  Brevo ; sans lui, les emails du script partent sans SPF valide.

### 4.3 DKIM (Zoho)

- Format Zoho : un enregistrement **TXT**, hôte `zmail._domainkey`, valeur
  commençant par `v=DKIM1; k=rsa; p=…` (clé longue : la coller **sans** retour
  à la ligne).
- Où la lire : `mail.zoho.eu` → **Admin Console → Domaines → kder-ai.com →
  Configuration e-mail → DKIM → Ajouter** (sélecteur `zmail`). Zoho affiche la
  valeur à copier. Selon la configuration proposée, Zoho peut donner à la place
  un **CNAME** (DKIM hébergé par Zoho) : dans ce cas coller le CNAME tel
  qu'affiché, jamais une valeur devinée.
- Après le collage : revenir dans la console Zoho et cliquer **Vérifier**.

### 4.4 DMARC

- Recommandation Zoho *et* standard : commencer en **surveillance**, donc
  `p=none`, avec un rapport quotidien vers l'adresse du domaine.
- `fo=1` demande un rapport d'échec dès qu'un mécanisme DKIM/SPF échoue : c'est
  ce qui préviendra si un expéditeur est mal configuré.
- Resserrer plus tard, quand les rapports sont propres :
  `v=DMARC1; p=quarantine; pct=25; rua=mailto:address-validator@kder-ai.com; fo=1`.

---

## 5. Datacenter monde (`.com`) — les valeurs en service

C'est la variante retenue pour `kder-ai.com` (§ 0). Remplacer les lignes 1 à 4
du § 3 par :

| Type | Host | Value | Priority |
|------|------|-------|----------|
| `MX Record` | `@` | `mx.zoho.com` | `10` |
| `MX Record` | `@` | `mx2.zoho.com` | `20` |
| `MX Record` | `@` | `mx3.zoho.com` | `50` |
| `TXT Record` | `@` | `v=spf1 include:zoho.com include:spf.brevo.com ~all` | — |

`include:zoho.com` est l'`include` que Zoho publie pour le datacenter monde, et
le TXT SPF de `zoho.com` chaine lui-même `include:spf.zohomail.com` : les hôtes
d'envoi `zohomail.com` sont donc couverts. **Ne pas écrire `include:zohomail.com`** :
ce domaine publie bien un SPF (`v=spf1 include:spf.zohomail.com -all`, relevé le
15/10/2026), mais ce n'est pas la valeur documentée pour un domaine, et l'exiger
faisait déclarer « écart » un SPF pourtant correct — c'est le faux positif
corrigé dans `scripts/verifier_dns_kder.py` le 15/10/2026.

Le DKIM (§ 4.3), le DMARC (§ 4.4) et la preuve de domaine sont **identiques**
dans les deux cas.

---

## 6. Avertissements à relire avant de cliquer

1. **Supprimer les MX eForward existants AVANT d'ajouter les MX Zoho** : un
   domaine ne peut avoir qu'un seul jeu de MX.
2. **Un seul TXT `v=spf1`** : les autres expéditeurs s'ajoutent en `include:`
   dans la *même* ligne.
3. **Un seul TXT `_dmarc`.**
4. Ne toucher à **aucun** enregistrement `A` / `CNAME` du site : `87.76.197.38`
   sert l'API et les pages publiques.
5. `contact@kder-ai.com` : à créer comme **alias** dans Zoho (Admin Console →
   Utilisateurs → Alias). Ce n'est pas du DNS, et le code n'en dépend pas : il
   utilise `address-validator@`.
6. Le TTL *Automatic* chez Namecheap vaut 30 min : compter **jusqu'à 1 h** de
   propagation avant de conclure qu'un enregistrement manque.
7. Ne jamais coller un secret (clé SMTP Brevo, clé API) dans le DNS : le DNS est
   public et le restera.
8. Ne pas créer d'enregistrement `CAA` tant qu'aucune autorité n'est annoncée :
   un `CAA` mal renseigné bloquerait le renouvellement Certbot du site.

---

## 7. Vérifier (après ~1 h)

```powershell
# Sans option : datacenter du domaine (.com, § 5) — c'est le contrôle courant
.venv\Scripts\python.exe scripts\verifier_dns_kder.py

# DKIM : coller entre les guillemets la valeur affichée par la console Zoho
.venv\Scripts\python.exe scripts\verifier_dns_kder.py --dkim "v=DKIM1; k=rsa; p=MIIBIjANBgkq..."

# Variante Europe (§ 3 et § 4), pour un domaine réellement hébergé par zoho.eu
.venv\Scripts\python.exe scripts\verifier_dns_kder.py --dc eu
```

Sortie attendue : `✅ OK` sur toutes les lignes, code de retour `0`
(`✅ DNS prêts.`). Les codes sont : `0` tout est vert, `1` au moins un
enregistrement **manquant**, `2` uniquement des **écarts**, `3` aucun outil DNS
utilisable sur la machine.

Le DKIM Brevo (`mail._domainkey`) reste annoncé **DIFFERENT** tant que le CNAME
n'a pas été créé dans Brevo : c'est un écart de la tâche suivante, décrit dans
`DKIM-BREVO-A-CONFIGURER.md` — il ne bloque pas les MX, le SPF, le DKIM Zoho ni
le DMARC.

---

## 8. Checklist Namecheap (préparation, variante Europe)

> La migration est faite sur `.com` (§ 0 et § 5) : cette checklist a servi à la
> préparation. Ce qui reste réellement ouvert côté e-mail est décrit dans
> `DKIM-BREVO-A-CONFIGURER.md`.

- [ ] Supprimer les 5 MX `eforward*.registrar-servers.com`
- [ ] Supprimer le TXT SPF `include:spf.efwd.registrar-servers.com`
- [ ] Ajouter MX `@` `mx.zoho.eu` (priorité 10)
- [ ] Ajouter MX `@` `mx2.zoho.eu` (priorité 20)
- [ ] Ajouter MX `@` `mx3.zoho.eu` (priorité 50)
- [ ] Remplacer le TXT SPF par `v=spf1 include:zoho.eu include:spf.brevo.com ~all`
- [ ] Vérifier que `zoho-verification=…` est toujours présente
- [ ] Coller la clé DKIM sur `zmail._domainkey`, puis **Vérifier** dans Zoho
- [ ] Ajouter `_dmarc` (`p=none`)
- [ ] Cliquer **Enregistrer** (bouton en bas de *Advanced DNS*)
- [ ] Attendre 1 h, puis lancer `scripts\verifier_dns_kder.py`
- [ ] Créer l'alias `contact@kder-ai.com` dans Zoho
