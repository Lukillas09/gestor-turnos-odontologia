# Runbook de incidente de integridad clínica

## Objetivo

Preservar evidencia y limitar el alcance cuando el verificador informa una inconsistencia,
se compromete la clave HMAC o aparece una modificación no explicada. Este runbook no
reemplaza el plan institucional de incidentes ni el asesoramiento legal y de seguridad.

## Activación

Aplicar este procedimiento ante cualquiera de estas señales:

- `verificar_integridad_historias` devuelve errores;
- un ZIP falla por diferencia de SHA-256;
- un trigger o constraint fue desactivado sin cambio autorizado;
- se perdió, divulgó o reemplazó la clave clínica;
- base, Storage o backups presentan cambios no explicados;
- aparece una edición o eliminación directa de contenido clínico.

## Acciones inmediatas

1. No modificar, borrar, volver a sellar ni “corregir” el registro afectado.
2. Bloquear temporalmente nuevas escrituras clínicas. Si es necesario, detener la app o
   aplicar modo mantenimiento sin destruir procesos ni datos.
3. Revocar accesos sospechosos y preservar el estado de cuentas, roles y sesiones para la
   investigación; no eliminar las cuentas involucradas.
4. Preservar logs de Railway, PostgreSQL, Supabase y aplicación con sus timestamps.
5. Crear copias de resguardo de PostgreSQL y Storage en un destino externo restringido.
6. Preservar la clave vigente y sus metadatos de custodia. No incluirla en tickets, chats,
   logs, base de datos ni manifiestos exportados.
7. Registrar hora de detección, persona informante, IDs internos afectados y acciones
   tomadas. No copiar contenido clínico a canales operativos no autorizados.

## Verificación y alcance

Ejecutar primero sin leer binarios:

```powershell
python manage.py verificar_integridad_historias --fallar-si-hay-errores
```

Limitar por paciente o asiento para repetir una comprobación:

```powershell
python manage.py verificar_integridad_historias --paciente ID
python manage.py verificar_integridad_historias --historia ID
```

Cuando Storage esté preservado y el volumen sea aceptable:

```powershell
python manage.py verificar_integridad_historias --historia ID --verificar-adjuntos
```

Comparar, sin alterar el origen:

- sellos, folios y secuencias informados;
- backups anteriores y posteriores al evento;
- logs de acceso y despliegue;
- cambios de variables y privilegios;
- integridad y disponibilidad de Storage;
- historial de migraciones y triggers PostgreSQL.

No imprimir snapshots ni datos personales para compartir el diagnóstico. Usar IDs internos
y códigos de error del comando.

## Escalamiento

Notificar al responsable del consultorio y a las personas designadas para seguridad,
privacidad y custodia clínica. Solicitar asesoramiento legal para determinar obligaciones
de documentación, comunicación y respuesta al paciente o autoridades aplicables.

Si la clave fue comprometida:

- no reemplazarla de inmediato sin preservar una copia controlada;
- congelar nuevas escrituras;
- identificar desde cuándo pudo estar expuesta;
- revisar acceso simultáneo a base, código, backups y secretos;
- diseñar una migración con versionado de claves antes de reanudar.

Si la clave se perdió, conservar la base y los sellos existentes. Una clave nueva no puede
verificar retrospectivamente los HMAC anteriores.

## Recuperación

Restaurar únicamente mediante un procedimiento autorizado y reproducible:

1. Seleccionar un backup cuya integridad y fecha estén documentadas.
2. Restaurar PostgreSQL y Storage juntos en un entorno aislado.
3. Configurar la clave correspondiente a ese backup mediante custodia autorizada.
4. Aplicar migraciones esperadas.
5. Ejecutar verificación completa, incluidos adjuntos.
6. Comparar conteos y muestras autorizadas con el origen preservado.
7. Obtener aprobación del responsable antes de promover la restauración.
8. Mantener la evidencia original sin sobrescribirla.

No editar directamente un asiento para hacer desaparecer un error. Una aclaración clínica
posterior debe registrarse como enmienda una vez que el sistema sea confiable y vuelva a
estar habilitado.

## Cierre

Documentar causa, alcance, timeline, datos afectados, controles que funcionaron, acciones
de recuperación y medidas preventivas. Registrar la resolución en el sistema institucional
de incidentes sin incluir secretos ni contenido clínico completo.

Antes de cerrar:

- confirmar que PostgreSQL y Storage tienen backups externos verificados;
- confirmar que triggers, constraints y permisos están activos;
- ejecutar nuevamente el verificador;
- revisar accesos y rotar credenciales comprometidas mediante un plan aprobado;
- registrar quién autorizó la reanudación de escrituras;
- programar una prueba posterior de restauración;
- actualizar este runbook con las lecciones aprendidas.
