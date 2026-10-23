---
name: nuevo-crud-django
description: Crear o extender un CRUD interno de Django (modelo, permisos, alcance por objeto, selectors, services, forms, vistas, URLs, plantillas y tests) siguiendo los patrones reales de este repo. Usar cuando se pida una nueva entidad administrable, un listado/alta/edición/archivado o pantallas de gestión en el panel interno.
---

# Nuevo CRUD Django

Antes de escribir código, leé `AGENTS.md`, el `AGENTS.md` anidado de la app afectada (si existe)
y `docs/arquitectura.md`. Si el CRUD toca pacientes, historias, indicaciones u odontogramas, las
reglas clínicas de esas apps prevalecen sobre esta guía.

## 1. Ubicación

- Preferí una app de dominio existente (`pacientes`, `turnos`, `consultorio`, ...).
- Una app nueva requiere: entrada en `INSTALLED_APPS` (`app/config/settings.py`), `include()`
  en `app/config/urls.py` y agregar sus tests a la lista del job `django-tests` en
  `.github/workflows/ci.yml` (si no, CI no los ejecuta).

## 2. Capas y referencias reales

| Capa | Responsabilidad | Referencia |
| --- | --- | --- |
| Modelo | Persistencia e invariantes esenciales (`clean`, constraints) | `app/turnos/models.py` (`TipoTurno`) |
| Permisos | Funciones `puede_*` por rol | `app/usuarios/roles.py`, `app/indicaciones/permissions.py` |
| Mixins | Control de acceso en vistas | `app/usuarios/mixins.py` (`RolRequeridoMixin`) |
| Selectors | Lecturas reutilizables con `select_related` | `app/indicaciones/selectors.py` |
| Services | Mutaciones con reglas de negocio | `app/pacientes/services.py` (`archivar_paciente`) |
| Forms | Validación y normalización de entrada | `app/turnos/forms/tipos_turno.py` |
| Vistas | HTTP, permisos, alcance, mensajes | `app/turnos/views/tipos_turno.py`, `app/pacientes/views.py` |
| URLs | `app_name` + nombres estables | `app/pacientes/urls.py` |

### Modelo
- Campos de auditoría `creado_por` / `actualizado_por` cuando quien modifica importa (ver `TipoTurno`).
- Si la entidad tiene historia o relaciones clínicas, **archivar en vez de borrar**
  (`activo`, `archivado_en`, `archivado_por`, motivo), como `Paciente`.
- Generá la migración con `makemigrations` y revisala: nada destructivo sin plan explícito.

### Permisos y alcance por objeto
- Agregá la regla como función en `roles.py` (o `permissions.py` del dominio); no la repitas en
  vistas ni plantillas.
- Mixin que hereda de `RolRequeridoMixin`: usuario anónimo → login; autenticado sin permiso → 403.
- Limitá el queryset **antes** de buscar el objeto (`limitar_pacientes_por_usuario`,
  `limitar_turnos_por_usuario`) y usá `get_object_or_404` sobre ese queryset: fuera de alcance
  responde 404, no 403, para no confirmar existencia (IDOR).

### Mutaciones
- Catálogo simple sin reglas de negocio: `form.save(commit=False)`, setear auditoría y `save()`
  en la vista, como `TipoTurnoCreateView`.
- Transiciones de estado o reglas de negocio: función en `services.py` con `transaction.atomic()`,
  `select_for_update()` del objeto, revalidación después del lock y `ValidationError` con mensaje
  para el usuario. La vista captura el error y lo muestra en el form.
- Si hay emails, Calendar o Storage: post-commit (`app/turnos/integrations/post_commit.py`),
  nunca con locks abiertos.

### Forms
- `ModelForm` con `fields` explícito (el repo no usa `"__all__"`), `labels` y `widgets` en español.
- Dependencias de contexto por kwargs (`def __init__(self, *args, odontologo, **kwargs)`).

### Vistas, URLs y plantillas
- CBVs (`ListView` con `paginate_by = 10`, `FormView`, `UpdateView`), `messages.success(...)` y redirect.
- URLs: nombres `lista`, `crear`, `detalle`, `editar`, `archivar`; rutas `nuevo/`, `<int:pk>/editar/`.
  Usá `<uuid:...>` si el identificador puede circular fuera del panel (ver `indicaciones/urls.py`).
- Plantillas que extienden `base.html` y reutilizan `includes/form_field.html`,
  `includes/empty_state.html` e `includes/icon.html`. El form genérico de
  `turnos/tipos_turno/form.html` espera `titulo`, `subtitulo`, `texto_boton` y `url_cancelar`.
- UI y accesibilidad: `docs/design-system.md` (ver también la skill `componente-ui`).

## 3. Tests (junto con el código)

Seguí el estilo de `app/pacientes/tests.py` (helper `asignar_rol`, `force_login`, `reverse`). Cubrí:

- anónimo → redirect a login;
- rol sin permiso → 403;
- objeto fuera de alcance (otro odontólogo, asociación inactiva, archivado) → 404;
- camino feliz de cada operación y errores de validación;
- consultas acotadas (`CaptureQueriesContext`) si el listado puede tener N+1.

Si la garantía depende de PostgreSQL (locks, constraints, triggers), agregá un test PostgreSQL y
sumalo al job `clinical-postgresql` de CI.

## 4. Validación

Corré primero los tests focalizados y después lo que aplique de `.github/workflows/ci.yml`:
Black, Ruff, `manage.py check`, `makemigrations --check --dry-run`, tests con cobertura y Bandit.
Hacé la autorrevisión obligatoria de `AGENTS.md` antes de reportar.

## No hacer

- Borrado físico de datos con valor clínico o de auditoría.
- DNI, email, teléfono o contenido clínico en logs o mensajes de error de autorización.
- Reglas de permisos o de negocio en plantillas o JavaScript.
