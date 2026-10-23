# Rediseño público V3 — Especificación para implementar

Rediseño visual de la **experiencia pública del paciente** (landing, reserva, datos, confirmación, acceso OTP y Mis turnos). Es una capa de presentación: **no cambia reglas de negocio, modelos, servicios, permisos ni protecciones públicas.**

Las referencias visuales están en `referencias/*.dc.html`. Cada archivo es HTML autocontenido con estilos inline. Se usa como **maqueta visual**, no como código a copiar. Las imágenes que en esas referencias apuntan a `/_blob/...` son en el repo:

- logo → `{% static 'images/consultorio-abete-logo.png' %}` (o `configuracion_consultorio.logo_url` si existe)
- foto → `{% static 'images/dental-clinic-hero.webp' %}`

Las etiquetas `<sc-for>`, `<sc-if>`, `{{...}}` y el `<script type="text/x-dc">` son del editor de diseño. Hay que reemplazarlos por templates Django y por el JS existente.

---

## 0. Antes de tocar nada

1. Leer `AGENTS.md`, `CLAUDE.md`, `app/turnos/AGENTS.md`, `docs/design-system.md`, `docs/ui-ux-v2.md` y `docs/redesign_publico_pacientes_plan.md`. Este rediseño **continúa** ese plan.
2. Restricciones que no se negocian:
   - El servidor sigue decidiendo disponibilidad, duración, elegibilidad, márgenes, idempotencia, CSRF, Turnstile y OTP. JS solo presenta.
   - Conservar **todos** los contratos `data-public-*` listados en `docs/redesign_publico_pacientes_plan.md` (sección "Contratos funcionales preservados").
   - No reintroducir Redis, `django.core.cache` ni estado de frontend como autoridad.
   - No exponer horarios ocupados, scores ni razones del algoritmo.
   - Los textos nunca prometen confirmación inmediata: es una **solicitud** que el consultorio confirma.
   - No tocar el panel interno. Todo va bajo `.public-shell-body` y clases `public-*`.
3. Trabajar en una rama nueva, por ejemplo `feat/rediseno-publico-v3`.

---

## 1. Tokens (`app/static/css/tokens.css`)

Se mantienen los `--public-*` existentes. Hay que agregar o ajustar estos:

| Token | Valor | Uso |
|---|---|---|
| `--public-bg` | `#FBFAF7` | fondo (ya existe) |
| `--public-surface` | `#FFFEFA` | tarjetas (ya existe) |
| `--public-surface-soft` | `#F7F1E7` | secciones alternas (ya existe) |
| `--public-text` | `#2D2823` | texto (ya existe) |
| `--public-muted` | `#5E5750` | **cambiar** desde `#746d65` para tener mejor contraste en texto secundario |
| `--public-subtle` | `#6B645C` | **nuevo**: etiquetas chicas y ayudas |
| `--public-border` | `#E6DED2` | bordes (ya existe) |
| `--public-border-strong` | `#D9C9AE` | **nuevo**: botón secundario y borde hover |
| `--public-gold` | `#BE9142` | solo bordes, números, anillo de foco (ya existe) |
| `--public-gold-ink` | `#8A6224` | **nuevo**: fondo de botón primario y selección (texto blanco ≈ 5.4:1) |
| `--public-gold-ink-hover` | `#74511C` | **nuevo** |
| `--public-gold-soft` | `#F6EDDD` | fondos de íconos y badges (ya existe) |
| `--public-green` | `#3F7D60` | completado y confirmado (ya existe) |
| `--public-green-ink` | `#2F5E48` | **nuevo**: texto verde sobre fondo claro |
| `--public-green-soft` | `#EAF3ED` | (ya existe) |
| `--public-warning-bg` / `--public-warning-text` | `#FDF3DC` / `#7A5410` | **nuevo**: chip "Pendiente" |
| `--public-danger` | `#A33F3F` | (ya existe) |
| `--public-danger-bg` | `#FFF8F7` | **nuevo**: fondo de campo con error |

**Importante:** hoy el botón dorado usa `#BE9142` con texto blanco y queda por debajo de 4.5:1. Hay que pasar `.public-gold-button` a `--public-gold-ink`.

