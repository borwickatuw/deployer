+++
title = "OpenTofu provider pinning and the one gate this review never ran"
review = "2026-09-18"
number = 4
+++

The aws provider floor raised in `f1ea933` is not in force where tofu
actually runs, `.terraform.lock.hcl` is gitignored so no provider version is
reproducible across machines, and `make tofu-validate` — the target this
review's lane added — has never been run against the real tree. The three are
one piece of work because they share a precondition: nothing can be verified
until the repo's on-disk `.terraform` is refreshed past the 5.100.0 it still
holds.

Operator decision on 2026-09-21, answer `b16` (`InfraOdds`, **"pin and
commit only"**): fix the two deployer provider items; deployer-environments'
alerting and havoc-staging's media-bucket versioning stay as filed
carry-forwards in that repo with a production-rollout trigger. So sub-phases
4-1 through 4-3 are decided work, not open questions.

Evidence for each is in claude-meta
`docs/investigations/2026-09-18-comprehensive-review-run-ledger.md`,
`## Unit records` → `### deployer` → `#### Pending operator`, under the TOFU
and SECURITY leads.

Per-repo item; no fleet family. Sequence it 4-4 last — the gate cannot be
green until the floor it is validating is the one the repo means.

### Sub-phases
- **4-1 — TOFU (adjudication, answer b16, zero-risk half) — "The aws provider floor raised in f1ea933 is still not in force where tofu actually runs: versions.tf never reaches an environment directory, so live environments resolve the aws provider unconstrained." Evidence: versions.tf is the only file in the repo with an aws version constraint; src/deployer/utils/environment.py:117-122 symlinks only modules, main.tf, variables.tf and outputs.tf into the environments directory — not versions.tf. Proof of consequence: all 25 module directories validated standalone exit=0 in the scratch sweep precisely because, with no constraint present, tofu picks the newest 6.x; that is also how blocked_encryption_types applied successfully on 2026-08-26 while the deployer root module could not validate at all. This sub-phase is the half the lead called zero-risk: add a required_providers aws '~> 6.0' block to each templates/<name>/main.tf.example (7 files), affecting only newly created environments. (Ledger: deployer → Pending operator, [adjudication, TOFU].)**
- **4-2 — TOFU (adjudication, answer b16, the half that needed sign-off — now signed off) — add the same required_providers aws '~> 6.0' block to environments/deployer.tf, which create_deployer_tf_symlink links into every existing standalone environment directory. The lead held this back because it can force a provider upgrade in live environments the run could not inspect; b16 ('pin and commit only') decides it. Land 4-1 first, then this, then re-run the scratch-copy validation the TOFU checkpoint used rather than mutating the operator's .terraform. (Ledger: deployer → Pending operator, [adjudication, TOFU], same bullet as 4-1.)**
- **4-3 — TOFU / GIT Practice 2 (adjudication, answer b16) — ".terraform.lock.hcl is gitignored, so no provider version is reproducible across machines or across operators." Evidence: .gitignore lists .terraform.lock.hcl under the 'OpenTofu / Terraform' heading alongside tfstate files and .terraform/; git ls-files returns no lock file. The practical effect was visible in the checkpoint itself — the on-disk lock dated Aug 7 pinned aws 5.100.0 while the config committed Aug 26 required 6.x, and nothing in the repo recorded which provider version the code was written against. b16 says track it: the lock holds provider checksums, no secrets and no state. Note the reach — each deployer-environments directory has its own lock, and deployer-environments carries a ratified leave-standing (answer c11) that its .gitignore disagrees with GIT.md Practice 2's OpenTofu block; that repo's decision is its own. The guide half (a lock-file line in TOFU.md's Audit Checklist next to 'State files in git') is claude-meta's. (Ledger: deployer → Pending operator, [adjudication, TOFU].)**
- **4-4 — SECURITY (carry-forward) — "make tofu-validate is still unverified at HEAD; it was never run against the real tree by this run's verifier." Verifier's return, verbatim: 'NOT RUN BY ME: make tofu-validate — it runs tofu init against the real tree and would mutate the operator's .terraform/provider lock; the TOFU checkpoint ran it in a scratch copy for that reason. Unverified at HEAD.' make security-checkov is green at HEAD ('Passed checks: 499, Failed checks: 0') but does not substitute for tofu validate. This is the same shape as both of this lane's refutations: a gate outside make check that nothing re-ran after later commits. Precondition, itself a held-back behaviour-change from the run: after f1ea933 the operator must run 'tofu init -upgrade' once in /Users/borwick/code/deployer before validate or plan works locally again, because the on-disk .terraform holds aws 5.100.0 which the new '~> 6.0' constraint excludes; the run deliberately did not run it, since it rewrites the operator's gitignored provider lock. Recovery/verification command: cd /Users/borwick/code/deployer && tofu init -upgrade && env -u VIRTUAL_ENV make tofu-validate, expecting 27 'Success!' lines. The same refresh is needed in any deployer-environments directory still holding a 5.x lock. (Ledger: deployer → Pending operator, [carry-forward, SECURITY, fixup] and [behaviour-change, TOFU].)**
