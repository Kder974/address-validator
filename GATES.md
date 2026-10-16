# Gates: address-validator acceptance ledger

Scope: every decision recorded in `DECISIONS.md` is decided by a runnable oracle, one gate per decision. No gate in this ledger is manual.

Each gate's `CHECK:` line runs the single-decision oracle in `scripts/decisions_check.py`. That oracle prints `DECISION OK D<n>` only after every assertion of decision D<n> holds, prints `DECISION FAIL D<n>: <reason>` otherwise, and exits 1 when any gate is red (2 for usage or an unknown id). Its assertions read the repository (dependency pins, DDL, shebang, docs) and exercise the running code (HMAC payment proof, circuit breaker under eight threads, Redis cooldown, bounded `/health` with hanging probes, ORM-versus-DDL columns, degraded geocoding).

Commands are cmd.exe lines anchored beside this ledger, so run them from the `address-validator` directory with the Python 3.12 venv in `.venv`. The earlier phase ledger G1..G16 is superseded; the phase evidence it collected is measured in `TEST_REPORT.md`.

- [x] G0: this ledger lints with no error finding
  CHECK: node "..\.cline\skills\unlazy\scripts\gate-lint.mjs" GATES.md
  EXPECT: LINT OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=3cd8c37f977e54e16275c84fa4714b7bc1aab0d3c0a8088e2910c99685f82e1b; exit=0; EXPECT=matched; output-sha256=48630b7361dd44ee870917b12c3d19b9d7bdea738aaca16bb04d4cab83b772d2; output-bytes=8; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D1: the pinned cdp-sdk version is the published release and is installed
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D1
  EXPECT: DECISION OK D1
  EVIDENCE: automatic-evidence=v1; definition-sha256=b9aaf476ecb22394db5a65616ee891e45e70e96d5a19c5f5bd973432bb98adab; exit=0; EXPECT=matched; output-sha256=9f33080ae08245a959a285e7ca027b5d6485e88ec1421926a8b2e8671529d381; output-bytes=16; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D2: no smarty-street-python package is imported and the HTTP API is called directly
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D2
  EXPECT: DECISION OK D2
  EVIDENCE: automatic-evidence=v1; definition-sha256=8f44a0daeab84ab7e169de51847ab8f47352c73ae49c36964ff594e7c55811a6; exit=0; EXPECT=matched; output-sha256=e5aeec253b2d0e57d01dd3ed7e41981907a11257d5d315b0bcf3f97837eb1665; output-bytes=16; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D3: pydantic-settings is declared, importable, and carries the configuration
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D3
  EXPECT: DECISION OK D3
  EVIDENCE: automatic-evidence=v1; definition-sha256=4d25b8075de6f95d55f93f4b6fa1a9897ee12b4d99eebb51a28189bd85bfde79; exit=0; EXPECT=matched; output-sha256=c10379c1d66db0726b355b6b4221ae2226972661def2ace8bb2166b29d3ffeae; output-bytes=16; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D4: pydantic resolves to 2.10.3, the version cdp-sdk requires
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D4
  EXPECT: DECISION OK D4
  EVIDENCE: automatic-evidence=v1; definition-sha256=2c4732bb885cf68e65355fc4a1dd94ff5cfbc2dc4798361b35280ced5a09f207; exit=0; EXPECT=matched; output-sha256=41a5185a3e8824a9fc84a34a0746c352dab1035df52e21c93e9a8bb36703190c; output-bytes=16; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D5: the paid route answers from cache before any provider call
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D5
  EXPECT: DECISION OK D5
  EVIDENCE: automatic-evidence=v1; definition-sha256=aa2208c0bf2cf1ed46bf22750b425618c0be8cd4bbdd423d1522d575f7fa24fe; exit=0; EXPECT=matched; output-sha256=24ccd8261e7a8c5e374f4f5b681ee23e75890dc09390e56a636bc5e30697edbb; output-bytes=77; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D6: a missing CDP SDK keeps payment fail-closed and never fail-open
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D6
  EXPECT: DECISION OK D6
  EVIDENCE: automatic-evidence=v1; definition-sha256=e0096faf99c2f59745a0b2f4fd4f5722cacdcbc22c5eea79cbc50934617a9243; exit=0; EXPECT=matched; output-sha256=514483ef21f52b9101159a1158175cca015d92c26dbf9eb351bd6c7ea0be71c4; output-bytes=137; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D7: the payment proof is fresh, symmetric, and HMAC-signed
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D7
  EXPECT: DECISION OK D7
  EVIDENCE: automatic-evidence=v1; definition-sha256=7df93e79c9151239b2561791dec0baa06f287b03b81a9a5432608c6ae83e0306; exit=0; EXPECT=matched; output-sha256=022973beb864ba58df1a178531ce209f1105bef3e5daa46275f9e334aa47ef2f; output-bytes=77; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D8: an unknown environment variable does not break startup
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D8
  EXPECT: DECISION OK D8
  EVIDENCE: automatic-evidence=v1; definition-sha256=bbe2f4b717806013e9baf1a891a0cc42832e9e45902345f2f48de5df59288acf; exit=0; EXPECT=matched; output-sha256=5559256f391e8ebe52a7cd101f0155eac9698aa0d8242704c26cae7c920040ef; output-bytes=16; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D9: the circuit-breaker state is lock-protected and re-armable
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D9
  EXPECT: DECISION OK D9
  EVIDENCE: automatic-evidence=v1; definition-sha256=f8c4565de3fa7e1da902f1d834a1ff565d7d1597df7b3407d57082bb4c9cad69; exit=0; EXPECT=matched; output-sha256=e3ce28235f358296af941ad338a4c94383549cc214e437f8fd372a46a1a56bb4; output-bytes=16; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D10: the geocoder sends a User-Agent carrying a contact address
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D10
  EXPECT: DECISION OK D10
  EVIDENCE: automatic-evidence=v1; definition-sha256=69e9258df894396926427e9f737d947ddba067e629e68605cec3e39f2fa32fea; exit=0; EXPECT=matched; output-sha256=71caf1ad3a70fd9f56e54ea6b296068be90527c1364dff490d51f57222175e35; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D11: payments are recorded in a dedicated idempotent table
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D11
  EXPECT: DECISION OK D11
  EVIDENCE: automatic-evidence=v1; definition-sha256=a798d4731642016fa48840d932f6fd6f33332803d84b0c9066a09051d85ce782; exit=0; EXPECT=matched; output-sha256=7bb54985002e9a27ea3ed4a92aaf76903d5a4fc0318ef004c11c92100f88f3b3; output-bytes=78; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D12: Redis timeouts are short and cache errors are counted
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D12
  EXPECT: DECISION OK D12
  EVIDENCE: automatic-evidence=v1; definition-sha256=41fc2289a1015ea58dc72ac8aac68ed10a395e2ed5167ee82d4c5548380b3aa0; exit=0; EXPECT=matched; output-sha256=b5f7d8c37cb8224e5da69e45c53a7b6d31b7ab88e13f7cf8bf1d4186894036c7; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D13: ORM models cover exactly the SQL DDL columns
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D13
  EXPECT: DECISION OK D13
  EVIDENCE: automatic-evidence=v1; definition-sha256=422cd7d8a68e8b757cd79d8af550ed9cc12141740d134ba9cf801b6e71d3e926; exit=0; EXPECT=matched; output-sha256=8794ce39126024f2f570527a28953a3a5f14c2cd4120da298be8ea49892a6727; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D14: an unreachable database blocks neither startup nor /health
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D14
  EXPECT: DECISION OK D14
  EVIDENCE: automatic-evidence=v1; definition-sha256=57fc5cfee13646831957ec0267fc77dec89eb66164f287e175086806ced7a5a5; exit=0; EXPECT=matched; output-sha256=4e1593dee86db8f5f37e3ed79248621c098122be481d52180464fa480ac5d344; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D15: the shared-secret proof still serves, and the on-chain surface is bounded
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D15
  EXPECT: DECISION OK D15
  EVIDENCE: automatic-evidence=v1; definition-sha256=799accbdc643a719807dc586e6b09c34870c837adfea32147f0a02a1cc0ecaf0; exit=0; EXPECT=matched; output-sha256=8b1f57b9f64e156adf287aa3edd74301ccffd94b92fa3556053119729eaec353; output-bytes=78; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D16: startup creates the schema without depending on the database
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D16
  EXPECT: DECISION OK D16
  EVIDENCE: automatic-evidence=v1; definition-sha256=32796554801f1c53e3304cb07723b60975b13ba6ae7c8fec6921a90c9899b281; exit=0; EXPECT=matched; output-sha256=10509f048e42b17892c49d28ff554caff0b0f81c14c96d129f563da471e8b11c; output-bytes=246; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D17: /metrics counters are atomic and free of external dependencies
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D17
  EXPECT: DECISION OK D17
  EVIDENCE: automatic-evidence=v1; definition-sha256=c016b05834baf2a13843a72b682e58763446bc20d6c1c84569c23415954a6488; exit=0; EXPECT=matched; output-sha256=8e8710f9f94b4b1e650a97613e74b715e760ac853b1345a2e51c2f80fbdd8503; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D18: test dependencies stay out of the production image
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D18
  EXPECT: DECISION OK D18
  EVIDENCE: automatic-evidence=v1; definition-sha256=e7dc7f426bc0de62c95e340edfc1bd3a1408dff046c45984a00df17dd45eb519; exit=0; EXPECT=matched; output-sha256=62b4eeb6c8a76f4399ecb44aed8453d1e40b94ce32a3ed6506dbdde2130cd0fb; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D19: a cooldown of exactly the elapsed time re-arms the probe
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D19
  EXPECT: DECISION OK D19
  EVIDENCE: automatic-evidence=v1; definition-sha256=3a26dcf0559728072cf7b24323fd140d56ca80a061009d0d7649bf83d060c0ca; exit=0; EXPECT=matched; output-sha256=a412bf1c28a2106db9f1bcb9c49f445e25ed00e24b8b6f571d68fd7060471231; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D20: deployment stops on the first error, runs from the venv, and is LF-only
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D20
  EXPECT: DECISION OK D20
  EVIDENCE: automatic-evidence=v1; definition-sha256=a1b073d06801c38a4f21a0112890ca59d7fbdb9cd096ad97f591b45c287817ac; exit=0; EXPECT=matched; output-sha256=4e7dcc9de466f2c3c62e20b17595cd676ae79e36554810a01fc9ba218e42e1ea; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D21: rate limiting lives in the proxy, not in the application
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D21
  EXPECT: DECISION OK D21
  EVIDENCE: automatic-evidence=v1; definition-sha256=46119738038da5856156229b479fa5ece3ecbb97a3c33e265301424a7056e7e1; exit=0; EXPECT=matched; output-sha256=87bf72e86f43c466b17fe645cdba69030d58606b63c43b5ff03aaa82bded4ce5; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D22: /health probes run off-loop, in parallel, and bounded
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D22
  EXPECT: DECISION OK D22
  EVIDENCE: automatic-evidence=v1; definition-sha256=3ac0e5b1b1936a6e6dc8ef1ba5afc8b70551499c98deb6a08815f6cd9a51c539; exit=0; EXPECT=matched; output-sha256=831a498fd173b32fd2b7517344e7a94c7a6c38f259452480e9ede526218935b8; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D23: the provider call budget is bounded and the retry is preserved
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D23
  EXPECT: DECISION OK D23
  EVIDENCE: automatic-evidence=v1; definition-sha256=e52901abbf13891c8d45c403bcd774715377f61f56893367ad1c564c80254ea7; exit=0; EXPECT=matched; output-sha256=f3a0cee0b91f7ad62046644b6c8576c004fef0920de8227d005757f6c7fd0bfa; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D24: /metrics separates an open circuit from a broken cache backend
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D24
  EXPECT: DECISION OK D24
  EVIDENCE: automatic-evidence=v1; definition-sha256=3e91f329b0d7ff9ee1419f066ee155d6b2f8ace4dc8d2c900f2e20d1a039c5dc; exit=0; EXPECT=matched; output-sha256=881f5199db933cfa3d0ba0fc12017b50dd57b6264b89bc69d383c9f87d9bb3c2; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D25: a cache backend outage costs one call, not one per request
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D25
  EXPECT: DECISION OK D25
  EVIDENCE: automatic-evidence=v1; definition-sha256=1c33e34a0bea3d9cab83d47b2fe74275f7fa7b03e012971f257977469fc97ce4; exit=0; EXPECT=matched; output-sha256=28cf9b698e48ef098087015d60a524db1572bed7cfb8a316d91b5be9188fb0e6; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D26: the geocoder is bounded and degrades to null coordinates
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D26
  EXPECT: DECISION OK D26
  EVIDENCE: automatic-evidence=v1; definition-sha256=ad59716320fe571d8d74d92c4f45d0fa1cca032ddc1bf979857e06e946cb739f; exit=0; EXPECT=matched; output-sha256=5453964a743b526186062204acc70f2cd71e09b494c8b317a816c65282ab1e52; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D27: the on-chain payment check is opt-in, fail-closed and library-free
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D27
  EXPECT: DECISION OK D27
  EVIDENCE: automatic-evidence=v1; definition-sha256=7dcf495a47172d12d8a5084d49af66bf5a6ee620467448aa765eca05eed46430; exit=0; EXPECT=matched; output-sha256=73b1bd07135856a159c5fb7af836bee43769ace51acd816fac4ea731271467ee; output-bytes=153; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries


