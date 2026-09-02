# Scope

These instructions extend the repository root `AGENTS.md` for all work under `app/turnos/`.

# Read first

- `../../docs/flujo-turnos.md`
- `../../docs/agenda_inteligente.md`
- `../../docs/arquitectura.md`
- `../../docs/seguridad_produccion.md`

# Core invariants

The browser is not authoritative for availability, duration, operational margin, appointment
type, or smart-scheduling candidate. Derive these values on the server and revalidate them at
the final mutation boundary.

Pending and confirmed appointments block availability; cancelled appointments do not. Preserve
public booking windows, minimum notice, active dentist/patient checks, agenda exceptions, and
overlap validation.

# Concurrency

Before changing create, confirm, cancel, or reschedule flows, inspect:

- current `transaction.atomic()` boundaries;
- agenda locks and `select_for_update()` calls;
- the documented lock order in `../../docs/arquitectura.md`;
- exception and overlap checks;
- `tests_concurrency.py` and relevant PostgreSQL suites.

Do not casually change lock ordering. Revalidate after acquiring locks. Do not replace database
guarantees with cache state. Validate meaningful concurrency changes against real PostgreSQL.

# Public booking

Preserve these guarantees:

- Public submissions never overwrite trusted patient data automatically.
- `SolicitudTurnoPublica` remains an auditable snapshot separate from `Paciente`.
- DNI alone is not authentication.
- A proposed email is not a verified identity or OTP destination.
- OTP uses an allowed persisted contact.
- Public responses remain neutral and non-enumerable.
- Sensitive mutations require the established session/token/POST/CSRF protections.

Do not expose occupied intervals, patient data, algorithm scores, or technical scoring reasons.

# Public protections

PostgreSQL is authoritative for rate limits, idempotency, OTP state, and sensitive public
authorization. Do not reintroduce Redis, `LocMemCache`, generic cache state, or frontend state as
an authority. Preserve fail-closed behavior and neutral HTTP 503 responses when the database
cannot guarantee protection.

Read `public_access/` and `solicitudes_publicas/` before changing any public flow.

# Smart scheduling

Keep scheduling deterministic and server-authoritative. Never trust duration, margin, score, or
candidate validity sent by the client. Recalculate the final candidate without cache under the
existing transactional protection.

When `TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED` is off, preserve legacy behavior unless the user
explicitly requests a change. Preserve appointment snapshots so later configuration changes do
not rewrite historical bookings.

# Integrations

Email and Google Calendar must not prolong agenda locks. Read before changing them:

- `integrations/post_commit.py`
- `integrations/google_calendar.py`
- `google_calendar_sync.py`
- `notifications.py`
- `../config/email_backends.py`

Keep external work post-commit, time-bounded, independently recoverable, and free of PII in logs
or provider identifiers.

# Tests

Map changes to the existing suites:

- General turn and agenda behavior: `tests.py`.
- Public protection and OTP behavior: `tests_public_protection.py`.
- Internal object scope: `tests_internal_availability_permissions.py`.
- Smart scheduling: `tests_smart_scheduling.py`.
- Post-commit behavior: `tests_post_commit_integrations.py`.
- Google Calendar and email: `tests_google_calendar.py` and `tests_email_api.py`.
- Concurrency: `tests_concurrency.py`.
- Browser workflows: `tests_e2e/`.

Run focused tests first. SQLite tests do not validate PostgreSQL concurrency, public-protection,
or smart-scheduling guarantees.