Tipografía:
- **Fraunces** (Google Fonts, pesos 400/500, itálica 400) solo para titulares y números grandes, como la fecha del turno.
- Interfaz y texto en **Inter**, que ya está en `--font-family`.
- Cargar Fraunces solo en el shell público (`base.html` cuando `public-shell-body`). Usar `display=swap` y `preconnect`.

Otros:
- Radios: 14px en campos y horarios, 20–24px en tarjetas, 999px en botones.
- Sombra: `--public-shadow: 0 18px 48px rgba(71,55,34,.08)`.
- Foco: `outline: 3px solid var(--public-gold); outline-offset: 2–3px`.
- Altura mínima de controles: 44px.

---

## 2. Componentes compartidos

### Header público (`templates/includes/public_topbar.html`)
- Logo (o el configurado) + `nombre_visible` en Fraunces + subtítulo "Odontología · Turnos online".
- Navegación: Inicio · Servicios (`#servicios`) · Equipo (`#equipo`) · Mis turnos.
- CTA píldora "Reservar turno" → `turnos:solicitud_publica`.
- **Quitar "Ingresar"** del header. El acceso interno pasa al footer.
- Variante minimalista en el paso Datos: logo + "Conexión segura · Tus datos están protegidos", sin navegación.
- En móvil, botón hamburguesa de 44×44 con el `details` nativo existente y CTA fija abajo en la landing.

### Stepper (`turnos/includes/public_stepper.html`)
- Tres segmentos de barra de 4px, cada uno con su etiqueta debajo ("1 · Turno", "2 · Tus datos", "3 · Confirmación").
- Actual: dorado y `aria-current="step"`. Completado: verde con check, y el paso 1 completado vuelve a la selección. Futuro: neutro.
- En móvil: barra de progreso fina más "Paso N de 3" en el header.

### Footer (`includes/brand_footer.html`)
- Logo chico, "© {año} {nombre} · {localidad}", y los enlaces Reservar turno · Mis turnos · **Acceso para personal del consultorio**.

### Chips de estado
- **Pendiente**: fondo warning, ícono reloj.
- **Confirmado**: fondo verde, texto blanco, check.
- **Cancelado**: fondo rojo suave, cruz.
- Siempre llevan ícono y texto, nunca solo color.

---

## 3. Pantallas

### 3.1 Landing — `turnos/templates/turnos/public/landing.html` · ref `Main.dc.html`, `Movil-Inicio.dc.html`

Secciones en este orden:

1. **Hero** en dos columnas (en móvil, una):
   - Badge "Turnos online 24/7".
   - H1 serif con `titulo_portada` y una línea en itálica dorada "sin complicaciones".
   - Párrafo con `texto_bienvenida`.
   - CTA primario y secundario "Ver mis turnos".
   - Tres checks: "Sin crear una cuenta", "Seguimiento por email" y "Datos protegidos".
   - A la derecha, la foto con radio 28 sobre un rectángulo `gold-soft` desplazado, y una tarjeta flotante "Te avisamos por email".
2. **Beneficios**: reutilizar `public_benefits.html`, en una tarjeta de 4 columnas.
3. **Motivos de consulta** (`#servicios`, fondo `surface-soft`):
   - Una tarjeta por cada `TipoTurno` activo y visible al público, con su ícono, nombre, descripción y el enlace "Reservar {nombre}" a `solicitud_publica` con `?tipo_turno=`.
   - Si no hay tipos (smart scheduling apagado), la sección no se muestra.
   - **Nuevo en el contexto de la vista.** Buscar el selector existente que ya filtra los tipos para el formulario público y reutilizarlo; no escribir otra query.
4. **¿Cómo funciona?**: tres pasos con círculos numerados en serif. El tercero va verde y con el texto "Enviá y esperá la confirmación".
5. **Nuestro equipo** (`#equipo`): igual que hoy (`odontologos_publicos`).
   - Tarjeta con foto o iniciales sobre un fondo de 200px de alto, nombre, especialidad, matrícula y "Ver disponibilidad".
   - En móvil, carrusel horizontal.
