+++
title = "Machine-readable env dump subcommand for deploy.toml"
source = "deployer"
captured = "2026-08-21"
closed = "2026-09-22"
+++

Promoted 2026-09-22 from someday-maybe (captured 2026-08-21 out of 53j-4a).
Repo-local phase; no fleet family, nothing blocked-on.

**Current state.** `Deployer.print_environment_config`
(`src/deployer/deploy/deployer.py`) narrates the merged environment for a
human reading the deploy log: `KEY=value`, `(unset)` for an empty string,
no quoting. Its own docstring records why that is not a parseable encoding
and says a machine-readable dump "would need its own subcommand with real
quoting". Nothing in the fleet parses the narration, and nothing should.

**Scope.** A new read-only subcommand on `bin/deploy.py` — working name
`env` — that prints the merged environment for one environment in a
format a shell or dotenv parser reads back exactly: `--format dotenv`
(default; `KEY='value'` with single-quote escaping, empty string as
`KEY=''`) and `--format json` (a flat object, `ensure_ascii=False` per the
project's read-back convention in CLAUDE.md). It prints environment
variables only — the `[secrets]` half is a disjoint route by ADR
(DECISIONS.md 2026-01-21) and the subcommand refuses any option that would
pull it in. No AWS calls beyond what loading the resolved config already
makes (`${tofu:...}` resolution).

**Approach.** Reuse `get_environment_variables(ctx)` exactly as
`print_environment_config` does, so the two never disagree about what
deploys; the encoders are two small pure functions with unit tests on the
awkward values (empty, `(unset)` as a literal, quotes, newlines, non-ASCII,
ints and bools from TOML). Register the command the way the other
`deploy.py` subcommands are registered; document it in
`docs/DEPLOYMENT-GUIDE.md` next to where the deploy log's environment
block is described, with the one-line warning that the log block is not
the parseable form.

**Complexity**: Low-Medium.

### Sub-phases
- **9-1 — the two pure encoders (dotenv, json) with unit tests on empty, literal (unset), quotes, newlines, non-ASCII, TOML ints and bools** **done** encode_dotenv/encode_json plus stringify_environment shared with build_task_definition; 20 tests including a real sh source round-trip (e9c0499)
- **9-2 — the deploy.py env subcommand: same get_environment_variables route as print_environment_config, --format, secrets refused by construction; CLI test through the Click runner** **done** deploy.py env ENV --format dotenv|json; stdout holds only the document, secrets unreachable by construction, invalid shell names refused in dotenv; 10 CliRunner tests (1692166)
- **9-3 — DEPLOYMENT-GUIDE.md: document the subcommand and state that the deploy log's environment block is narration, not the parseable form** **done** DEPLOYMENT-GUIDE Dumping the environment variables section, CLAUDE.md Common Commands, print_environment_config docstring re-pointed (a5e28d9)

### Outcome

Closed 2026-09-22. `deploy.py env ENV [--format dotenv|json]` prints the
merged environment as a parseable document and nothing else on stdout;
values pass through the same `stringify_environment` the task definition
uses, so the dump equals what deploys; secrets are unreachable by
construction. 30 tests including a real `sh` round-trip. Commits
`e9c0499`, `1692166`, `a5e28d9`. A per-service dump was left out of scope.