- [x] D28: the published discovery files describe only endpoints that answer
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D28
  EXPECT: DECISION OK D28
  EVIDENCE: automatic-evidence=v1; definition-sha256=8d6752a64696212280d265152f264bd43f50206874dc7ebbe94ba81cff0fafbe; exit=0; EXPECT=matched; output-sha256=06c5c2c3265fe69555587339eadb0e778075daeeff58906e92817d1a4ca538db; output-bytes=78; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D29: every accepted payment is recorded once, keyed by its proof hash
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D29
  EXPECT: DECISION OK D29
  EVIDENCE: automatic-evidence=v1; definition-sha256=edc72c78446ac700f1a45a1a475275322b17ad43d4e7b8e723a4f7e06ea0eaf9; exit=0; EXPECT=matched; output-sha256=dc8813bbf4496532b5e652cd26f21f9db6e2503f5a3a2f9aad8e295e9cdf2bca; output-bytes=230; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D30: unpaid attempts are logged for discovery with the client IP pseudonymised
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D30
  EXPECT: DECISION OK D30
  EVIDENCE: automatic-evidence=v1; definition-sha256=5345fc80c850194a1e704815ac94fe65d293fc0938d36b87fe0dfafecf66b496; exit=0; EXPECT=matched; output-sha256=74acb564281e1c323bd9cab444660f639361183311466e2984455ee4ef91ced5; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D31: /metrics/revenue reports durable revenue windows and degrades honestly
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D31
  EXPECT: DECISION OK D31
  EVIDENCE: automatic-evidence=v1; definition-sha256=10c0ac6162dfee8c46a9e8ea1800ebb9f48d7ff460ced1ee05e7ca89310402b1; exit=0; EXPECT=matched; output-sha256=dbf2fbb5c955d3e7c48c794cdf0eceff0b3ab48c3a1bd409f843af9eb6d5636e; output-bytes=160; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D32: /dashboard is self-contained, fail-closed and password protected
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D32
  EXPECT: DECISION OK D32
  EVIDENCE: automatic-evidence=v1; definition-sha256=abc36b4f1c9cf754669c5ec7c97cae002cbc6c9d7a4399d4d91ce23e31460e02; exit=0; EXPECT=matched; output-sha256=a5a69cfee41a10e80a2bf28a7a340433d6d6ec3c68db4308fb5f2734e0cec46f; output-bytes=591; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D33: the manifest carries schemas, examples, use cases and explicit limits
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D33
  EXPECT: DECISION OK D33
  EVIDENCE: automatic-evidence=v1; definition-sha256=4afd0c17f0ed7e2d74a8fbd3bb24db46c5fb7fddbdeda04a887d9440b9b023f4; exit=0; EXPECT=matched; output-sha256=b8b5cb15a7a86dc53948e7520bc855288bf2c8f53d8e5c41d46405a9c7504328; output-bytes=153; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

