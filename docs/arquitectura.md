# Arquitectura del Proyecto

Este documento define las decisiones iniciales de arquitectura para el gestor de turnos odontologico.

La idea es que el proyecto crezca de forma ordenada, con codigo limpio, responsabilidades claras y cambios faciles de mantener.

## Objetivo del sistema

El sistema debe permitir administrar turnos de un consultorio odontologico.

En esta primera etapa se busca resolver:

- Carga de pacientes.
- Carga de odontologos.
- Carga y gestion de turnos.
- Validacion de horarios disponibles.
- Prevencion de turnos superpuestos.
- Preparacion para una futura integracion con Google Calendar.

## Principios de diseno

El proyecto va a seguir estos criterios:

- Modularidad: cada app debe tener una responsabilidad clara.
- Alta cohesion: cada modulo debe agrupar codigo relacionado con un mismo concepto.
- Bajo acoplamiento: una app no debe conocer detalles internos innecesarios de otra.
- Nombres claros: clases, funciones y variables deben expresar su intencion.
- Codigo simple: primero resolver bien el caso actual, sin sobrearmar estructuras prematuras.
- Reglas testeables: cada regla importante del dominio debe poder probarse con tests.
- Cambios incrementales: cada etapa debe dejar el sistema funcionando.

## Modulos actuales

### config

Responsabilidad:

- Configuracion general de Django.
- Registro de apps instaladas.
- Configuracion de autenticacion y redirecciones de login/logout.
- Configuracion de base de datos.
- Configuracion de idioma, zona horaria y archivos estaticos.
- Rutas principales del proyecto.

Este modulo no debe contener reglas de negocio.

### pacientes

Responsabilidad:

- Representar y administrar los datos de los pacientes.
- Centralizar la informacion personal y de contacto.

Modelo principal:

- `Paciente`

Por ahora, esta app no conoce los detalles internos de los turnos. La relacion con turnos aparece desde la app `turnos`.

### turnos

Responsabilidad:

- Representar odontologos.
- Representar disponibilidad de odontologos.
- Representar turnos.
- Validar reglas basicas de agenda.
- Evitar turnos superpuestos.
- Calcular horarios disponibles.
- Guiar la creacion de turnos con horarios disponibles.
- Mostrar agenda diaria y semanal simple.
- Preparar la relacion futura con Google Calendar.

Modelos principales:

- `Odontologo`
- `DisponibilidadOdontologo`
- `Turno`

Esta app concentra las reglas iniciales del dominio de agenda.

## Reglas de negocio actuales

El modelo `Turno` valida que:

- La duracion del turno sea mayor a cero.
- El odontologo este activo para turnos no cancelados.
- El turno entre dentro de una disponibilidad activa del odontologo.
- Los dias sin disponibilidad activa queden bloqueados como no laborables.
- No exista otro turno activo superpuesto para el mismo odontologo.
- Los turnos cancelados no bloqueen horarios.

El selector `obtener_horarios_disponibles` calcula horarios libres usando:

- Disponibilidad activa del odontologo.
- Duracion configurada del turno.
- Turnos pendientes y confirmados ya existentes.
- Estado activo/inactivo del odontologo.

El formulario de creacion de turnos consume ese selector para que la hora se elija desde una lista de horarios libres, en lugar de cargarla manualmente.

Los selectores `obtener_turnos_del_dia` y `obtener_turnos_de_la_semana` concentran las consultas de agenda para que las vistas solo preparen contexto de presentacion.

Estados actuales de un turno:

- `pendiente`
- `confirmado`
- `cancelado`
- `realizado`

## Decisiones tomadas

### Django Admin como primera interfaz

Se usa Django Admin para validar el dominio rapidamente y poder cargar datos desde el inicio.

Esta decision permite avanzar sin invertir todavia en vistas propias, plantillas o frontend.

Mas adelante se agregaran pantallas especificas para usuarios del consultorio.

### Login interno

Las vistas internas de pacientes, turnos y agenda requieren sesion iniciada.

Por ahora se utiliza la autenticacion nativa de Django. La separacion por roles queda como decision futura para no agregar permisos antes de que aparezca una necesidad concreta.

### SQLite para desarrollo local

Se usa SQLite porque simplifica el arranque del proyecto.

Antes de produccion, la base deberia cambiarse a PostgreSQL.

### Validaciones iniciales en los modelos

Las primeras reglas de agenda viven en el modelo `Turno`.

Esto es aceptable en esta etapa porque:

- Las reglas son pocas.
- Estan cerca de los datos que validan.
- Se ejecutan tanto desde admin como desde codigo.

Cuando crezca la logica, se moveran los casos de uso a servicios especificos.

## Evolucion prevista

La arquitectura puede evolucionar asi:

```text
turnos/
|-- models.py
|-- admin.py
|-- forms.py
|-- views.py
|-- services.py
|-- selectors.py
|-- tests.py
`-- integrations/
    `-- google_calendar.py
```

La separacion esperada seria:

- `models.py`: estructura de datos y validaciones esenciales.
- `forms.py`: validaciones propias de formularios.
- `views.py`: manejo de requests y responses.
- `services.py`: casos de uso que modifican datos.
- `selectors.py`: consultas de lectura reutilizables.
- `integrations/`: comunicacion con servicios externos.
- `tests.py`: pruebas del comportamiento del dominio.

Esta estructura se va a crear solo cuando haga falta, no antes.

## Casos de uso futuros

Los siguientes casos de uso deberian vivir fuera del modelo cuando la logica crezca:

- Crear turno.
- Confirmar turno.
- Cancelar turno.
- Reprogramar turno.
- Buscar horarios disponibles.
- Sincronizar turno con Google Calendar.

Ejemplo de nombres esperados:

```python
crear_turno(...)
confirmar_turno(...)
cancelar_turno(...)
reprogramar_turno(...)
obtener_horarios_disponibles(...)
```

## Criterios de codigo limpio

Para mantener el proyecto entendible:

- Una funcion debe hacer una sola cosa.
- Si una funcion necesita demasiadas condiciones, probablemente haya una abstraccion pendiente.
- Evitar nombres genericos como `data`, `obj`, `item` cuando haya un nombre de dominio mejor.
- Evitar duplicar reglas de negocio en varios lugares.
- No mezclar logica de negocio con detalles de interfaz.
- No mezclar logica de negocio con integraciones externas.
- Los tests deben describir comportamiento, no implementacion interna.

## Criterio para agregar nuevas apps

No se debe crear una app nueva por cada modelo.

Una app nueva se justifica cuando aparece un area del dominio con responsabilidad propia.

Ejemplos posibles a futuro:

- `historia_clinica`
- `pagos`
- `notificaciones`
- `integraciones`

Por ahora, `pacientes` y `turnos` son suficientes.

## Integracion futura con Google Calendar

La integracion con Google Calendar no debe quedar mezclada directamente dentro del modelo `Turno`.

Cuando se implemente, deberia estar aislada en un modulo propio, por ejemplo:

```text
turnos/integrations/google_calendar.py
```

La app deberia poder crear turnos aunque Google Calendar falle temporalmente.

Esto ayuda a mantener bajo acoplamiento entre el dominio del sistema y un servicio externo.

## Regla de trabajo por etapa

Cada etapa del proyecto deberia cerrar con:

1. Codigo implementado.
2. Tests actualizados cuando corresponda.
3. `python manage.py check`.
4. `python manage.py test`.
5. README o documentacion actualizada si cambia la forma de usar el sistema.
6. Commit con mensaje claro en espanol.
