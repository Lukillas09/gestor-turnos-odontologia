# Scope

These instructions extend the repository root `AGENTS.md` for all work under
`app/indicaciones/`.

# Read first

- `../../docs/indicaciones_postoperatorias.md`
- `../../docs/historia_clinica_inmutable.md`
- `../../docs/backups.md`
- `../../docs/seguridad_produccion.md`

# Clinical content and states

Do not generate diagnoses, prescriptions, medication instructions, or medical advice. Content
must come from the treating professional or an approved versioned template.

Preserve the state model:

- `BORRADOR` is editable by the responsible authorized professional.
- `EMITIDA` is immutable and retains snapshots, PDF, SHA-256, and HMAC integrity data.
- `ANULADA` remains preserved and immutable.

Correct an issued document by annulment and replacement. Do not overwrite or physically delete
the original document, PDF, template version, or audit evidence.

# Access, integrity, and storage

Preserve active patient association, object-level clinical scope, and professional write
ownership. Do not treat administrative permission as clinical access.

Keep PDFs private. Do not introduce permanent public URLs or direct unauthenticated downloads.
Preserve controlled download views, safe filenames, hashes, integrity seals, private Storage,
and database plus Storage backup compatibility. HMAC remains an integrity seal, not a digital
signature.

# Email and concurrency

Send only to the persisted allowed and verified patient contact. Never use a proposed public
email as a verified destination.

Keep network and Storage I/O outside database locks where the current flow does so. Preserve
post-commit delivery, short claim/finalization transactions, retry limits, idempotency, and
concurrency protection. Do not expose recipient data, document content, PDF bytes, provider
responses, or secrets in logs.

# Feature flag and tests

Do not enable `INDICACIONES_POSTOPERATORIAS_ENABLED` automatically. Activation requires the
documented migrations, private Storage, email, backup, permission, and PostgreSQL validation.

Use the existing `tests/` suites for models, services, forms, views, Storage, email transactions,
commands, and PostgreSQL triggers. Run `tests_e2e/` when changing the user flow. SQLite does not
validate PostgreSQL immutability or concurrency guarantees.
