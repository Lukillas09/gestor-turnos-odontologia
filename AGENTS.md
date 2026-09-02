# Purpose

This repository is a real Django application for an odontological practice. Treat it as a
production system that stores personal and clinical data, not as a CRUD demo. Prioritize
correctness, security, privacy, maintainability, and recoverability.

# Start here

Read the documents relevant to the change before editing:

- `README.md`: current product scope, setup, and main workflows.
- `docs/arquitectura.md`: application boundaries, permissions, and data flows.
- `docs/configuracion.md`: environment variables and operational defaults.
- `docs/seguridad_produccion.md`: security guarantees and production checklist.
- `docs/design-system.md`: UI components, responsive behavior, and accessibility.
- `docs/backups.md`: backup, restore, and clinical recovery requirements.
- `.github/workflows/ci.yml`: canonical quality and test requirements.

Nested `AGENTS.md` files point to deeper domain documentation. Read this file and every
applicable nested file for the path being changed.

# Mandatory engineering rule

Before implementing something new:

1. Search the repository for an analogous implementation.
2. Understand its invariants, callers, tests, and failure behavior.
3. Reuse or extend the existing pattern when it represents the problem.
4. Add a new abstraction only when the current architecture cannot represent the problem
   clearly.

Use these repository patterns as the first reference:

- External integrations: `app/turnos/integrations/`, Calendar sync, and notifications.
- Permissions: `app/usuarios/roles.py` and domain access policies.
- Transactional mutations: domain `services.py` modules.
- Reusable reads and query construction: domain `selectors.py` modules.
- Public protections: PostgreSQL-backed rate limiting and idempotency in `app/turnos/`.
- Clinical integrity and object access: `app/historias/`.

# ASK -> CODE -> REVIEW

## ASK / PLAN phase

When the user asks to analyze, design, investigate, plan, audit, review, or explicitly says
not to modify code yet, do not edit files.

1. Read all applicable `AGENTS.md` files.
2. Inspect the current repository and working tree.
3. Find analogous implementations.
4. Identify business and security invariants.
5. Identify the smallest affected file set.
6. Evaluate migrations, configuration, and deployment impact.
7. Evaluate permissions, privacy, transactions, concurrency, and external calls.
8. Identify focused, PostgreSQL, and E2E tests that apply.
9. Present a small coherent plan, including risks and assumptions.

Then stop. Do not implement until the user explicitly requests implementation, for example
with `implementa el plan`.

## CODE phase

When the user explicitly requests implementation:

1. Revalidate the branch, working tree, applicable instructions, and current code.
2. Confirm that the requested approach still fits the repository.
3. Implement the smallest complete change.
4. Reuse existing boundaries and helpers.
5. Add or update tests at the same time as behavior.
6. Run focused checks first.
7. Perform the mandatory self-review below.
8. Run the applicable canonical validations from CI.

Do not silently expand scope. Report a newly discovered adjacent issue instead of folding an
unrelated refactor into the change.

## REVIEW phase

Review the final diff, test results, migration/configuration impact, and unresolved risks.
Correct issues introduced by the change before reporting completion.

# Mandatory self-review

Review the complete diff as a senior reviewer. Explicitly check for:

- bugs, regressions, edge cases, and backwards compatibility;
- permission gaps, object-scope failures, and IDOR;
- privacy leaks or secrets and sensitive data in logs or responses;
- partial writes, incorrect transaction boundaries, and missing rollback behavior;
- race conditions, lock ordering changes, deadlocks, and stale revalidation;
- network, email, Calendar, or Storage calls while database locks are held;
- duplicated logic or bypassed services/selectors;
- N+1 queries and unbounded work;
- unsafe or unnecessary migrations;
- missing focused, PostgreSQL, permission, or E2E coverage;
- stale documentation or deployment requirements.

Fix issues caused by the change before declaring it done.

# Architecture conventions

- Models own persistence invariants and essential validation.
- Services own mutations and business use cases.
- Selectors own reusable reads and query construction.
- Forms own input validation and normalization at the boundary.
- Views own HTTP behavior, permissions, object scoping, and orchestration.
- Integrations own external-provider protocols and normalized errors.
- Templates and static assets own presentation only.

Do not put critical business rules in JavaScript or templates. Do not bypass an existing
service for a mutation. Keep views thin. Avoid broad refactors unless the user requests them.

# Clean code and design principles

Prioritize clarity, simplicity, cohesion, maintainability, testability, explicit names, and
clear responsibilities. Use KISS, DRY, YAGNI, and SOLID as design tools, not dogma. When in
doubt, choose the simplest solution that still preserves business rules, security, privacy,
concurrency, and the existing architecture. Never trade correctness for fewer lines of code.