A red gate names its own broken assertion, so `DECISION FAIL D7: <reason>` is diagnosable without re-reading the oracle. Replay one decision by id, or the whole ledger with `--all`.

## Lot B gates (D34-D39)

The Lot B gates measure the agent-facing payment and discovery work: the x402 v1 envelope, the offline EIP-712 verification, the settlement rule, the MCP server, the published directory files, and the applied 30-day retention.

- [x] D34: the 402 body is an x402 v1 PaymentRequired document and a settled payment carries its receipt
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D34
  EXPECT: DECISION OK D34
  EVIDENCE: automatic-evidence=v1; definition-sha256=f3622bffcde5f8b5285f9ac7af9f6e96099b0649ee8462309e97c57a3ea0bb4d; exit=0; EXPECT=matched; output-sha256=c79e2b9df84b52e33cacb7e7913d1896fa376812502d73455580c9bd42c24e3b; output-bytes=1261; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries

- [x] D35: the EIP-712 signature is verified offline, before any network call
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D35
  EXPECT: DECISION OK D35
  EVIDENCE: automatic-evidence=v1; definition-sha256=2e8fac15e096f7f388926e8f4386d84c52338b303a8ac1e3c197f3ad52b102da; exit=0; EXPECT=matched; output-sha256=a2339b5809ee0b6ed0b923d594362656dff87f76df1b6ee72916a8633a24799f; output-bytes=4165; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries

- [x] D36: service is rendered only after settlement, and every settlement failure is a refusal
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D36
  EXPECT: DECISION OK D36
  EVIDENCE: automatic-evidence=v1; definition-sha256=3ce910ead5e127f7ef1a93762e8a95f4f8d40121c9ce22f7c7165a285157efa2; exit=0; EXPECT=matched; output-sha256=5849e54c160a9d5aab7692e6ce0520b5b66d6ee8460b7a805f7e378b673db359; output-bytes=2727; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries

