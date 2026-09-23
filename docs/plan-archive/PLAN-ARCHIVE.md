# deployer Plan (Archive)

The register of closed phases, maintained by fileplan (`plan.toml`
`[states.plan]`): every closed phase gets a `## N. Title` heading here,
minted by `archive` at its ordered place. Each entry carries a short
summary of the outcome and a pointer to the full record. The register
stays bounded by rotation (see
[PLAN-METHOD.md](../PLAN-METHOD.md#rotating-the-register)): the oldest
entries are cut into dated segment files and `first-number` is raised.

The floor is 1: before the 2026-09-18 review every phase this repo
worked was queued and numbered by claude-meta, and those records — Phase
53, Phase 54, and all the pre-numbering repo-local work — live in the
rotated segment [PLAN-ARCHIVE-2026-09.md](PLAN-ARCHIVE-2026-09.md). Their
headings are claude-meta numbers or unnumbered, so they were rotated out
whole rather than reheaded into this register; nothing here re-mints them.

Live items are one file each in [plan/](../plan/); what each state and
transition means is in [PLAN-METHOD.md](../PLAN-METHOD.md).

## 1. Operations and governance: an owner, the console-only checks, and the password flag

Closed 2026-09-22. Filed by the 2026-09-18 comprehensive review. An
Ownership block (owner and escalation contact) now heads
`docs/operations/README.md`; GOVERNANCE.md names the real `ecs-run.py exec`
subcommand; `bin/cognito.py create|reset-password` take `--password-stdin`
in place of `-p/--password` (17 characterization tests, residual aws-CLI
argv exposure recorded in GOVERNANCE R4 and filed as an idea); the four
AWS-side runnable checks were assigned to the app repos' own checkpoints.
Commits `695c046`, `13e498b`, `68ab618`, `8a1d4bc`. Full record in
`items/`.

## 2. Security gates and static-analysis config: the bandit skips, the reporter's name, and what check gates on

Closed 2026-09-22. Filed by the 2026-09-18 comprehensive review. Bandit
skips now pair with ruff's per-file-ignores and the report-only target is
`security-report`, both landed by the concurrent claude-meta fleet sweep
(`98b9baf`, `931544a`; fileplan-claude-meta:77 and :78, closed the same
day). The operator chose to gate `make check` on the read-only secrets scan
(`779454f`). Full record in `items/`.

## 3. Docs and plan plumbing: example paths, the project memory, the mdformat pathspec, and the blocked-on key

Closed 2026-09-22. Filed by the 2026-09-18 comprehensive review. The
`blocked-on` key is declared with its method-doc hooks (`9101134`,
deployer's half of fileplan-claude-meta:89); the project memory is
pointers (`be1443d`); example paths in docstrings and `--help` are generic
(`ad7a573`); the mdformat pathspec and front-matter workaround were settled
by the fleet sweep (`7d3c042`, `0357299`; fileplan-claude-meta:84 and :85).
Full record in `items/`.

## 4. OpenTofu provider pinning and the one gate this review never ran

Closed 2026-09-22. Filed by the 2026-09-18 comprehensive review; operator
answer b16 ("pin and commit only"). The aws `~> 6.0` floor now reaches
environments through `environments/deployer.tf` and the five templates that
own their terraform block (`037e649`, `b9b469e`); the root provider lock is
tracked at aws 6.66.0 (`4a31400`); `make tofu-validate` ran for the first
time against the real tree and returned 27 successes. Environments holding
a 5.x lock need `tofu init -upgrade` before their next plan. Full record in
`items/`.

## 5. Before the next apply: production RDS protections and the staging scheduler's new failure signal

Closed 2026-09-22. Filed by the 2026-09-18 comprehensive review.
`environments/deployer.tf` now declares and passes the four production RDS
protections (previously silently ignored from an environment's tfvars), the
production tfvars example carries the production values, and the docs name
the real variables (`fcb3c38`). The staging scheduler's 500-on-failure
behaviour and its alarm consequence are documented for the next apply
(`1be49e3`). Two follow-ups captured as ideas: the same gap in the shared-app
module, and two PRODUCTION.md settings that are not settable. Full record in
`items/`.

## 7. Characterization tests for the four largely unpinned bin/ CLIs

Closed 2026-09-22. Filed by the 2026-09-18 comprehensive review. The four
bin/ CLIs the review named as the repo's coverage slack are pinned:
link-environments 17→97%, ops 54→99%, environment 22→100%, resolve-config
34→100%, no production code changed, `fail_under` 81→86. Commits `d4743cb`,
`808fea7`, `c8d486d`, `8e8f61f`. Surprises the tests pinned are filed as an
idea. Full record in `items/`.

## 8. Developer Experience: Tofu Placeholder Map Indexing

Closed 2026-09-22. Promoted from someday-maybe the same day.
`${tofu:NAME.KEY}` (nested keys allowed) indexes map outputs in the
resolver, with fail-fast errors naming the placeholder, the walked prefix
and the available keys; both placeholder forms share one walk helper; the
existing behaviour was pinned by characterization tests first. Commits
`f593233`, `e8f9b4c`, `446bce6`. Follow-up idea: migrate the templates to
the dotted form and retire the per-key bucket outputs. Full record in
`items/`.

## 9. Machine-readable env dump subcommand for deploy.toml

Closed 2026-09-22. Promoted from someday-maybe the same day (captured
2026-08-21 out of 53j-4a). `deploy.py env` dumps the merged environment as
dotenv or JSON with real quoting, stdout holding only the document, secrets
excluded by construction, values stringified by the same helper the task
definition uses. Commits `e9c0499`, `1692166`, `a5e28d9`. Full record in
`items/`.

## 10. ALB WAF rate rule aggregates by TCP source IP behind CloudFront

Closed 2026-09-22. Promoted from someday-maybe the same day (captured
2026-09-11). Behind CloudFront the rate rule now counts per viewer via an
`x-viewer-ip` header a CloudFront Function writes, gated on a new
opt-in ALB ingress restriction to the CloudFront origin-facing prefix
list; the module refuses the forgeable combination and the root warns on
plan. X-Forwarded-For was rejected on evidence. Commits `6773fbb`,
`612dc7f`, `5dae323`, `93b651e`, `8ffc944`. Two ideas captured: the
origin-verify header and the allowlist/geo rules. Full record in `items/`.
