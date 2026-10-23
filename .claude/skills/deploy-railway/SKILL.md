---
name: deploy-railway
description: Checklist de preparación y verificación de un deploy en Railway (app Django) con Supabase como PostgreSQL y Storage. Revisa CI, migraciones, variables y flags antes del deploy, y hace la verificación posterior. No despliega por sí misma.
disable-model-invocation: true
---

# Deploy en Railway

`AGENTS.md` prohíbe desplegar sin pedido explícito. Esta skill **prepara y verifica**; no hace
push ni dispara deploys. Railway despliega desde el repo de GitHub conectado. La rama que dispara
el deploy se configura en Railway y no está documentada en el repo: confirmala con el usuario
antes de asumirla. Un push a esa rama equivale a un deploy y requiere pedido explícito.

Fuentes: `docs/deploy.md` (guía principal), `docs/staging.md`, `docs/configuracion.md`,
`docs/seguridad_produccion.md`, `docs/backups.md`.

## Pipeline (no cambiar sin motivo)

`railway.json` define:

1. Build: `bash scripts/build.sh` → instala `requirements.txt` y corre `collectstatic`.
2. Pre-deploy: `bash scripts/release.sh` → `manage.py migrate --noinput`.
3. Start: `bash scripts/start.sh` → Gunicorn con `WEB_CONCURRENCY` workers.

La base es Supabase: **no** agregar PostgreSQL de Railway. WhiteNoise sirve `app/staticfiles/`.

## 1. Antes del deploy

- [ ] Working tree limpio y commit correcto (`git status`, `git log -1`).
- [ ] CI en verde para ese commit (`gh run list --commit <sha>`); todos los jobs de
      `.github/workflows/ci.yml`, incluidos PostgreSQL y E2E.
- [ ] Migraciones nuevas revisadas (`git diff --stat <deploy-anterior>..HEAD -- '*/migrations/*'`
      y `manage.py sqlmigrate <app> <migración>`):
  - destructivas, de reescritura o con locks largos → backup previo según `docs/backups.md`
    y plan de reversión acordado con el usuario;
  - migraciones clínicas → ventana de mantenimiento según `docs/seguridad_produccion.md`.
- [ ] Variables nuevas: documentadas en `.env.example`, `.env.railway-supabase.example` y
      `docs/configuracion.md`, y **cargadas en Railway antes** del deploy. Los valores secretos
      los carga el usuario; nunca pedirlos ni mostrarlos en el chat.
- [ ] Feature flags (odontograma, agenda inteligente, indicaciones, acceso clínico compartido)
      sin cambios de default salvo pedido explícito.
- [ ] Si cambió el dominio: `DJANGO_ALLOWED_HOSTS` (sin `https://`),
      `DJANGO_CSRF_TRUSTED_ORIGINS` (con `https://`) y redirect URI de Google OAuth.

## 2. Después del deploy

- [ ] El deploy figura como `Deployment successful`. `Queued` o `Failed` significa que sigue
      sirviendo la versión anterior.
- [ ] Logs del pre-deploy sin errores de migración.
- [ ] Smoke manual según el "Checklist de Deploy" de `docs/deploy.md`: login interno, landing,
      solicitud pública y acceso OTP, más lo que haya tocado el cambio.
- [ ] Si se tocaron email o Storage: `python manage.py probar_email` /
      `python manage.py probar_storage_historias` (en Railway, por el usuario).

## 3. Si algo falla

- Troubleshooting de `docs/deploy.md`: `DisallowedHost`, CSRF, 404 por deploy viejo, incidentes
  de Railway (no tocar código si el problema es de la plataforma).
- Volver a un deploy anterior en Railway **no revierte migraciones** ya aplicadas en Supabase.
  Para migraciones irreversibles, el rollback es restaurar el backup previo (`docs/backups.md`).

## Reporte

Informá qué se verificó, qué quedó pendiente del lado del usuario (variables, smoke, OAuth) y
cualquier riesgo de migración.