- [x] D37: the MCP server serves one paid tool and its card describes the live endpoint
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D37
  EXPECT: DECISION OK D37
  EVIDENCE: automatic-evidence=v1; definition-sha256=a9e15fb72a7c59adab0fec08a09c61b25181d34a94cf877e82602c96be8d32f9; exit=0; EXPECT=matched; output-sha256=3e3dfddf5b894171b3ee6eb524082881c8d58160f865f2a927e746ed7950d078; output-bytes=1648; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries

- [x] D38: the directory files describe the live endpoint in a sourced format, with the payment primitives pinned
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D38
  EXPECT: DECISION OK D38
  EVIDENCE: automatic-evidence=v1; definition-sha256=a4501d13b7fa3263d2792905f5964f60fa7dc0c610d85875470f786256d9ade0; exit=0; EXPECT=matched; output-sha256=1e5bd1538c0c084ed3762e743cb9ddfa6085df5042911cb6fcca5931a8c26349; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D39: the announced retention is applied by a scheduled purge, not promised
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D39
  EXPECT: DECISION OK D39
  EVIDENCE: automatic-evidence=v1; definition-sha256=b00ae7972f143a4107192b75b586e15d487c95a31832dd3a19a6bd805c02139d; exit=0; EXPECT=matched; output-sha256=f3cf531ec6c2a09cfc66b377946c42d7be09d561b551f5a91effa697f35fc1c2; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

## Lot C gate (D40)

The Lot C gate measures the revenue split added once the operator's own paid acceptance tests had written two rows in the production registry: the ledger keeps every row, `/metrics/revenue` publishes the customer figure separately from the test figure, and the migration that marks the already-written rows is idempotent.

- [x] D40: a paid acceptance test is stored as a test row, the published real revenue ignores it, and the catch-up migration is idempotent
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D40
  EXPECT: DECISION OK D40
  EVIDENCE: automatic-evidence=v1; definition-sha256=cfc6ebe65a636e6ac7a6d31922d6d8882f1daa19bf0e4dbdc8e327c3cea52c9b; exit=0; EXPECT=matched; output-sha256=f1f10bceaf0c6d4a593de4ddb662c4c04e0dbb274d02eb7ea9c396dc2a0fc4aa; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

## Durcissement W2 gate (D41)

W1 a rendu le `200` dépendant d'un encaissement prouvé. W2 ferme la brèche suivante : un paiement accepté dont l'**inscription au registre échoue**. Le registre est ce qui prouve l'encaissement (D29) et ce qui alimente le revenu publié (D40) ; servir sans ligne produirait un revenu perdu *et* invisible — le client a payé, l'exploitant ne le saura jamais. La porte mesure les deux chemins (HTTP et MCP), le refus (`500` / erreur JSON-RPC `-32603`), la montée du compteur, sa publication par `/metrics`, et la non-régression d'une écriture réussie.

- [x] D41: a payment whose ledger write fails is not served -- 500 on /validate, JSON-RPC -32603 on /mcp -- and ledger_write_failures_total counts it
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D41
  EXPECT: DECISION OK D41
  EVIDENCE: automatic-evidence=v1; definition-sha256=009d4c60aa1781bd26209110094ff494750934c7773a9ce57bcecde1fa39fe2c; exit=0; EXPECT=matched; output-sha256=c5ab2e1765eb8775b355871a35ba0f9b2c863d56247602e4a444045c93bd30a2; output-bytes=675; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries

## Durcissement W3 gate (D42)

W1 a rendu le `200` dépendant d'un encaissement prouvé, W2 (D41) d'une ligne au registre. Les deux règles restaient mesurées *sous* la route : `tests/test_onchain.py` mesure la couche on-chain, `tests/test_payment.py` la forme de la preuve, et l'audit du trafic (§5.1) a précisément observé ce qu'aucune de ces mesures ne couvre — des `200` sur `POST /validate` sans ligne au registre, dans le régime du VPS (`TEST_MODE` absent). La porte mesure donc le contrat rendu, là où le client le rencontre : un transfert USDC confirmé est servi *et* inscrit au registre avec le hash confirmé, une transaction inconnue de la chaîne est refusée sans écriture, une chaîne injoignable est refusée sans écriture, et la recette déclarée reste le seul régime où la preuve à secret partagé ouvre un `200` sans interroger la chaîne. Elle vérifie aussi que le point ouvert de `AUDIT_TRAFIC_VPS.md` §5.1 ne reste pas consigné comme ouvert.

- [x] D42: the payment gate is measured at the route -- POST /validate serves a chain-confirmed transfer and records it, refuses an unknown transaction, refuses an unreachable chain, and only the declared test mode accepts the shared-secret proof
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D42
  EXPECT: DECISION OK D42
  EVIDENCE: automatic-evidence=v1; definition-sha256=597aac290dfed253ffd685278c2c4125c88ed0a810be5666c4695bc0e94b7626; exit=0; EXPECT=matched; output-sha256=05b2982476780c25290952b83367fad1ef53a10d2e309c4e494a7e59df5e9679; output-bytes=1399; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries


## Campagne commerciale gate (D43)

W3 a fermé la porte de paiement au niveau de la route. La campagne commerciale ouvre la première page **publique** du service : elle vend, donc elle doit être lisible par un inconnu — et pourtant rester aussi sobre qu'une page d'exploitation. La porte mesure les deux à la fois : `/pricing` répond `200` sans compte, s'indexe (`robots: index, follow`), se met en cache (`public, max-age=300`) et n'autorise aucun `form-action` ; la page ne charge **rien** (aucune ressource externe, aucun script) et sert la **même** feuille de style que `/dashboard`, importée depuis `app/web.py` — une seule feuille, donc les deux pages ne peuvent plus diverger. Elle vérifie aussi que les prix unitaires affichés sont **calculés** depuis le plan (0,0145 € / 2 000, 0,0079 € / 10 000), qu'aucun secret de configuration n'apparaît sur une page indexable, et que les témoignages d'**exemple** ne sont pas publiés tant que l'exploitant ne l'a pas décidé.

