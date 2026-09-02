# Scope

These instructions extend the repository root `AGENTS.md` for all work under `app/pacientes/`.

# Read first

- `../../docs/arquitectura.md`
- `../../docs/seguridad_produccion.md`
- `../usuarios/roles.py`
- `models.py`
- `services.py`
- `tests.py`
- `../turnos/solicitudes_publicas/services.py` when changing public intake behavior.

# Identity and privacy

Treat DNI and contact details as sensitive. Do not log them unnecessarily or expose them in
authorization failures.

Preserve the separation between the persisted patient, the public-request snapshot, and a
verified contact. Publicly submitted data must not overwrite trusted patient records
automatically. A proposed email is not a verified identity or OTP destination.

# Archiving

Archiving is not deletion. Preserve patient data, histories, attachments, turns, and associations
for audit and continuity. Do not physically delete patients.

An archived patient must not regain active access or operations implicitly through a new public
request, turn, association, clinical record, or direct URL. Reactivation remains an explicit,
authorized, audited use case with a reason.

# Object scope

Visibility depends on role and active `PacienteOdontologo` association. General permission does
not grant access to every patient. Preserve queryset scoping before object lookup and the current
404 behavior for out-of-scope objects.

Do not create associations, clinical records, or other state as a side effect of opening a direct
URL.

# Clinical and administrative boundaries

Do not equate administrative patient management with clinical read or write access. Changes to
the odontological record, history summaries, attachments, emergency access, or indications must
also follow `../historias/AGENTS.md` and the policies in `../historias/access_policy.py`.

# Associations and tests

Patient-dentist association is part of access control. Reuse `services.py`,
`../usuarios/roles.py`, and the existing access policy. Do not create implicit associations
unless the documented business flow explicitly requires one.

Cover changes with the relevant tests in `tests.py`, `../usuarios/tests.py`, and public-request
tests under `../turnos/`. Include archived-patient, inactive-association, direct-URL, 403/404, and
cross-dentist cases as applicable.
