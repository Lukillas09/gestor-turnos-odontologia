---
name: componente-ui
description: Crear o modificar un componente de interfaz con el patrón real del repo, que es HTML renderizado en el servidor, includes de plantilla, el design system y JavaScript vanilla como mejora progresiva (con fetch a endpoints JsonResponse si hace falta). El repo no usa HTMX ni frameworks de frontend. Usar al agregar includes, widgets interactivos, actualizaciones parciales o comportamiento JS en plantillas.
---

# Componente UI

El repo renderiza en el servidor y usa JavaScript sólo para mejorar la experiencia. **No hay
HTMX, React ni bundler** (no existe `package.json`); no agregues dependencias de frontend sin que
el usuario lo pida. Las reglas de negocio, permisos y disponibilidad viven en el servidor
(`AGENTS.md`); el navegador nunca es la autoridad.

Leé `docs/design-system.md` antes de empezar.

## 1. Plantilla (siempre)

- Include reutilizable global en `app/templates/includes/`; específico de dominio en
  `app/<app>/templates/<app>/includes/`.
- Parametrizalo con `{% include "..." with param=valor %}`, como `includes/empty_state.html`
  (`title`, `description`, `icon_name`, `action_url`...) o `includes/form_field.html`.
- Reutilizá antes de crear: `form_field.html`, `empty_state.html`, `icon.html`, `messages.html`.
- Clases existentes: `.button` y variantes, `.ui-card`, `.status-badge`, `.internal-form-v2`,
  `.form-section`, `.sticky-form-actions`, `.ui-skeleton`.
- El componente debe funcionar (o degradar con dignidad) sin JavaScript.

## 2. Estilos

- Agregá reglas al CSS existente que corresponda (`components.css`, `forms.css`, `internal.css`,
  `public.css`, `responsive.css`) usando tokens de `tokens.css`; no hardcodees colores ni
  espaciados. El color de marca llega por `--primary*`.
- Respetá los breakpoints del design system (520 / 767 / 1023 / 1279 px), sin scroll horizontal
  de página, y `prefers-reduced-motion`.

## 3. JavaScript (sólo si hace falta)

- Archivo en `app/static/js/<nombre>.js` (o `app/<app>/static/<app>/`), con IIFE y `"use strict"`,
  enganchado por atributos `data-*`. Referencia mínima: `app/static/js/exception-form.js`.
- Cargalo desde la plantilla con
  `{% block extra_scripts %}<script src="{% static 'js/<nombre>.js' %}" defer></script>{% endblock %}`.
  Hay algunos scripts inline heredados; no sumes nuevos.
- Nada de lógica de negocio: el JS muestra, oculta, consulta y refresca.

## 4. Datos dinámicos (fetch + JsonResponse)

Patrón del repo, con dos referencias:

- **Lectura (GET)**: `HorariosDisponiblesJsonView` en `app/turnos/views/turnos.py`. Mixin de rol,
  parámetros validados en el servidor y respuesta `{"horarios": [...], "mensaje": "..."}`.
- **Mutación (POST)**: `EstadoDentalCreateView` en `app/odontogramas/views.py` con
  `app/odontogramas/static/odontogramas/odontograma.js`:
  - header `X-CSRFToken` (nunca `csrf_exempt`);
  - permiso de objeto verificado en la vista → `403 {"ok": false, "error": ...}`;
  - cuerpo validado con un Django `Form` → `400 {"ok": false, "errors": form.errors}`;
  - mutación delegada a un service; respuesta `{"ok": true, ...}`;
  - fragmentos HTML renderizados en el servidor con `render_to_string` (ej. `historial_html`),
    no armados en JS con datos sin escapar.
- En el JS, manejá `!response.ok`, `data.ok === false` y errores de red (`catch`) con mensajes en
  español para el usuario.
- Alcance por objeto igual que en las vistas HTML (queryset limitado + 404). No devuelvas datos
  personales ni internos que la pantalla no necesite.

## 5. Accesibilidad (WCAG 2.2 AA)

Labels visibles, nombre accesible en botones de sólo icono, `aria-live` para resultados
asíncronos, `aria-busy` durante cargas, foco gestionado en modales/drawers (Escape y ciclo de
foco), objetivos táctiles de 44 px y estados que no dependan sólo del color.

## 6. Tests y validación

- Endpoint JSON: tests Django de anónimo, rol sin permiso (403), fuera de alcance (404),
  payload inválido (400) y forma de la respuesta exitosa.
- Flujo importante: smoke E2E con Playwright en `app/<app>/tests_e2e/` (ver
  `app/turnos/tests_e2e/test_internal_smoke.py`).
- `python scripts/check_text_encoding.py` (textos en español con acentos), Black, Ruff y
  `collectstatic` según `.github/workflows/ci.yml`.