- [x] D43: /pricing is public, indexable and cacheable, loads nothing, shares the dashboard stylesheet, computes its unit prices, leaks no secret, and publishes no placeholder testimonial by default
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D43
  EXPECT: DECISION OK D43
  EVIDENCE: automatic-evidence=v1; definition-sha256=2708ed9dce9c0ac8df5d729483741dbe1c337b37d8b59cfb8b4d868f3b4ae660; exit=0; EXPECT=matched; output-sha256=95ed68eb59346b2ecfc8816f2609caad5f83341bab6a2b0691a2d66fcb0bf603; output-bytes=159; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries



## Inscription self-service gate (D44)

D43 a ouvert la première page publique ; D44 ouvre le **compte** qui va avec. La porte mesure ce qu'un client prête et qu'il faut lui rendre intact : son mot de passe (empreinte bcrypt coût 12, jamais en clair, refus explicite au-delà de 72 octets), sa clé API (`sk_live_` + 32 caractères, affichée **une seule fois** sur une page `no-store`, seule une empreinte restant en base, régénération qui invalide immédiatement l'ancienne), et ses chiffres (compteurs issus des validations réellement servies, estimation d'économies affichée avec sa formule). Elle vérifie aussi le plafond d'inscription par IP (5 par heure, `429` + `Retry-After`, repli local compté quand Redis est absent), la fermeture par défaut de `/compte` (401 + défi Basic, même message pour un email inconnu et un mauvais mot de passe) et le `503` explicite quand la base est injoignable — jamais des zéros décoratifs.

- [x] D44: signup and the customer area protect the password (bcrypt, never clear), the API key (shown once, stored hashed, regenerable), the usage counters (from served validations), the per-IP signup cap, the default-closed /compte and the explicit 503 when the database is down
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D44
  EXPECT: DECISION OK D44
  EVIDENCE: automatic-evidence=v1; definition-sha256=5f695f07cc9205220ea2863391f8482c7aad463fd68fa74a3b1c06699d8a91f6; exit=0; EXPECT=matched; output-sha256=e75d04926c6019d4b9e0e72538b1215bd78dd984d713a1d711043fe5eb2bc097; output-bytes=1268; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries


## Abonnement par clé API gate (D45)

D44 a ouvert le compte ; D45 fait servir la **clé** que ce compte a reçue. La porte mesure la route telle que le client la rencontre, avec une base de comptes réelle (SQLite jetable) et le fournisseur coupé : une clé valide est servie par le pipeline partagé, le compte est débité, `price_paid = 0.0` et `transaction_id = null`, et **aucune ligne** n'entre au registre USDC — un témoin positif (un paiement accepté en recette déclarée, sous le même double) prouve que le registre est bien branché, donc que l'absence est mesurée et non supposée. Une clé inconnue vaut `401` sans qu'aucune validation soit calculée, un essai terminé vaut `403`, un quota du mois consommé vaut `402`, et la fin de la fenêtre est vérifiée dans les deux sens : 105 validations datées du mois précédent plus une du 1ᵉʳ du mois à minuit donnent 106 à vie, 1 ce mois, et un service quand même. Un plan payant dont la date d'essai est passée reste servi (`trial_expire` vrai, `expire` faux). Quand la clé et un paiement sont présentés ensemble, `payment_result` n'est même pas appelé. Une base de comptes injoignable donne `503` (jamais `402`), sans calcul ni écriture. Enfin, la grille publiée sur `/pricing` (100 / 2 000 / 10 000 / illimité, « par mois ») doit être **exactement** celle appliquée par `accounts.PLANS`, et l'en-tête documenté (`X-API-Key` dans `README.md`, `SECURITY.md`, `/compte` et le code) doit être celui du code.

- [x] D45: POST /validate serves an API key within its plan -- valid key served and debited with nothing written to the USDC ledger, unknown key 401 without computing, expired trial 403, exhausted monthly quota 402, paid plan still served past its trial date, key wins over a concurrent payment, unreachable accounts database 503 and never 402, and /pricing publishes the enforced quotas
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D45
  EXPECT: DECISION OK D45
  EVIDENCE: automatic-evidence=v1; definition-sha256=a84fb8f031ff4d47248280315a6de1e3b864aaf2adaba35fa1afeb24e1a5ec51; exit=0; EXPECT=matched; output-sha256=827774a5f41909350da85cf82704a3277008db0a7922f63b32d3763d14f6f97c; output-bytes=1838; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries


## Essai gratuit public gate (D46)

