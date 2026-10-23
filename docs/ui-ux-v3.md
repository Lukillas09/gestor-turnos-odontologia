# Interfaz UI/UX V3: experiencia pública

## Alcance

El rediseño V3 cubre solo la experiencia pública del paciente: landing, elegí tu turno, tus datos,
solicitud enviada, acceso con código, mis turnos y reprogramar. Continúa el plan de
[`redesign_publico_pacientes_plan.md`](redesign_publico_pacientes_plan.md) y reemplaza la parte
pública de [`ui-ux-v2.md`](ui-ux-v2.md). El panel interno no cambia.

La especificación y las maquetas de referencia están en
[`rediseno-publico-v3/`](rediseno-publico-v3/SPEC.md).

Es una capa de presentación. El servidor sigue decidiendo disponibilidad, duración, elegibilidad,
márgenes, idempotencia, CSRF, Turnstile y OTP. No cambian reglas de turnos, servicios, permisos,
rate limiting ni modelos, y no hay migraciones.

## Sistema visual

- Tokens `--public-*` en `app/static/css/tokens.css`, que es la única fuente (el shell público ya
  no los redefine). Nuevos: `--public-subtle`, `--public-border-strong`, `--public-gold-ink`,
  `--public-gold-ink-hover`, `--public-green-ink`, `--public-warning-bg`/`-text`,
  `--public-danger-bg` y `--public-font-serif`. `--public-muted` pasa a `#5E5750`.
- El botón primario usa `--public-gold-ink` (`#8A6224`, texto blanco ≈ 5.4:1). El oro claro
  `--public-gold` queda para bordes, números y foco.
- Fraunces (OFL) se sirve desde `static/fonts/fraunces/` y solo se carga con
  `usar_shell_publico`. No hay dependencias de servicios externos de fuentes.
- Foco visible: `outline: 3px solid var(--public-gold)` con offset. Controles de al menos 44 px.
- Estilos nuevos al final de `public.css` y `responsive.css`, en secciones "Rediseño público V3",
  con prefijos propios por pantalla (`public-home-*`, `public-pick-*`, `public-patient-*`,
  `public-received-*`, `public-access`, `public-mine-*`).

## Componentes compartidos

- **Header** (`includes/public_topbar.html`): marca en Fraunces, "Odontología · Turnos online",
  navegación Inicio · Servicios · Equipo · Mis turnos (Servicios y Equipo solo en la landing y
  si la sección existe) y CTA "Reservar turno". Variante mínima en Tus datos. En móvil, el menú
  `details` existente. El acceso del personal pasó al footer.
- **Stepper** (`turnos/includes/public_stepper.html`): tres barras con etiqueta; actual dorado
  con `aria-current="step"`, completado verde con check. Desde Tus datos, el paso 1 vuelve a la
  selección conservando profesional, fecha y motivo (`url_seleccion`).
- **Footer** (`includes/public_footer.html`): solo en el shell público. `brand_footer.html`
  sigue siendo el del panel interno.
- **Chip de estado** (`turnos/includes/public_status_chip.html`): pendiente, confirmado y
  cancelado, siempre con ícono y texto.
- **Campos**: `includes/form_field.html` agrega `id` al error (para `aria-describedby`) y un
  parámetro opcional `optional_note`.

## Pantallas

- **Landing**: hero, beneficios, motivos de consulta (solo con agenda inteligente; enlazan a la
  selección con `?tipo_turno=`), cómo funciona, equipo, gestión de solicitudes, información del
  consultorio abierta (cada dato respeta su `mostrar_*`, "Cómo llegar" a Google Maps sin iframe),
  CTA final y CTA fija en móvil.
- **Elegí tu turno**: tarjetas numeradas y resumen sticky; en móvil, barra fija con resumen
  compacto. Profesionales y motivos como `radiogroup`. Calendario sobre el `input[type=date]`
  canónico; en móvil la tira de días es la vista principal y el calendario queda detrás de
  "Ver calendario" para no perder fechas fuera de los 14 días de la tira. Horarios en
  "Recomendados", "Mañana" y "Tarde" con los grupos del servidor. `?tipo_turno=` sin
  profesional preselecciona el motivo cuando el profesional elegido lo ofrece.
- **Tus datos**: campos desde el form Django, errores con borde, fondo, ícono y `role="alert"`,
  resumen "Tu turno" con "Cambiar".
- **Solicitud enviada**: el envío sigue redirigiendo a la landing con un banner neutral. El mismo
  bloque se usa en la vista OK, que solo agrega el detalle del turno cuando ya estaba en la
  sesión.
- **Acceso con código**: tarjeta centrada, código en un único campo con
  `autocomplete="one-time-code"` y aviso de seguridad.
- **Mis turnos**: "Tu próximo turno" es la primera cita futura; el resto va en "Otras
  solicitudes" con "Gestionar". Reprogramar y cancelar siguen usando los mismos enlaces y POST
  con token.

## Decisiones sobre textos

Ningún texto promete algo que el backend no hace:

- Se habla de **solicitud** que el consultorio revisa; no de confirmación inmediata.
- No se prometen recordatorios: `enviar_recordatorios_email` depende de una programación
  externa que el repositorio no garantiza.
- La nota de Tus datos dice que el horario se revalida al enviar y que, si ya no está libre, se
  avisa para elegir otro. No se ofrecen alternativas automáticas.
- La respuesta tras enviar es neutral (igual para todos los resultados), como exige
  `docs/flujo-turnos.md`.
- El reenvío de código muestra un aviso fijo con `TURNOS_PUBLIC_RESEND_SECONDS`, el mismo setting
  con el que el servidor aplica el cooldown. Se redacta como condición mínima ("tiene que pasar al
  menos") porque además rige `TURNOS_PUBLIC_RESEND_LIMIT` por ventana. No hay cuenta regresiva
  porque el cooldown real solo se registra para DNIs con email y la revelaría.
- No se muestra email enmascarado ni "Hola, {nombre}": el contexto no los provee.

## Accesibilidad

Criterios del SPEC verificados con axe-core 4.10 (WCAG 2.0/2.1/2.2 A y AA más best practices)
en todas las pantallas públicas a 1440 x 900 y 390 x 844, incluidos estados con errores y
detalles abiertos: sin violaciones de contraste, nombres, roles ni tamaño de objetivos. La única
observación restante proviene de `includes/messages.html` (compartido con el panel interno), que
usa `role="status"` en un `li`.

## Validación y capturas

- E2E (`turnos/tests_e2e/test_public_booking_smoke.py`) en 1440 x 900 y 390 x 844, con
  comprobación de scroll horizontal, incluido el flujo de acceso, mis turnos y reprogramar.
- Las capturas se generan con `CAPTURE_UI_SCREENSHOTS=1`. Usar `UI_SCREENSHOT_DIR` y correr solo
  el smoke público: el smoke interno escribe siempre en `docs/screenshots/`.
- Capturas públicas en `docs/screenshots/`: `public-home-desktop.png`, `public-home-mobile.png`,
  `public-booking-desktop.png`, `public-booking-mobile.png`, `public-data-desktop.png`,
  `public-access-mobile.png` y `public-my-appointments-desktop.png`.
