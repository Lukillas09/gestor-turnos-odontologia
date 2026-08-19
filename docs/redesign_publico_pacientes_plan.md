# Rediseño público para pacientes

## Objetivo

Reconstruir la landing y los dos primeros pasos de la solicitud pública con la
composición premium de las referencias entregadas, conservando como fuente de
verdad el comportamiento actual de Django, la agenda inteligente, las
validaciones del servidor y las protecciones públicas.

## Lenguaje visual

- Fondo marfil cálido y superficies blancas, sin predominio del azul del panel
  interno.
- Acento dorado sobrio para acciones, selección y progreso.
- Verde apagado para estados completados y mensajes de privacidad.
- Titulares con serif editorial; cuerpo e interfaz con el stack sans existente.
- Bordes finos, radios de 18 a 24 px y sombras amplias de baja opacidad.
- Movimiento limitado a transiciones de 120 a 250 ms y entrada suave; se
  respeta `prefers-reduced-motion`.

### Tokens públicos

Se definirán dentro de `.public-shell-body` para aislarlos del panel interno:

- `--public-bg`: marfil de fondo.
- `--public-surface`: superficie principal.
- `--public-surface-soft`: superficie cálida secundaria.
- `--public-text`: carbón cálido.
- `--public-muted`: gris cálido.
- `--public-border`: borde beige tenue.
- `--public-gold`, `--public-gold-dark`, `--public-gold-soft`: acción y selección.
- `--public-green`, `--public-green-soft`: progreso completado y privacidad.
- `--public-danger`: errores.
- `--public-shadow`: elevación difusa de baja intensidad.

## Componentes compartidos

### Header público

- Logo configurado o iniciales, nombre real y señal secundaria "Turnos online".
- Navegación real: Inicio, Mis turnos y Reprogramar / Cancelar.
- CTA dorado hacia `turnos:solicitud_publica`.
- Acceso interno relegado al menú compacto y al enlace secundario de la landing.
- En el paso de datos se usa una variante minimalista con el mensaje de
  protección y sin navegación distractora.
- En móvil se usa un menú nativo `details` con botones reales, sin scroll
  horizontal.

### Stepper

- `nav` y `ol` conservados, con `aria-current="step"`.
- Tres pasos con título y descripción: Turno, Datos y Confirmación.
- Paso actual dorado, completado verde con check y futuro neutro.
- En móvil permanece horizontal y compacto; no se oculta el progreso.

### Beneficios y confianza

- Fila de cuatro beneficios reutilizable en landing y selección.
- Señales veraces: atención rápida, recordatorios, cambios simples y protección
  de datos.
- No se promete confirmación inmediata porque el flujo crea una solicitud para
  revisión.

## Pantalla 1: landing

### Estructura

1. Header fino y centrado.
2. Hero en grid de dos columnas.
3. Badge "TURNOS ONLINE 24/7".
4. H1 serif grande; el título configurado sigue presente y se añade la línea de
   acento "y sin complicaciones".
5. Texto de bienvenida configurado.
6. CTA de reserva y CTA de consulta.
7. Tres señales de confianza.
8. Fotografía clínica real con marco cálido y card flotante.
9. Fila horizontal de beneficios.
10. Sección "¿Cómo funciona?" con tres pasos y conectores.
11. Equipo, gestión de solicitudes e información del consultorio actuales,
    adaptados al nuevo sistema sin eliminarlos.

### Grid y responsive

- Desktop: proporción aproximada 52/48, ancho máximo de 1480 px.
- Tablet: imagen y texto mantienen dos columnas hasta que la lectura deja de ser
  cómoda; luego pasan a una columna.
- Móvil: texto, CTA, imagen y beneficios en ese orden; botones de ancho completo.

### Asset

Se incorpora una fotografía clínica propia, sin texto ni marcas. No se utiliza
ninguna captura como fondo ni como fuente de controles.

## Pantalla 2: selección de turno

### Correspondencia con la referencia

- Stepper ancho y cabecera centrada.
- Layout desktop de contenido principal más resumen lateral sticky.
- Card superior con profesional, motivo y calendario en tres columnas cuando la
  agenda inteligente está activa.
- Con la feature flag desactivada, la misma card usa dos columnas coherentes.
- Card inferior para días cercanos, horarios y mensajes de disponibilidad.
- Card de ayuda por WhatsApp únicamente cuando la configuración pública lo
  permite.

### Calendario visual

- Se conserva el `input` Django como valor canónico, con sus atributos `min` y
  `max`.
- Un calendario progresivamente mejorado muestra mes, navegación, encabezados y
  días mediante botones accesibles.
