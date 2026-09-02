# Scope

These instructions extend the repository root `AGENTS.md` for all work under `app/historias/`.

# Read first

- `../../docs/historia_clinica_inmutable.md`
- `../../docs/arquitectura.md`
- `../../docs/seguridad_produccion.md`
- `../../docs/backups.md`
- `access_policy.py`
- `services.py`
- `integrity.py`
- `migrations/0005_historia_inmutable_esquema.py`
- `migrations/0006_migrar_historias_legacy.py`
- `migrations/0007_protecciones_postgresql.py`

# Clinical data rules

Treat all clinical data as highly sensitive. Do not log notes, diagnoses, attachment contents,
snapshots, or unnecessary patient identifiers.

Require object-level authorization before opening data or Storage. Knowing a primary key is not
permission. Preserve the combined checks for general permission, active patient association,
clinical read policy, write ownership, and audited emergency access. Return the established 404
or 403 behavior without leaking object existence.

# Immutability

Do not edit a finalized history entry. Make later corrections through append-only amendments.
Do not physically delete finalized entries, versions, amendments, or clinical attachments.

Do not bypass protections through QuerySet updates, bulk operations, admin actions, fixtures, or
direct view writes. Route clinical mutations through `services.py` and preserve model and
database defenses.

# Integrity

`CLINICAL_INTEGRITY_HMAC_KEY` is a stable independent secret. Do not log it, persist it in the
database, include it in manifests, or regenerate it to make verification pass. HMAC is an
integrity seal, not a digital signature.

Treat changes to canonical serialization, snapshot schemas, hash chaining, or legacy
initialization as high risk. Preserve historical verification and recovery before introducing a
new format or key strategy.

# Clinical storage

Keep attachments private. Do not introduce permanent public URLs or call Storage before object
authorization. Preserve controlled downloads, safe filenames, persisted SHA-256, upload
validation, and backup/restore compatibility.

Do not silently deliver incomplete exports. Keep private paths, signed URLs, binary contents,
and sensitive metadata out of logs and manifests unless the documented format requires a safe
technical reference.

# Audit

Preserve clinical audit events for authorized and denied operations. Keep audit reasons neutral;
do not copy clinical content into them. Do not add a read or write path that bypasses
`access_policy.py`.

# PostgreSQL guarantees

PostgreSQL triggers are part of the security model. SQLite does not validate append-only rules,
finalized-record immutability, delete protection, or related concurrency guarantees. Run the
PostgreSQL clinical suite for changes that touch those areas.

# Schema changes

Before changing a clinical model:

1. Inspect existing migrations and PostgreSQL triggers.
2. Evaluate persisted and legacy data.
3. Preserve backwards compatibility and audit evidence.
4. Evaluate complete database plus private-Storage backup and restore.
5. Design a forward migration and rollback/incident procedure.

Do not edit old applied migrations. Use `tests.py`, `test_inmutabilidad.py`, and PostgreSQL CI
coverage to validate changes.