6. **Gestioná tus solicitudes**: una tarjeta con texto a la izquierda y dos enlaces a la derecha ("Consultar mis turnos" y "Reprogramar o cancelar").
7. **Información del consultorio**: **abierta**, ya no en `<details>`.
   - Grilla de contacto, cada dato respetando su flag `mostrar_*`.
   - Horarios y política de cancelación.
   - Espacio para mapa. Por ahora un placeholder o un enlace "Cómo llegar" a Google Maps con la dirección. No agregar un iframe sin consultarlo antes.
   - En móvil: botones "Llamar" y "WhatsApp".
8. **CTA final**: banda oscura `--public-text` con el botón en dorado claro `#D9B26A` y texto oscuro.
9. Footer.

Hay que conservar el banner `solicitud_turno_confirmada` actual.

### 3.2 Elegí tu turno — `turnos/templates/turnos/solicitud_publica_seleccion.html` · ref `Reserva.dc.html`, `Movil-Reserva.dc.html`

- Layout: columna principal (`flex: 999 1 600px`) y aside de resumen sticky (`flex: 1 1 320px`). En móvil, el resumen pasa a una barra fija abajo.
- Tarjetas numeradas:
  1. **Profesional**: tarjetas radio (`role="radio"`, `aria-checked`) con iniciales o foto. La seleccionada lleva borde `gold-ink`, fondo `#FBF5EA` y check.
  2. **Motivo**: chips píldora. Solo si `data-smart-scheduling` está activo; si no, la tarjeta no aparece.
  3. **Día y horario**:
     - A la izquierda, un **calendario mensual** con progressive enhancement sobre el `input[type=date]` existente, que sigue siendo el valor canónico con sus `min` y `max`. Los días fuera de la ventana van deshabilitados. Hay navegación por mes y teclado (flechas, Home/End, Enter/Espacio). Al elegir un día se dispara `change` en el input.
     - A la derecha, los horarios agrupados en "Recomendados" (los primeros que devuelve smart scheduling, sin score), "Mañana" (antes de las 13:00) y "Tarde". La grilla es de 3 columnas.
     - En móvil, en lugar del calendario va una tira horizontal de días (tarjetas de 60×72).
- **Resumen**: profesional, motivo, fecha, horario ("Sin elegir" mientras no haya) y duración.
  - "Continuar" queda realmente deshabilitado hasta elegir un horario y usa **solo** la URL que devuelve el servidor (`data-public-slot-continue`).
  - Nota debajo: "Es una solicitud: el consultorio la revisa y te confirma por email".
- Tarjeta de ayuda por WhatsApp, solo si `mostrar_whatsapp` y hay `whatsapp_url`.
- Estados a cubrir: cargando (skeleton), sin horarios, error de red con reintento, y reset de la selección al cambiar profesional, motivo o día.
- JS: extender `app/static/js/public-booking.js`. No reescribirlo.

### 3.3 Tus datos — `turnos/templates/turnos/solicitud_publica_form.html` · ref `Datos.dc.html`

- Header minimalista con el stepper en el paso 2.
- Formulario en una tarjeta:
  - Nombre y Apellido, Teléfono y DNI en dos columnas, que pasan a una en móvil.
  - Email.
  - Comentario, marcado "(opcional)".
  - Labels, ayudas y validaciones salen **del form Django** (`SolicitudTurnoPublicaForm`) a través de `includes/form_field.html`.
- Error de campo: borde `danger`, fondo `danger-bg`, ícono más mensaje con `role="alert"` y `aria-invalid`.
- Turnstile en su contenedor actual.
- Pie del formulario: nota de privacidad a la izquierda; a la derecha "Volver" y "Enviar solicitud" (primario, con ícono de enviar y bloqueo de doble envío).
- Aside con el resumen del turno y el enlace "Cambiar", más la nota: "Al enviar verificamos que el horario siga libre. Si alguien lo tomó, te mostramos otras opciones."
  - Antes de dejar ese texto, **confirmar que el flujo realmente hace eso.** Si no, ajustarlo a lo que hace.

### 3.4 Solicitud enviada — `turnos/templates/turnos/solicitud_publica_ok.html` · ref `Confirmacion.dc.html`

