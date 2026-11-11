# Runbook: `rls_auto_enable()` en Supabase

## Objetivo

Cerrar el aviso de seguridad de Supabase "función `SECURITY DEFINER` ejecutable por `anon` y
`authenticated`" sobre `public.rls_auto_enable()` **sin romper** la activación automática de RLS
en las tablas nuevas.

## Contexto

- `rls_auto_enable()` no la crea ninguna migración del repo. Es la función del event trigger
  `ensure_rls` (`ddl_command_end`) de Supabase: cada vez que una migración de Django crea una
  tabla en `public`, activa RLS en ella. Por eso las tablas del proyecto tienen RLS activo.
- RLS activo **sin políticas** es intencional: la Data API (PostgREST) queda denegada y Django se
  conecta con un rol con `bypassrls`. No agregar políticas ni desactivar RLS.
- La función es `SECURITY DEFINER`, pertenece a `postgres` y por defecto tiene `EXECUTE` para
  `PUBLIC`, `anon`, `authenticated` y `service_role`; por eso aparece en `/rest/v1/rpc`.
  (Al ser una función de event trigger no se puede invocar como RPC útil, pero el aviso persiste.)

## Por qué es un paso manual y no una migración

1. `ensure_rls` depende de esta función. Revocar `EXECUTE` no debería afectar su disparo, pero si
   lo hiciera el fallo sería **silencioso**: las tablas nuevas quedarían sin RLS y expuestas a la
   Data API. Solo se detecta creando una tabla de prueba (paso 3).
2. Una migración exigiría que el rol de Django sea el dueño de una función que no es del repo; un
   `REVOKE` fallido bloquearía el despliegue.
3. En el `postgres:17` del CI no existe esta función ni los roles de Supabase, así que una
   migración sería siempre un no-op ahí y no aportaría cobertura.

## Procedimiento

Ejecutar en el SQL Editor de Supabase como `postgres`. Cada paso es idempotente.

### 1. Ver el estado actual (solo lectura)

```sql
select p.proname, p.proowner::regrole as dueño, p.prosecdef as security_definer, p.proacl
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname = 'rls_auto_enable';

select evtname, evtevent, evtenabled, evtfoid::regproc as funcion
from pg_event_trigger
where evtname = 'ensure_rls';
```

Si la función o el event trigger no existen, no hay nada que hacer.

### 2. Revocar la ejecución a los roles de la Data API

```sql
do $$
begin
  if to_regprocedure('public.rls_auto_enable()') is not null then
    revoke execute on function public.rls_auto_enable() from PUBLIC, anon, authenticated;
  end if;
end
$$;
```

Hay que revocar también `PUBLIC`: si no, `anon` y `authenticated` la siguen heredando. El dueño
(`postgres`) y `service_role` conservan el acceso.

### 3. Verificar que `ensure_rls` sigue activando RLS

Dentro de una transacción que se revierte (no deja ninguna tabla):

```sql
begin;
create table public._rls_check (id integer);
select relrowsecurity from pg_class where oid = 'public._rls_check'::regclass;
rollback;
```

`relrowsecurity` debe ser `true`. Si da `false`, **deshacer el paso 2** (ver Reversa) y no
continuar.

### 4. Volver a correr los advisors

El aviso sobre `rls_auto_enable` debe desaparecer. Los avisos sobre RLS sin políticas y sobre
índices sin uso no se tocan (son esperados en este proyecto).

### 5. Opcional: no exponer `public` en la Data API

Django no usa PostgREST. Si el proyecto no consume la Data API, se puede quitar `public` de
"Exposed schemas" en Settings, API del panel: así ninguna función de `public` queda disponible
vía `/rest/v1/rpc`. Es un cambio de configuración del proyecto, no de la base.

## Reversa

```sql
grant execute on function public.rls_auto_enable() to PUBLIC, anon, authenticated;
```

## Cuándo repetirlo

Al crear un proyecto nuevo (staging, producción, restauración desde backup): los privilegios
vienen con el proyecto, no con las migraciones.
