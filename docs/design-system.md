# Sistema de diseño

La interfaz V2 sigue la dirección "clínica moderna, clara y tranquila". El objetivo es que la superficie pública sea simple y cálida, mientras que el panel interno sea compacto, predecible y orientado al trabajo.

## Fundamentos

Los tokens viven en `app/static/css/tokens.css`. El color de marca continúa llegando desde la configuración del consultorio mediante `--primary`, `--primary-dark`, `--primary-soft` y `--primary-contrast`.

### Color

- Navy (`--navy-950` a `--navy-800`): navegación y contraste estructural.
- Azul dinámico: acciones principales, selección y foco.
- Teal y menta: información clínica, confirmación y apoyo visual.
- Verde: estados confirmados o correctos.
- Ámbar: pendientes y elementos que requieren atención.
- Rojo: errores, cancelaciones y acciones destructivas.
- Grises: superficies, bordes, texto secundario y jerarquía.

Los estados siempre incluyen texto o iconografía; nunca dependen solo del color.

### Tipografía

Se utiliza una pila nativa sin descargas externas: `Inter`, `ui-sans-serif`, fuentes del sistema, `Segoe UI`, Roboto, Helvetica y Arial. La escala va de `--font-xs` (metadatos) a `--font-4xl` (hero público). Inputs y contenido móvil conservan 16 px para evitar zoom involuntario.

### Espacio, radio y sombra

- Espaciado: `--space-1` a `--space-8`, desde 4 px hasta 64 px.
- Radio habitual de controles y tarjetas: `--radius-sm` (8 px).
- Radios mayores se reservan para hero, overlays y superficies destacadas.
- Sombras: `--shadow-xs` para tarjetas operativas; `--shadow-md` o superior solo para overlays y focos de atención.

## Componentes

### Botones

Las variantes principales son `.button`, `.button-secondary`, `.button-tertiary`, `.button-success`, `.button-danger`, `.button-icon`, `.button-small` y `.button-large`. Todos tienen foco visible, estado presionado, deshabilitado y loading. El objetivo táctil mínimo es 44 px.

### Tarjetas y paneles

`.ui-card` define borde, superficie y sombra mínima. Las vistas internas usan paneles sin anidar tarjetas decorativas. Las listas repetidas emplean una barra lateral o un icono para facilitar el escaneo.

### Badges

`.status-badge` comunica pendiente, confirmado, cancelado, sincronización o revisión. El punto o forma anterior al texto aporta una segunda señal visual.

### Formularios

`includes/form_field.html` unifica label, requerido, ayuda y errores. `.internal-form-v2` organiza formularios largos por secciones y limita el ancho de DNI, fecha, hora y teléfono. Las acciones pueden permanecer visibles con `.sticky-form-actions`.

### Estados vacíos y carga

`includes/empty_state.html` admite icono o ilustración, explicación y acciones. `.ui-skeleton` se utiliza durante la consulta progresiva de horarios y respeta movimiento reducido.

### Navegación

En escritorio, el panel interno usa sidebar navy y topbar contextual. Hasta 767 px se reemplaza por navegación inferior: Inicio, Agenda, Turnos, Pacientes y Más. El drawer Más tiene overlay, cierre visible, `aria-expanded`, Escape y ciclo de foco.

## Iconografía e ilustraciones

`includes/icon.html` contiene SVG de trazo redondeado, `currentColor` y tamaño estable. Las ilustraciones originales están en `app/static/images/`: hero dental, calendario vacío, confirmación, sin resultados y acceso seguro.

## Movimiento

Las duraciones se mantienen entre 80 y 260 ms. Se permiten entrada suave, hover de 1-2 px, selección, toast, drawer y skeleton. `prefers-reduced-motion: reduce` elimina traslaciones, escalas y shimmer.

## Responsive

- Hasta 520 px: móvil pequeño, una columna y acciones anchas.
- 521-767 px: móvil grande, navegación inferior y filtros colapsables.
- 768-1023 px: tablet, layouts simplificados.
- 1024-1279 px: escritorio.
- Desde 1280 px: escritorio amplio con ancho máximo estable.

No se comprimen siete columnas de agenda en móvil. La vista semanal ofrece navegación por días y contenido vertical. Las tiras de fechas y tabs pueden desplazarse dentro de su propio contenedor sin generar scroll horizontal de página.

## Accesibilidad

La base incluye skip link, landmarks, labels visibles, foco de teclado, `aria-current`, `aria-live`, `aria-busy`, roles de mensajes y títulos jerárquicos. Los botones de solo icono tienen nombre accesible. El objetivo es WCAG 2.2 AA.