D45 fait payer l'usage régulier ; D46 ouvre la porte d'entrée. La porte mesure la page telle qu'un visiteur la rencontre, le fournisseur étant remplacé par un double : `GET /validate-form` est publique, indexable, cachable cinq minutes et ne charge **rien** (aucun script, aucune ressource externe), et le plafond qu'elle annonce est celui du réglage. Un essai valide est servi par le **même objet** que la route payante (`perform_validation`, vérifié par identité), avance les compteurs `/metrics`, et n'écrit rien : le module n'importe ni `SessionLocal` ni `record_transaction`. Une adresse incomplète est refusée `400` **sans** appeler le fournisseur — et le témoin positif du même double (l'essai valide qui compte un appel) prouve que cette absence est mesurée et non supposée. La saisie est échappée, un résultat dégradé est annoncé comme dégradé et non comme une adresse invalide, et la réponse d'un essai est `no-store`/`noindex` parce qu'elle contient l'adresse du visiteur. Le plafond est éprouvé sur la valeur **configurée** (pas une valeur de test) : les essais autorisés passent, le suivant vaut `429` avec un `Retry-After` exact et borné par la fenêtre, la limite et un chemin vers `/signup` sont affichés, `validate_form_denied` s'incrémente, un second refus ne repousse pas la fenêtre du visiteur, et une autre IP garde sa propre fenêtre.

- [x] D46: GET /validate-form is a public, indexable page that loads nothing, and POST /validate-form serves a capped free trial -- the configured number of attempts per IP per 24h, then 429 with an exact bounded Retry-After and a signup path, with no write to the accounts database or the payment ledger, the same validation pipeline object as the paid route, escaped input, and a degraded result never presented as an invalid address
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D46
  EXPECT: DECISION OK D46
  EVIDENCE: automatic-evidence=v1; definition-sha256=78b4f93441138af2948472b5f098410513a1211982a050ba4b1e8f0126cbf8f8; exit=0; EXPECT=matched; output-sha256=5529134915f54fc059a23beeb80946c7f0950c606f4f8d411243e50c79f10d19; output-bytes=1514; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries



## Dégradé gardé cinq minutes gate (D47)

D46 a ouvert la porte d'entrée ; en écrivant le guide d'intégration (élément 4), un défaut du cache est apparu : `store_in_cache` écrivait **tout** résultat avec le même TTL de 30 jours, y compris `degraded: true`. Or « le fournisseur n'a pas répondu » (D23) n'est pas un verdict : le garder un mois transforme une panne passagère en réponse fausse d'un mois, sur une adresse que le client paie à chaque appel (ou consomme à son quota) et sans aucun moyen de forcer une vérification. La porte mesure la correction avec un **seul** double de cache qui retient les TTL demandés, ce qui rend l'absence de mensonge mesurable plutôt que déclarative : un résultat dégradé est écrit avec `CACHE_TTL_DEGRADED` (300 s) et un résultat vérifié avec `CACHE_TTL` (30 jours, témoin positif du même double) ; la réponse dégradée reste **servie depuis le cache** pendant ses cinq minutes — la protection mesurée par D23 (disjoncteur) et D25 (cooldown Redis) ne doit pas disparaître en corrigeant le TTL — et, l'entrée expirée, la même adresse est **re-vérifiée** et redevient un vrai verdict.

- [x] D47: a degraded result is cached for 5 minutes, not 30 days -- the paid route writes CACHE_TTL_DEGRADED for a degraded answer and CACHE_TTL for a verified one through the same cache double, the degraded answer is still served from cache within its short window, and the same address is verified again once it expires
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D47
  EXPECT: DECISION OK D47
  EVIDENCE: automatic-evidence=v1; definition-sha256=1da8ee4cc2a25802de136f9f9c39bec4086308d185bd77a6a34ba62067dc6c81; exit=0; EXPECT=matched; output-sha256=859bafbec3fe398e63f10e80d9b8a95c9154bfd762ee8e87f6e761b8dd89d9e9; output-bytes=966; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries


## Guide d'intégration gate (D48)

D46 a ouvert l'essai, D47 a corrigé ce qu'un essai dégradé laissait derrière lui ; D48 publie le **contrat**. Un guide est un contrat : s'il annonce un en-tête que le serveur ne renvoie pas, un prix qui n'est plus le sien, ou un code que la route n'émet jamais, il coûte une journée à l'intégrateur et fait douter du reste. La porte mesure donc la page réellement servie : publique (`200`, indexable, `max-age=300`), autonome (aucun script, aucune image, aucun `url(`, `form-action 'none'`), et **sans aucun secret** — la clé partagée et le mot de passe du tableau de bord sont cherchés littéralement dans le texte d'une page indexable par construction. Les faits publiés sont ceux du **code** : la base publique vient de `public_base_url` (un gabarit non remplacé fait rougir la porte), le prix et le plafond de l'essai gratuit sont lus dans la configuration, les trois en-têtes annoncés sont vérifiés **aussi** dans `app/routes/validate.py`, les quatre plans sont cités, et les huit codes de retour de la table sont tous présents. Enfin, un guide introuvable n'est pas un guide : `/guide` est atteint depuis `/pricing` et `/validate-form` via l'en-tête **partagé**, et `/pricing`, qui recopiait sa propre copie du `<nav>`, est aligné sur `site_head()` — une seule définition, comme la feuille de style en D43.

- [x] D48: /guide is a public, indexable, cacheable integration guide that loads nothing and leaks no secret -- the public base, price, free-trial cap, response headers and plans it publishes are read from the code and configuration, all eight documented status codes are present, and the page is reachable from the shared navigation which /pricing no longer duplicates
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D48
  EXPECT: DECISION OK D48
  EVIDENCE: automatic-evidence=v1; definition-sha256=8f2a71746ddc7b1a112e73ccf08a95751d9cc3729dae69790262d6cb043b77cb; exit=0; EXPECT=matched; output-sha256=bf07314daf704527a0f81bcda38045e8a0eb840d9395409f27dcb77ac1f9d238; output-bytes=244; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries




## Email de bienvenue gate (D49)

D48 publie le contrat ; D49 écrit la **promesse** qui l'accompagne. Un email commercial n'est pas une page : il ne se relit pas après envoi, et un premier client qui lit « 14 jours et 100 validations offertes » puis se voit refuser un appel a perdu confiance — c'est plus cher qu'un client perdu. La porte mesure donc le message réellement envoyé, chiffre par chiffre, **contre le code** : jours d'essai et quota du plan d'essai viennent d'`accounts`, les deux prix mensuels de la page `/pricing` (29 € / 2 000, 79 € / 10 000) doivent y figurer, et les quatre routes citées (`/signup`, `/compte`, `/guide`, `/validate-form`) doivent être montées par l'application. Elle verrouille ensuite la **voie** annoncée : c'est celle de la boutique (`X-API-Key`), et la porte **interdit** `X-PAYMENT`, `X-Payment-Proof` et `tx_hash` dans ce message — un commerçant n'a pas de portefeuille à brancher, et lui apprendre la voie des agents ferait échouer sa première intégration. Aucun secret n'a sa place dans un email, même privé : une clé `sk_live_` complète ou une valeur par défaut du dépôt fait rougir la porte, et le texte doit dire qu'une **empreinte** (pas une adresse) est conservée, avec la voie d'effacement. Enfin, les limites sont dites (aucun prélèvement automatique, un résultat dégradé qui « ne dit rien » de l'adresse), l'exemplaire « prêt à envoyer » ne contient plus aucune variable de fusion, et chaque variable du gabarit est expliquée dans la table des fusions.