- Los días fuera del rango se deshabilitan; no se inventa disponibilidad diaria.
- Elegir un día actualiza el input y dispara el endpoint existente.
- Navegación por teclado: flechas, Home/End y Enter/Espacio sobre botones.

### Horarios

- La clasificación sigue siendo solo presentacional.
- Mañana: antes de las 13:00; tarde: desde las 13:00. Las alternativas continúan
  dentro de "Ver más horarios".
- Los horarios recomendados aparecen primero sin mostrar score ni razones
  internas.
- Seleccionar un horario actualiza el resumen y habilita el CTA real hacia la URL
  construida por el servidor.

### Resumen

- Datos dinámicos de profesional, especialidad, motivo, fecha, duración y hora.
- CTA `Continuar` realmente deshabilitado hasta elegir horario.
- El enlace final siempre conserva odontólogo, tipo, fecha, hora y clasificación.

### Responsive

- Desktop: contenido 75% y resumen 25%.
- Tablet: controles en dos columnas, resumen no sticky debajo.
- Móvil: profesional, motivo, calendario, horarios, resumen y CTA; strip de días
  con scroll horizontal contenido.

## Pantalla 3: datos del paciente

### Estructura

- Header minimalista y stepper con el paso 1 completado.
- Cabecera centrada "Completá tus datos".
- Grid 70/30: formulario y resumen.
- Formulario con Nombre/Apellido, Teléfono/DNI, Email y Comentario adicional.
- Ayudas y requerimiento condicional de email exactamente como los define el
  formulario Django.
- Nota de privacidad veraz y CTA dorado alineado a la derecha.
- Resumen con foto, especialidad, motivo, fecha, horario, duración y profesional.
- Decoración botánica CSS/SVG propia, no interactiva y `aria-hidden`.

### Estados

- Errores de campo y generales conservan `role="alert"`, `aria-invalid` y ayudas.
- El submit usa el estado de carga global existente y añade bloqueo explícito de
  doble envío en esta pantalla.
- En móvil el formulario pasa a una columna, el resumen deja de ser sticky y el
  CTA ocupa todo el ancho.

## Contratos funcionales preservados

- `data-public-availability`
- `data-availability-url`
- `data-smart-scheduling`
- `data-types-url`
- `data-public-search-form`
- `data-professional-picker`
- `data-public-professional`
- `data-public-service-picker`
- `data-public-service`
- `data-public-results`
- `data-public-date`
- `data-public-slot`
- `data-public-slot-action`
- `data-public-slot-label`
- `data-public-slot-continue`

El servidor continúa decidiendo disponibilidad, duración, elegibilidad,
revalidación, márgenes, idempotencia, CSRF y Turnstile. JavaScript solo controla
presentación, sincronización de controles y feedback.

## Cambios previstos

### HTML

- Reorganizar los tres templates del alcance.
- Ampliar `public_topbar.html` y `public_stepper.html`.
- Crear partials acotados para beneficios y resumen de selección.
- Mantener los includes y rutas existentes fuera de estas pantallas.

### CSS

- Añadir una sección V3 aislada al final de `public.css`.
- Añadir breakpoints públicos específicos al final de `responsive.css`.
- No modificar el sistema visual interno.

### JavaScript

- Mantener carga AJAX, abortado, debounce y URLs devueltas por backend.
- Añadir calendario visual, actualización del resumen, CTA deshabilitado y
  bloqueo de doble submit.
- Mantener el flujo legacy cuando la feature flag está desactivada.

## Estados a validar

- Carga, vacío, error de red, reintento, seleccionado, deshabilitado, hover,
  focus-visible, error de formulario y éxito.
- Sin profesionales, sin servicios, sin horarios, smart scheduling activo y
  feature flag desactivada.

## Riesgos y mitigaciones

- **Desincronización del calendario:** el input real nunca se elimina y cada
  selección dispara su evento `change`.
- **Pérdida de parámetros:** el CTA usa exclusivamente la URL de horario enviada
  por Django.
- **Datos stale en el resumen:** se reinicia la selección al cambiar profesional,
  servicio o fecha.
- **Layout demasiado denso:** breakpoints reales para tablet y móvil, no simple
  reducción tipográfica.
- **Regresiones internas:** tokens y selectores bajo `.public-shell-body` y clases
  `public-*`.
- **Afirmaciones incorrectas:** todos los textos reflejan una solicitud pendiente
  de confirmación.

## Validación

- Pruebas Django de templates y flujo.
- E2E de landing, selección, calendario, resumen, formulario, doble submit,
  feature flag desactivada y viewports 390x844, 768x1024 y 1440x900.
- Tres ciclos de captura y comparación sobre landing, selección y datos.
- Suite completa, formato, codificación, migraciones y `git diff --check`.
