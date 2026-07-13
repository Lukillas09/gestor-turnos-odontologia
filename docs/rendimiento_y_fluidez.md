# Rendimiento y fluidez visual

Este documento resume las mejoras livianas aplicadas para que el sistema se sienta más rápido sin cambiar la lógica principal.

## Fluidez visual

Se agregaron clases CSS reutilizables:

- `page-transition`: transición suave al entrar a cada página.
- `animate-fade-up`: aparición sutil con desplazamiento corto.
- `card-hover`: hover suave para cards y paneles importantes.
- `smooth-action`: transición para botones, enlaces y controles interactivos.

Las animaciones respetan `prefers-reduced-motion`, por lo que se desactivan si el usuario prefiere menos movimiento.

## CSS base

El CSS global ya no vive inline en `base.html`. El punto de entrada es `app/static/css/app.css`, que carga:

- `tokens.css`: variables estáticas y defaults.
- `base.css`: layout general, topbars, sidebar y tipografía base.
- `forms.css`: botones, formularios, tablas y paneles comunes.
- `components.css`: componentes compartidos, iconos, skeletons, mensajes y estados vacíos.
- `internal.css`: pacientes, turnos, agenda, historias y pantallas internas.
- `public.css`: landing pública, solicitud pública, autogestión y revisión visual.
- `calendar.css`: calendario y selección de horarios.
- `mobile-navigation.css`: navegación inferior y drawer móvil.
- `animations.css`: movimiento breve y controlado.
- `responsive.css`: breakpoints, tactilidad y `prefers-reduced-motion`.

`base.html` conserva solo las variables dinámicas del color principal configurado en el perfil del consultorio.

## Vistas alcanzadas

- Listado de pacientes.
- Perfil clínico del paciente.
- Listado de turnos.
- Agenda diaria.
- Agenda semanal.
- Historia clínica.
- Detalle de turno.
- Solicitud pública de turno.
- Landing, OTP, Mis turnos y confirmación pública.
- Dashboard, configuración, perfil y excepciones de agenda.

## Paginación

El listado de pacientes muestra 10 pacientes por página para que la pantalla cargue y se escanee más rápido.

## Consultas

El listado de pacientes carga datos mínimos y usa subconsultas para mostrar el próximo y el último turno sin traer todos los turnos del paciente.

La agenda diaria y semanal reutiliza los turnos cargados al construir bloques y columnas, evitando consultas duplicadas por odontólogo.

La lista de historia clínica usa conteo anotado de adjuntos en lugar de traer adjuntos completos cuando solo se necesita mostrar la cantidad.

## Recursos y JavaScript

- No se agregaron frameworks, fuentes remotas ni librerías de calendario.
- Las ilustraciones son SVG locales y tienen dimensiones explícitas.
- Los scripts usan `defer` y se inicializan solo si encuentran su componente.
- La reserva pública mantiene su caché de horarios y mejora progresiva; sin JavaScript conserva el formulario GET.
- Los skeletons animados se desactivan con `prefers-reduced-motion`.
- Playwright verifica que las vistas móviles no generen scroll horizontal fuera de las tiras deliberadamente desplazables.

## Próxima medición recomendada

Más adelante conviene medir consultas con Django Debug Toolbar solo en desarrollo. Por ahora no se instaló para mantener el proyecto simple y limpio.