- Stepper completo en verde y círculo verde con check.
- "¡Recibimos tu solicitud!" con la aclaración: "Todavía no es un turno confirmado…". El email se muestra solo si el contexto ya lo tiene y es seguro mostrarlo.
- Tarjeta del turno: bloque de fecha (mes y día en serif), día y hora, motivo, profesional, duración y chip **Pendiente de confirmación**.
- "Qué sigue", en tres pasos: Solicitud registrada → El consultorio la revisa → Recordatorio.
- Acciones: "Ver mis turnos" (primario) y "Solicitar otro turno".
- Ayuda: "¿No te llegó el email? Revisá spam o escribinos por WhatsApp".

### 3.5 Acceso con código — `turnos/templates/turnos/public_access/solicitar_acceso.html` y `verificar.html` · ref `Movil-Acceso.dc.html`

- Ícono de sobre, "Revisá tu email" y el email **enmascarado** si el backend ya lo provee.
  - No revelar si el email existe: las respuestas tienen que seguir siendo neutrales.
- Código: mantener el campo del form Django. Opcionalmente, mejora progresiva a 6 cajas sincronizadas con ese input (pegar el código entero tiene que funcionar, con `autocomplete="one-time-code"`).
- "Reenviar en m:ss" solo si existe un cooldown real del lado del servidor. Si no existe, dejar el enlace normal.
- Aviso de seguridad: "Nunca te vamos a pedir este código por teléfono ni WhatsApp".

### 3.6 Mis turnos — `turnos/templates/turnos/public_access/mis_turnos.html` (y `reprogramar.html`) · ref `MisTurnos.dc.html`

- Encabezado "Acceso verificado" + H1 ("Hola, {nombre}" solo si ya está en el contexto; si no, "Mis turnos") y el botón "Cerrar acceso", que es el POST existente.
- **Próximo turno destacado**: la primera cita futura activa.
  - Franja superior verde con "Tu próximo turno" y el chip de estado.
  - Fecha grande en serif, hora, motivo, duración y profesional.
  - Botones "Reprogramar" (secundario) y "Cancelar turno" (borde rojo). Siguen siendo los formularios y POST actuales.
- "Otras solicitudes": lista compacta con chip de estado y "Gestionar".
- Estado vacío: el actual, con el nuevo estilo.
- Banda final "¿Necesitás otro turno?" con CTA.

---

## 4. Archivos que probablemente se tocan

- `app/static/css/tokens.css`: tokens.
- `app/static/css/public.css`: agregar una sección **V3** al final. No borrar reglas que use el panel interno.
- `app/static/css/responsive.css`: breakpoints públicos.
- `app/static/js/public-booking.js`: calendario, resumen y estados.
- `app/templates/base.html`: carga de Fraunces solo en el shell público.
- `app/templates/includes/public_topbar.html`, `brand_footer.html`.
- Los templates de `app/turnos/templates/turnos/` que figuran arriba e `includes/public_*`.
- La vista de la landing, para el contexto de tipos de turno, reutilizando el selector existente.

## 5. Accesibilidad (criterios de aceptación)

- Contraste de texto ≥ 4.5:1. Revisar en especial el dorado y los grises.
- Todo lo clickeable es `<a>` o `<button>`, de al menos 44×44px y con foco visible.
- Los grupos de selección usan `role="radiogroup"`/`radio` o `aria-pressed`, siempre con `aria-label` en los días.
- Ningún estado se comunica solo con color.
- Respetar `prefers-reduced-motion`.
- Sin scroll horizontal de página en 390px. Las tiras de días o profesionales hacen scroll dentro de su caja.

## 6. Validación

- Correr la suite completa y el lint como en `.github/workflows/ci.yml`.
- Actualizar los tests de templates que dependan de textos o clases que cambien (por ejemplo, el enlace "Ingresar" del header).
- E2E: `turnos/tests_e2e/test_public_booking_smoke.py` en 1440×900 y 390×844.
- Regenerar las capturas con `CAPTURE_UI_SCREENSHOTS=1` y actualizar `docs/screenshots/`.
- Actualizar `docs/ui-ux-v2.md`, o crear `docs/ui-ux-v3.md`, con los cambios.

## 7. Orden sugerido (un commit por paso)

1. Tokens, fuentes, header, stepper y footer.
2. Landing.
3. Elegí tu turno (template + JS).
4. Tus datos.
5. Solicitud enviada.
6. Acceso OTP y Mis turnos.
7. Responsive móvil, accesibilidad, tests, capturas y docs.