- [x] D49: the shop welcome email promises exactly what the service applies -- trial days, offered quota and monthly prices come from the code and /pricing, the announced path is the API-key subscription and never a per-call payment header, no complete key or default secret appears, every cited route exists, the limits are stated (no automatic billing, degraded is not a verdict, only a hash is kept), the ready-to-send copy has no merge variable left, and every template variable is documented
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D49
  EXPECT: DECISION OK D49
  EVIDENCE: automatic-evidence=v1; definition-sha256=f8ccbc5872989c8ace2754062511c77160ae4bec5c8c88b1281590cf32c59166; exit=0; EXPECT=matched; output-sha256=234bf9a8998d784821f80be4c19e029143f019fc7a4c1e88c2b1d552e0c08cfa; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries


## Suivi des clients gate (D50)

D44 a créé les comptes, D45 a écrit les règles qui décident de servir, D46 a ouvert l'essai et D49 a écrit l'email d'accueil. D50 répond à la question suivante, celle qui décide du revenu : **parmi les comptes qui existent, lesquels sont en train de se perdre en silence, et que faut-il leur dire ?** Un tableau de suivi ne vaut que par son accord avec ce qui est réellement servi : s'il affiche « actif » un compte dont les appels reçoivent `403`, il rassure au lieu de décider — c'est pire que pas de rapport. La porte mesure donc l'accord, sur une base réelle : chaque état est lu dans les fonctions mêmes de la route payante (`accounts.plan_status`, `accounts.consumption`, `accounts.refusal_reason`) et les codes HTTP sont lus dans `REFUS_HTTP` ; un état « essai fini » ou « quota atteint » sans refus correspondant, et l'inverse, font rougir la porte. Elle vérifie aussi le cas que la règle D44 protège : un **client payant dont la date d'essai est passée** n'est pas « essai fini » (sinon le rapport appellerait au téléphone un client en règle). L'ordre publié est un ordre d'urgence : les comptes refusés maintenant viennent avant ceux qui le seront bientôt, et l'ordre est vérifié, pas annoncé. Un suivi de comptes ne doit contenir **aucun secret** : la porte cherche le préfixe de clé (présent, c'est ainsi qu'on reconnaît un compte au téléphone) et l'absence de la clé complète ; elle vérifie également que `app/suivi.py` ne fait que lire — ni `add`, ni `commit`, ni `delete` — car un outil de décision qui écrit change l'objet qu'il mesure. Enfin, un rapport vide n'est pas un « tout va bien » : sur une base injoignable ou sans table, `scripts/suivi_clients.py` doit sortir en code `2` et non présenter zéro comme un résultat, contrat porté par un vrai lancement du script dans la porte.

- [x] D50: the client follow-up reports, for the accounts really stored, which ones are served and which ones are refused -- each state agrees with the paying route (an expired trial or a consumed quota never appears without the matching refusal, and a paying account whose trial date has passed is not called an expired trial), the published order is the urgency order it claims, the report carries a key prefix but never a complete key, the module only reads the database, an unreachable or table-less database exits 2 instead of reporting an empty success, and the tool is documented in the README and recorded in DECISIONS.md
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D50
  EXPECT: DECISION OK D50
  EVIDENCE: automatic-evidence=v1; definition-sha256=411e079cfb66fa4c46d57ab82e37604c1f3a253bd3af0e2879a33529f1663733; exit=0; EXPECT=matched; output-sha256=ac89d6ed4891129ba5e3196a2f1f5d2367dc5e56840d0220b5463cd7d00589b8; output-bytes=17; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=d60a0eb116ce/23 entries

- [ ] D51: parse_address and standardize_address no longer mangle addresses -- unit keywords match whole words only (Stewart and Florida are not units), unit extraction, abbreviation expansion and re-capitalization apply to US only (a Reunion street is kept as typed with an empty line2), a leading St followed by another word stays St (Saint), the unchanged US cases still hold, and the fix is recorded in DECISIONS.md
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D51
  EXPECT: DECISION OK D51
- [ ] D52: the public texts (llms.txt, AGENTS.md, manifest limitations) describe both payment paths -- x402 without an account and the X-API-Key subscription -- no longer claim that no account or API key exists, and the Tiitoune Art welcome email no longer mentions a fragile piece
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D52
  EXPECT: DECISION OK D52
- [ ] D53: GET / serves the showcase page to browsers and keeps the exact previous JSON for programs, both with Vary: Accept, under the public CSP with form-action 'self', with no script, external resource or unfilled placeholder, the real trial form fields, and figures read from the code and the configuration
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D53
  EXPECT: DECISION OK D53
- [ ] D54: self-service signup only creates trial accounts: the signup form carries no plan, and posting starter, pro or enterprise still stores the trial plan with a trial end date
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D54
  EXPECT: DECISION OK D54
- [ ] D55: the published contact is the domain address address-validator@kder-ai.com, identical in .well-known/security.txt and in the configuration default, and PRIVACY.md no longer cites the old Gmail address
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D55
  EXPECT: DECISION OK D55
- [ ] D56: client_ip reads the last X-Forwarded-For entry (the one Nginx adds), a spoofed IP placed in front of the real one no longer opens new free-trial attempts, and nginx.conf replaces the incoming header with $remote_addr
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D56
  EXPECT: DECISION OK D56
- [ ] D57: the public site speaks six languages: French stays at the root and /en/, /zh/, /hi/, /es/, /ar/ serve their showcase page in their language and writing direction, with hreflang and language-selector links built by app/i18n.py, four photos served by the application under /images/ as image/jpeg with nosniff, a public CSP that adds only img-src 'self' (still default-src 'none', no script-src, no external URL), and prices converted from the euros in pricing.OFFRES within 1 % at the documented September 2026 rates
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D57
  EXPECT: DECISION OK D57
- [ ] D58: the integration guide speaks six languages: /guide (French) and /en/guide, /zh/guide, /hi/guide, /es/guide, /ar/guide are literal routes serving the validated design in their language and writing direction, with the same public headers, no script, no image, no external resource, the public base URL as the only absolute URL, a per-language sample key that is never a full sk_live_ key, and every figure (trial, caps, cache durations, x402 price and version, contact, base URL) injected from code and configuration
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D58
  EXPECT: DECISION OK D58