## KISS

Implement the simplest complete solution. Avoid unnecessary abstractions, design patterns,
wrapper classes, inheritance hierarchies, unrequested configurability, and premature
generalization. Simple code must still include required validation, permissions, transactions,
locks, tests, error handling, and security controls.

## DRY

Do not duplicate knowledge or business rules. Before repeating logic, search for the existing
source of truth and reuse or extract it only when the shared abstraction is clear. Similar-looking
code is not necessarily the same rule; prefer small explicit duplication over incorrect coupling
between independent domains.

Avoid duplicating permission rules, availability calculations, business validation, integrity
logic, data normalization, security rules, and state transitions.

## YAGNI

Do not implement speculative functionality. Avoid unrequested options, future-only flags,
just-in-case parameters, hypothetical generalizations, and infrastructure without a demonstrated
need. Make the current requirement evolvable without implementing imagined requirements now.

## SOLID

- **Single Responsibility:** Keep each module, class, function, and service focused. Use the
  repository's existing layers before inventing new ones. Do not mix complex business rules into
  views, external calls into forms, domain rules into templates, or unrelated domains in one
  service.
- **Open/Closed:** Support extension when a real stable variation exists. Apply YAGNI first; do
  not build plugin, factory, or strategy systems for a single implementation.
- **Liskov Substitution:** Subclasses, mixins, and interchangeable implementations must preserve
  permissions, side effects, expected exceptions, return semantics, and transaction guarantees.
  Prefer composition when inheritance would be surprising.
- **Interface Segregation:** Keep contracts small and focused. Prefer focused functions and
  dependencies; use `Protocol` only when it adds concrete value. Do not create formal interfaces
  for every Python component.
- **Dependency Inversion:** Isolate external providers, email, Google Calendar, Storage, and HTTP
  details when that improves testability or substitution. Follow existing functions and factories
  before adding dependency-injection containers or architectural machinery.

## Priority when principles conflict

Use this order: correctness and business rules; security, privacy, and integrity; transactional
consistency and concurrency; existing repository architecture; KISS; YAGNI; cohesion and Single
Responsibility; DRY for genuinely duplicated knowledge; other SOLID principles with concrete
value; elegance or generalization. Never weaken security or correctness, and never overengineer
merely to demonstrate SOLID.

# Security and privacy defaults

Do not log or expose unnecessarily:

- DNI, email addresses, or phone numbers;
- OTP values, action tokens, OAuth tokens, passwords, or secrets;
- clinical content, attachments, or private signed URLs;
- complete public-request snapshots.

Prefer technical identifiers, counts, normalized error types, and neutral public responses.
Do not weaken authorization, concurrency, or fail-closed behavior to simplify code. Cache,
Redis, browser state, and frontend validation are never final authorities for sensitive
decisions.

# External integrations

Before adding or changing an integration, read:

- `app/turnos/integrations/`
- `app/turnos/integrations/post_commit.py`
- `app/turnos/google_calendar_sync.py`
- `app/turnos/notifications.py`
- `app/config/email_backends.py`

Preserve transaction boundaries, post-commit execution, timeouts, safe logging, normalized
errors, privacy, and idempotency where applicable. Do not keep database locks open during
network or Storage operations unless an existing documented invariant requires it.

# Feature flags

Do not enable experimental functionality automatically. Preserve existing defaults and check
configuration, migrations, tests, and rollback before changing a flag. Pay special attention
to:

- odontogram;
- smart scheduling;
- postoperative indications;
- shared clinical access.

# Definition of Done

`.github/workflows/ci.yml` is the canonical source for required commands and environments. Do
not maintain a duplicated command list here.

Depending on the change, DONE includes Black, Ruff, mypy, Django system checks, migration drift
checks, Django tests, coverage of at least 83%, PostgreSQL guarantee tests, Bandit, pip-audit,
text-encoding validation, and Playwright E2E smoke tests.

- Run focused tests before broader suites.
- Do not consider SQLite sufficient for clinical triggers, concurrency, public protections,
  or smart-scheduling guarantees that are implemented in PostgreSQL.
- Review E2E coverage when changing an important user flow.
- Never say all tests pass unless all relevant tests actually ran and passed.
- Report every required check that could not run and why.

# Final report

After CODE work, report:

1. What changed.
2. Important implementation decisions.
3. Checks that ran and their results.
4. Checks that did not run and why.
5. Migration, configuration, and deployment impact.
6. Remaining risks or follow-up work.

# Git safety

Preserve user changes and inspect a dirty working tree before editing. Do not use destructive
reset or broad restore operations. Do not force-push or rewrite history. Do not deploy. Commit
or push only when the user explicitly asks for that action.
