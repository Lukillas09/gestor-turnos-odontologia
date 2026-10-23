Implementá el rediseño público V3 descrito en `docs/rediseno-publico-v3/SPEC.md`.

- Las maquetas visuales están en `docs/rediseno-publico-v3/referencias/`. Son HTML de referencia: tomá de ahí el diseño, no el código.
- Antes de empezar, leé `AGENTS.md`, `CLAUDE.md`, `app/turnos/AGENTS.md` y los docs que menciona la sección 0 del SPEC.
- Es un cambio de presentación. No toques reglas de turnos, servicios, permisos, OTP, Turnstile, rate limiting ni idempotencia, y conservá todos los contratos `data-public-*`.
- Trabajá en la rama `feat/rediseno-publico-v3` y seguí el orden de la sección 7, con un commit por paso.
- Primero mostrame un plan breve con los archivos que vas a tocar en el paso 1 y esperá mi OK.
- Después de cada paso corré los tests relevantes y contame qué cambió.
- Si algo del SPEC no coincide con lo que hace el código (por ejemplo, un texto que promete algo que el backend no hace), frená y preguntame. No inventes comportamiento.
