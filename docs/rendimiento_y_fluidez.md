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
- `internal.css`: pacientes, turnos, agenda, historias y pantallas internas.
- `public.css`: landing pública, solicitud pública, autogestión y revisión visual.
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

## Paginación

El listado de pacientes muestra 10 pacientes por página para que la pantalla cargue y se escanee más rápido.

## Consultas

El listado de pacientes carga datos mínimos y usa subconsultas para mostrar el último turno sin traer todos los turnos del paciente.

La agenda diaria y semanal reutiliza los turnos cargados al construir bloques y columnas, evitando consultas duplicadas por odontólogo.

La lista de historia clínica usa conteo anotado de adjuntos en lugar de traer adjuntos completos cuando solo se necesita mostrar la cantidad.

## Próxima medición recomendada

Más adelante conviene medir consultas con Django Debug Toolbar solo en desarrollo. Por ahora no se instaló para mantener el proyecto simple y limpio.