- [ ] D59: the free trial page speaks six languages: /validate-form (French) and /en/validate-form to /ar/validate-form answer GET and POST on literal routes, each form posts to its own language, the real pipeline result is shown in that language, a provider outage is never shown as an invalid address, and the per-IP cap is shared across languages with an exact Retry-After and a localized 429
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D59
  EXPECT: DECISION OK D59
- [ ] D60: the pricing page speaks six languages: /pricing (French) and /en/pricing to /ar/pricing are literal public routes, prices come from i18n.prix with the billed-in-euros notice, unit prices are the price divided by the accounts.PLANS quota with the division shown, trial length, x402 price and contact follow the configuration, the Enterprise button opens mailto: to the configured contact, Starter and Pro lead to the sign-up of the same language without a plan, and no testimonial is ever published
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D60
  EXPECT: DECISION OK D60
- [ ] D61: sign-up, the one-time key page and the client space speak six languages: /signup, /compte and /compte/cle (French) and their prefixed literal routes answer in the language of their path, the form never carries a plan field (D54), the key page shows the real key once with no-store and noindex while the database keeps only its hash, /compte stays behind HTTP Basic with no-store and shows only the key prefix, and the savings estimate uses the currency of the language
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D61
  EXPECT: DECISION OK D61
- [ ] D62: the client space has a real sign-in page in six languages and runs on a session: without a session /compte and /compte/cle redirect (303) to the sign-in page of their language and never send the HTTP Basic challenge, the __Host- session cookie is HttpOnly, Secure, SameSite=Lax and holds a random token of which only the SHA-256 hash is stored, LOGIN_MAX_FAILURES wrong passwords block sign-in for LOGIN_LOCK_MINUTES even for the right password, a form posted from another site is refused, signing out makes a copied cookie worthless, and the page shows the session, failure and lock values that the configuration applies
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D62
  EXPECT: DECISION OK D62
- [ ] D63: the forgotten-password flow works in six languages: the request page answers byte for byte the same whether or not the address has an account, the emailed link is built from PUBLIC_BASE_URL whatever the Host header, only the SHA-256 hash of its token is stored, it lasts PASSWORD_RESET_MINUTES and works once, the new password closes every session and the old password stops working, a form from another site is refused, and the link duration shown on the page and in the email is the configured one
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D63
  EXPECT: DECISION OK D63
- [ ] D64: the client space is a simplified dashboard in six languages: the month's figures come from customer_validations and the daily usage sums to the month counter, the greeting states the month's fact, the next step follows the account's real state in the documented order (trial ended, quota reached, paid plan, first call, trial running) and announces the exhaustion date only when it falls before the trial or month end, the savings formula, the key prefix only and the regeneration form stay, and nothing in the page is hard-coded
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D64
  EXPECT: DECISION OK D64
- [ ] D65: the five public pages in six languages carry exactly one SEO block in their head (canonical URL, Open Graph, icon, schema.org JSON-LD) whose only script is an application/ld+json data block and whose only absolute URLs are PUBLIC_BASE_URL ones, private pages carry none, structured-data prices are those of accounts.OFFRES, a hostile value cannot leave the JSON-LD, search and AI crawlers get the showcase at the root while programs keep the JSON, robots.txt, sitemap.xml (30 URLs with hreflang alternates) and favicon answer, and www redirects to PUBLIC_BASE_URL
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D65
  EXPECT: DECISION OK D65
- [ ] D66: /agents and /compare answer in six languages, public and indexable with their SEO block and in the sitemap, no unfilled field and no script outside the block; the service figures they show (x402 price and USDC units, MCP server URL, plan prices, quotas, trial) follow the configuration and none is written in their templates; every competitor price has its source as a plain domain and the page says when another service is the better choice; no template links to /mcp any more, the MCP server link leads to /agents
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D66
  EXPECT: DECISION OK D66
- [ ] D67: every public page visit and every /validate, /mcp and free-trial call leaves one tracking row (page or route, country from the local DB-IP file, referrer domain, robot family, real status, duration, MCP method) and no IP, postal address or full User-Agent is stored; a missing country database gives an unknown country, never an error; a database failure drops the batch without breaking a page; rows older than AUDIENCE_RETENTION_DAYS are purged; five wrong passwords from one IP lock /dashboard and /prospection with a 429 even for the right password
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D67
  EXPECT: DECISION OK D67
- [ ] D68: /dashboard shows the money, API and customer screens: cashed revenue excludes test payments, subscriptions are shown as to-be-invoiced at accounts.OFFRES prices, the month result is not computed until a cost is configured and then equals revenue minus costs with an alert when negative, API and customer figures come from the D67 tracking and the accounts tables; the page has no script and no external address, escapes names, offers the four periods, refreshes every minute and honours prefers-reduced-motion; a silent database is announced, not hidden
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D68
  EXPECT: DECISION OK D68
- [ ] D69: a browser without a session is sent to the /dashboard/connexion page while a script keeps the Basic challenge; the right credentials set a __Host- cookie (HttpOnly, Secure, SameSite=Strict) that opens the dashboard, a tampered, expired or pre-password-change cookie does not, five wrong passwords lock the IP even for the right password, a foreign form is refused and logout clears the cookie; the v2 page shows the site and SEO section with the dot world map, the journey and the registry, has no script and no external address and escapes names and referrers
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D69
  EXPECT: DECISION OK D69
- [ ] D70: a real browser form (Origin: null with Sec-Fetch-Site: same-origin, as Chrome sends under no-referrer) is accepted by the dashboard login and the client login, a browser saying cross-site or same-site is refused even with a matching Origin, a foreign Origin is refused, and spaces around the dashboard credentials are ignored
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D70
  EXPECT: DECISION OK D70
- [ ] D71: the dashboard shows the live block (visitors and calls of the last 5 minutes, latest events, scrolling ticker), the clicked menu entry lights up, the page refreshes every 30 s without replaying entrance animations and says when tracking is younger than the period; sparklines have a stroke; an accented login gives 401, not 500; a crashing tracked route is recorded as 500; in-site navigation is recognised through Sec-Fetch-Site and scripts are counted apart from visitors; country percentages use the real total; the success rate excludes 402 refusals
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D71
  EXPECT: DECISION OK D71
