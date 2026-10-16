# Gates: session agents IA, DNS `.com`, DKIM Brevo, SEO (15/10/2026)

Scope: les sept taches demandees le 15/10/2026 — corriger le defaut de datacenter de `verifier_dns_kder.py`, rendre les agents IA observables au lieu de les bloquer, installer le skill GEO-Optimizer, livrer deux analyseurs de journaux, preparer le DKIM Brevo, publier sur GitHub et deployer sur le VPS. Chaque porte se mesure sur l'artefact produit ; aucune n'est manuelle.

Commands are cmd.exe lines anchored beside this ledger, so run them from the `address-validator` directory with the Python 3.12 venv in `.venv` (same convention as `GATES.md`). The git and skill gates use `-C ..` or `..\` because they read the monorepo that contains this folder. A pipeline passes when its last command exits 0, so `findstr` and the Python oracles decide the gate, not the tool they filter.
T8 est une installation **manuelle assumee**. `npx skills add Auriti-Labs/geo-optimizer-skill` refuse ce depot : son `SKILL.md` d'amont n'a ni `name` ni `description`, que le CLI exige, et il n'existe aucun skill `geo-optimizer` resolvable par nom. Le skill a donc ete pose a la main dans `.agents/skills/geo-optimizer/` (6 fichiers : `SKILL.md`, `SCORING_RUBRIC.md`, trois references, `LICENSE` MIT conservee), avec un frontmatter ajoute en tete du `SKILL.md` — seule modification du contenu d'amont, signalee dans un commentaire. Provenance (commit `a674683c`), liste de ce qui a ete copie et de ce qui ne l'a pas ete, procedure de mise a jour et verification : `.agents/skills/geo-optimizer/SOURCE.md`. Deux consequences dites : `npx skills update` ne gere pas ce skill (aucune entree dans `skills-lock.json`, le CLI ne l'a jamais installe), et l'**outil** n'est pas installe non plus (`geo`, serveur MCP) — c'est la **methode** qui est lisible dans le depot, et `docs/SEO-OPTIMIZATION.md` (§ 2-3) est ce qui l'applique.

T10 et T11 sont **bloques par une divergence mesuree**, et la dire vaut mieux que pousser. `origin/main` (le depot publie, https://github.com/Kder974/address-validator) a pour tete `38df78e` (« Merge pull request #21 from Kder974/seo-technique ») et porte des decisions **D62 a D66** — `/compte/connexion`, mot de passe oublie, tableau de bord simplifie (Shopify), referencement technique (`robots.txt`, `sitemap.xml`, JSON-LD) et deux pages de contenu (`/agents`, `/compare`) — que l'arbre local **ne contient pas**. Les deux historiques sont **sans ancetre commun** (`git merge-base main origin/main` ne rend rien ; 57 commits cote publie) et le site public ne sert pas ce travail (`/robots.txt`, `/sitemap.xml`, `/agents`, `/compare` : `404`, mesure le 15/10/2026). Consequence : un `push` de l'export local sur `origin/main` **reverterait D62-D66**, et deployer le VPS reviendrait a y mettre un arbre plus ancien que le depot publie. Les deux portes attendent donc un arbitrage explicite : fusionner le publie dans l'arbre local, publier sur une branche dediee, ou deployer depuis le depot publie. Numerotation : les decisions de cette session sont **D72** (agents IA observables, jamais bloques) et **D73** (rapport de visites, sept sections) — numeros libres dans les deux historiques, verifies avant d'ecrire.




- [x] T0: this ledger lints with no error finding
  CHECK: node "..\.cline\skills\unlazy\scripts\gate-lint.mjs" GATES-AGENTS-IA.md
  EXPECT: LINT OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=607f4d8f4492294981a9090cdcb140c787d1fa24cae2410c6ff7bb44210235dc; exit=0; EXPECT=matched; output-sha256=48630b7361dd44ee870917b12c3d19b9d7bdea738aaca16bb04d4cab83b772d2; output-bytes=8; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=b9188821e2e2/24 entries

- [x] T1: `verifier_dns_kder.py` cible le datacenter `.com` par defaut et le controle reel ne signale plus aucun manquant
  CHECK: .\.venv\Scripts\python.exe scripts\verifier_dns_kder.py | findstr /C:"0 manquant(s)"
  EXPECT: 0 manquant(s)
  EVIDENCE: automatic-evidence=v1; definition-sha256=eecb5a8ee5f6ed004806cca1ec34481a483b88ae8b9c023e25becb0e32b45840; exit=0; EXPECT=matched; output-sha256=b58e2c2a973110fb44d825550c91f72ea5ead0f8f3bc6cc8d824cef089bf6768; output-bytes=82; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=b9188821e2e2/24 entries

- [x] T2: la suite de tests du controleur DNS passe en entier, defaut `.com` et variante `.eu` compris
  CHECK: .\.venv\Scripts\python.exe scripts\run_tests.py tests\test_verifier_dns_kder.py
  EXPECT: GATE OK pytest
  EVIDENCE: automatic-evidence=v1; definition-sha256=8f86c6862ebb37f072341ccf8b1102fb8db1d4bd7cb35fdfaa9d4a5c69beb29f; exit=0; EXPECT=matched; output-sha256=47def2f2a355f38d58a3b262c9c057463bc0cb5928aef453f7b8f9dafdf35f6d; output-bytes=667; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=b9188821e2e2/24 entries

- [x] T3: l'observabilite des agents IA classe, journalise et publie — tests dedies
  CHECK: .\.venv\Scripts\python.exe scripts\run_tests.py tests\test_agents_ia.py
  EXPECT: GATE OK pytest
  EVIDENCE: automatic-evidence=v1; definition-sha256=18a0690a119bd2ddcb69389101eb911b926102a668c57012da3d1d2660b53d49; exit=0; EXPECT=matched; output-sha256=6037acd0193cecd7afc43d8b17444fdc1205bdeac1af95ddc796df4ad4ab47d9; output-bytes=649; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=b9188821e2e2/24 entries

- [x] T4: `scripts/analyser_agents.py` produit un rapport JSON avec les cinq mesures demandees, meme sans journal
  CHECK: .\.venv\Scripts\python.exe scripts\analyser_agents.py --json | .\.venv\Scripts\python.exe -c "import json,sys;d=json.loads(sys.stdin.buffer.read().decode('utf-8'));need={'agents','top_ip','top_agents','top_pages','conversion'};print('AGENTS OK' if need<=set(d) else 'AGENTS MANQUE '+repr(sorted(need-set(d))))"
  EXPECT: AGENTS OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=b905bb0e3deeceda945511e8ac772dd2f1d6dbefb107873f84d9ca18612c4488; exit=0; EXPECT=matched; output-sha256=304f60e964f3cd88f40f42a783c0ead32b04585404a02ade20d7923cc1695aa5; output-bytes=11; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=b9188821e2e2/24 entries

- [x] T5: `scripts/analyser_visites.py` rend les sept sections demandees, y compris sur une machine sans journal nginx
  CHECK: .\.venv\Scripts\python.exe scripts\analyser_visites.py --since 24h --json | .\.venv\Scripts\python.exe -c "import json,sys;d=json.loads(sys.stdin.buffer.read().decode('utf-8'));print('VISITES OK '+str(len(d['sections'])) if len(d['sections'])==7 else 'VISITES KO '+repr(sorted(d['sections'])))"
  EXPECT: VISITES OK 7
  EVIDENCE: automatic-evidence=v1; definition-sha256=c46a71cc4f0004217edc2f9ebbb333c371d7b8f9e93ec553c2eabce2a382a141; exit=0; EXPECT=matched; output-sha256=2b57775e04179e81c54e6e0a609e191782294272867a895ca7c460932f9fc825; output-bytes=14; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=b9188821e2e2/24 entries

- [x] T6: `scripts/verifier_dkim_brevo.py` interroge les deux selecteurs et rend un verdict code
  CHECK: .\.venv\Scripts\python.exe scripts\verifier_dkim_brevo.py --json | .\.venv\Scripts\python.exe -c "import json,sys;d=json.loads(sys.stdin.buffer.read().decode('utf-8'));print('DKIM BREVO OK '+d['verdict']['code'] if d['selecteurs'] and d['verdict']['code'] in ('ok','manquant','different') else 'DKIM BREVO KO')"
  EXPECT: DKIM BREVO OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=2418017346514734c06189dccebc52e6737bbb74d9ebb873a88aeac5dbc854ce; exit=0; EXPECT=matched; output-sha256=9d0477317892b44724ac68eb3f15e2840f2d3853d5c4b1e081169a63362d1739; output-bytes=24; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=b9188821e2e2/24 entries

- [x] T7: les six documents livres sont presents
  CHECK: .\.venv\Scripts\python.exe -c "import pathlib as p;s='docs/AGENTS-IA.md docs/SEO-OPTIMIZATION.md docs/commercial/DKIM-BREVO-A-CONFIGURER.md docs/commercial/DNS-A-CONFIGURER.md SECURITY.md README.md'.split();m=[f for f in s if not p.Path(f).is_file()];print('DOCS OK' if not m else 'DOCS MANQUANTS '+repr(m))"
  EXPECT: DOCS OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=256c2e60a67e2c403225c0a32164e3131b6dd8661be53603af0495bc9922bc41; exit=0; EXPECT=matched; output-sha256=2e757275f814a46e446ec680b91aeb8dd21cf4e1396af08a581cf766fa373ba0; output-bytes=9; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=b9188821e2e2/24 entries

- [x] T8: le skill GEO-Optimizer est installe et son contenu est lisible dans le depot
  CHECK: .\.venv\Scripts\python.exe -c "import pathlib as p;r=p.Path('..');c=[r/'.agents/skills/geo-optimizer',r/'.cline/skills/geo-optimizer'];print('GEO SKILL OK' if any(d.is_dir() and any(d.iterdir()) for d in c) else 'GEO SKILL ABSENT')"
  EXPECT: GEO SKILL OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=d9b78ff3acb4f1e7537d4dccac4fc69b71f9ff15a043efaa4eb80ea49e52c3e4; exit=0; EXPECT=matched; output-sha256=cb852f5039ddbe3ba8548fe416709e1ee8d86ddbadb8f370efc4390bef9cd168; output-bytes=14; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=b9188821e2e2/24 entries

- [x] T9: aucun fichier de secret n'est suivi par git et `.env.smtp` reste ignore
  CHECK: .\.venv\Scripts\python.exe -c "import subprocess;f=subprocess.run(['git','-C','..','ls-files'],capture_output=True,text=True).stdout.split();b=[x for x in f if x.endswith(('.pem','.key')) or (x.rsplit('/',1)[-1].startswith('.env') and not x.endswith('.example'))];print('SECRETS OK' if not b else 'SECRETS SUIVIS '+' '.join(b))"
  EXPECT: SECRETS OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=9437f8f8ff05d5586e529e9a0527ab2053315e7aa2124abbced43ef1dabd8809; exit=0; EXPECT=matched; output-sha256=5d2473835b2ff5e62a0aa0d92c65c903cebb87f45e44571bf90c78d25cdb698d; output-bytes=12; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=b9188821e2e2/24 entries

- [ ] T10: la branche publiee sur `origin/main` porte le commit de session
  CHECK: git -C .. fetch -q origin main & git -C .. log -1 --oneline FETCH_HEAD | findstr /C:"correction DNS .com"
  EXPECT: correction DNS .com

- [ ] T11: le site public repond aux sept URL attendues, `/metrics/agents` compris
  CHECK: .\.venv\Scripts\python.exe scripts\verifier_urls_public.py
  EXPECT: URLS PUBLIQUES OK 7/7
