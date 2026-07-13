# Interfaz UI/UX V2

## Alcance

El rediseño cubre la experiencia pública de pacientes y el panel interno de recepción, odontólogos y administración. Se realizó sobre Django Templates, CSS modular y JavaScript nativo, sin modificar reglas de turnos, permisos, OTP, Turnstile, rate limiting, Google Calendar o storage.

## Experiencia pública

- Landing con marca en el primer viewport, CTA principal, garantías, explicación del proceso y profesionales reales.
- Stepper semántico en Turno, Datos y Confirmación.
- Selector visual de odontólogo sincronizado con el `select` de fallback.
- Fecha y horarios progresivos con skeleton, error recuperable y CTA sticky en móvil.
- Formulario final con resumen del turno y sin repetir la tarjeta completa del consultorio.
- Confirmación visual, OTP simplificado y Mis turnos priorizando la próxima cita.

## Panel interno

- Sidebar navy en escritorio y navegación inferior en móvil.
- Dashboard con métricas, atajos por permiso, agenda del día y elementos por revisar.
- Agenda diaria por bloques horarios y agenda semanal por columnas; en móvil no se comprimen siete días.
- Turnos y pacientes con jerarquía operativa, contacto directo, estados y acciones secundarias agrupadas.
- Perfil clínico con navegación por secciones e historia clínica en timeline.
- Formularios por secciones, tamaños de campo coherentes y acciones persistentes.
- Configuración del consultorio con editor y preview en vivo.

## Accesibilidad y resiliencia

- La navegación y las acciones críticas siguen siendo enlaces o formularios Django reales.
- Los enriquecimientos se inicializan solo cuando existe el componente.
- El drawer móvil mantiene el foco, cierra con Escape y expone su estado.
- Errores, estados y mensajes no dependen exclusivamente del color.
- Las animaciones se reducen según preferencias del sistema.

## Capturas de referencia

Las imágenes se generan con `CAPTURE_UI_SCREENSHOTS=1` y los tests E2E. Todos los registros son ficticios.

- `public-home-desktop.png` y `public-home-mobile.png`
- `public-booking-desktop.png` y `public-booking-mobile.png`
- `internal-dashboard-desktop.png` y `internal-dashboard-mobile.png`
- `agenda-desktop.png`
- `patient-profile-desktop.png`

## Validación visual

Playwright recorre la solicitud pública en 1440 x 900 y 390 x 844, valida la creación pendiente y comprueba overflow móvil. El recorrido interno cubre login, dashboard, agenda, turnos, detalle, pacientes, ficha clínica y menú Más. La generación de capturas es opcional para evitar escritura de artefactos en cada ejecución de CI.
