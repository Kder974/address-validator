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
  EVIDENCE: automatic-evidence=v1; definition-sha256=f3622bffcde5f8b5285f9ac7af9f6e96099b0649ee8462309e97c57a3ea0bb4d; exit=0; EXPECT=matched; output-sha256=65e67a6f8f7fa4d2a80d7e119fdb66101394baec7724a94d3db9c5a49d4f6e0f; output-bytes=1774; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D35: the EIP-712 signature is verified offline, before any network call
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D35
  EXPECT: DECISION OK D35
  EVIDENCE: automatic-evidence=v1; definition-sha256=2e8fac15e096f7f388926e8f4386d84c52338b303a8ac1e3c197f3ad52b102da; exit=0; EXPECT=matched; output-sha256=ffc6c1d6dce2a54187f579cacaf3a2846cd2b1116bacc11171bce1a3a3b6ae15; output-bytes=4225; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D36: service is rendered only after settlement, and every settlement failure is a refusal
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D36
  EXPECT: DECISION OK D36
  EVIDENCE: automatic-evidence=v1; definition-sha256=3ce910ead5e127f7ef1a93762e8a95f4f8d40121c9ce22f7c7165a285157efa2; exit=0; EXPECT=matched; output-sha256=38421bebf93eacff39e90ec5ad463c066538a59fd6a7b6b252f25c9300019fc2; output-bytes=2787; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

- [x] D37: the MCP server serves one paid tool and its card describes the live endpoint
  CHECK: .\.venv\Scripts\python.exe scripts\decisions_check.py D37
  EXPECT: DECISION OK D37
  EVIDENCE: automatic-evidence=v1; definition-sha256=a9e15fb72a7c59adab0fec08a09c61b25181d34a94cf877e82602c96be8d32f9; exit=0; EXPECT=matched; output-sha256=85cbaf9cff7c86458c7149814f81fd58edb68bef9b67049df4dfa915d23f448a; output-bytes=1708; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\kder_\mon-projet-agent\address-validator; path=dec308870448/20 entries

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
