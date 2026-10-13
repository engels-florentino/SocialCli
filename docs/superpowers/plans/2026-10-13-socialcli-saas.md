# SocialCli SaaS — Implementation Plan maestro

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** convertir SocialCli en un SaaS instalable por agentes, con identidad por usuario, prueba de 14 días y suscripción de USD 10 al mes.

**Architecture:** ampliar el servicio FastAPI existente; el CLI conserva la ejecución directa contra las redes. Autenticación gestionada por Supabase Auth, metadatos en SQLite y credenciales sociales en el almacén cifrado existente. Stripe gestiona contratación y facturación; el servidor decide el acceso.

**Tech Stack:** Python >=3.11, FastAPI, HTTPX, SQLite, Supabase Auth, Stripe Checkout/Billing/Customer Portal, uv, PyPI y GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-13-socialcli-saas-design.md`.

**Estado:** borrador de planificación para revisión. Precio confirmado por el usuario: USD 10 al mes. Se preparó únicamente el dominio raíz en NPM con HTTPS y la web actual; el SaaS no se ha implementado. Las demás propuestas del diseño siguen sujetas a revisión.
Revisión ampliada del 13 de octubre: cierra huecos de legacy, agentes sin stdin,
cobros duplicados, eliminación, mantenimiento y UI consistente.

## Global Constraints

- El repositorio objetivo es `app/SocialCli`, con Git propio.
- El proyecto privado Social y su ejecutor `socialctl 0.1.0` quedan fuera del cambio.
- El usuario dispone de Stripe y confirmó que se trabaje con claves de prueba.
- Precio confirmado: USD 10.00 al mes; Stripe `usd`, 1000 centavos, intervalo mensual y cantidad de intervalos 1.
- Dominio confirmado: `socialcli.app`; origen canónico `https://socialcli.app`.
- Una suscripción controla el servicio alojado del operador.
- La identidad es el identificador verificado del usuario.
- La sesión web no se copia al CLI.
- Conectar y pagar no conceden aprobación para publicar.
- Google se pasará a producción después.
- Las dependencias nuevas pertenecen al extra del servidor salvo necesidad demostrada del CLI.
- No cargar automáticamente el `.env` del proyecto privado desde el paquete público.
- Toda UI propia comparte tokens y componentes del diseño SocialCli aprobado.
- Mantener el producto en inglés y soporte macOS/Linux en el MVP.
- Cada fase incluye validación visual de sus pantallas y estados nuevos.

## Review Focus

1. Otro usuario, otra instalación o una marca con el mismo nombre no pueden apropiarse de una conexión.
2. Un cliente modificado, una ruta legacy o el callback de checkout no pueden saltarse el cobro.
3. Activaciones simultáneas y reinstalaciones no pueden reiniciar los 14 días.
4. Webhooks repetidos, fuera de orden o perdidos no pueden otorgar acceso incorrecto.
5. Fallos entre la escritura de credenciales y su vinculación deben recuperarse sin repetir OAuth o publicaciones a ciegas.

## Cómo ejecutar este plan

Es un programa de subproyectos. Antes de implementar cada fase, desarrollar su
plan específico con interfaces y tests a partir de este diseño revisado.
No ejecutar todos los subsistemas como un único cambio. Cada fase termina con
pruebas apropiadas, revisión y un commit limitado a esa fase.
Método propuesto: ejecución nativa por el agente principal; no se han autorizado subagentes.

Orden: sistema UI → identidad → vinculaciones → prueba → Stripe → control de acceso →
distribución/web → piloto. El trabajo de Google a producción se hace después,
según la instrucción del usuario, antes de ofrecer esa integración al público general.

## Fase 0: sistema UI y recorrido consistente

**Entregable:** una referencia visual y componentes compartidos para toda superficie.

**Archivos propuestos:** crear `socialctl/ui/__init__.py`,
`socialctl/ui/assets/tokens.css`, `socialctl/ui/assets/components.css`,
`scripts/build_ui_assets.py` y `docs/design-system.md`; adaptar
`docs/style.css`, `socialctl/connection_service/assets/connection.css`,
`socialctl/connection_service/landing.py` y tests de assets/plantillas.

- [ ] Inventariar las pantallas actuales; usar logo y pantallas de conexión 0.2.6 como referencia aprobada.
- [ ] Definir los tokens indicados en el diseño y componentes para navegación,
  botones, formularios, tarjetas, avisos, carga, vacío, éxito y recuperación.
- [ ] Generar assets web y wheel desde una fuente única; comprobar que el build no deriva colores independientes.
- [ ] Definir las plantillas de correo y la configuración de branding de Stripe/Supabase.
- [ ] Crear fixtures visuales del recorrido completo a 360, 390 y 1440 px,
  con teclado, foco, zoom, errores de campo y movimiento reducido.
- [ ] Verificar empaquetado y CSP sin añadir fuentes externas ni generar media.
- [ ] Revisar y hacer commit de la fase. Las fases siguientes reutilizan sus componentes.

**Aceptación:** no aparece una página nueva con otra identidad visual; Stripe
se ajusta a sus capacidades de branding sin prometer HTML idéntico.

### Preparación del dominio, antes de exponer el piloto

Avance comprobado el 13-oct-2026: raíz `socialcli.app` apunta al servidor y
responde 200 con TLS válido; NPM Host 39/Certificado 43 sirven la web actual.
`www` devuelve NXDOMAIN y queda pendiente. No se migraron broker ni callbacks.
Registro: `deploy/connection-service/domain-socialcli-app-2026-10-13.md`.

- [ ] Confirmar IPv4 pública actual del servidor. En Namecheap, A `@` a esa IP y
  CNAME `www` a `socialcli.app`; conservar registros de correo y TXT.
- [ ] Crear virtual host para raíz/www en Nginx Proxy Manager, certificado HTTPS
  válido, renovación y redirección de www al origen canónico.
- [ ] Comprobar DNS, TLS, rutas públicas y acceso al piloto aislado antes de
  modificar orígenes/callbacks o publicar instrucciones de instalación.
- [ ] Configurar `https://socialcli.app` en SaaS, TrustedHost, cookies, CSP,
  URLs permitidas de Supabase, retorno de Checkout/portal y webhook Stripe test.
- [ ] Registrar y probar callbacks nuevos de los proveedores cuando corresponda,
  manteniendo los actuales. Probar flujo de cookies, OAuth pendiente y llamadas
  POST en ambos dominios sin redirecciones que rompan autenticación.
- [ ] Preservar clientes, media y rutas actuales de `social.florentino.pro`;
  el hostname anterior debe conservar el cierre legacy de la fase 5.
- [ ] Configurar correo del dominio con el proveedor elegido y verificar entrega.

**Aceptación:** HTTPS funcional en socialcli.app, www canónico y clientes actuales
operativos. DNS/origen nuevos no acreditan aprobación del proveedor ni Google en producción.

## Fase 1: cuentas de usuario y acceso web

**Entregable:** un usuario accede por código de correo y consulta su cuenta.

**Archivos propuestos:**
- Crear `socialctl/saas/models.py`, `auth.py`, `store.py`, `routes.py` y `__init__.py`.
- Crear `socialctl/saas/assets/account.css` y plantillas de cuenta.
- Modificar `socialctl/connection_service/app.py`, `settings.py`, `pyproject.toml` y `uv.lock`.
- Crear `tests/test_saas_auth.py` y `tests/test_saas_store.py`.

**Contrato a fijar:** `AuthenticatedUser(id: str, email: str)` se obtiene solo de
una sesión verificada por el proveedor de identidad. `require_user(request)`
rechaza una sesión ausente, inválida o revocada. Los propietarios se consultan
por ese `id`; nunca se acepta `user_id` del cuerpo como autoridad.

- [ ] Escribir tests de sesión válida, expirada, revocada, identidad falsificada y cierre de sesión.
- [ ] Ver fallar esos tests antes de implementar autenticación y migraciones.
- [ ] Implementar código de correo, sesión web segura y migraciones versionadas reversibles.
- [ ] Probar CSRF, origen, redirecciones externas, OTP repetido y límites por
  intento/usuario; añadir revocación propia de sesión sin asumir que un JWT válido prueba una sesión vigente.
- [ ] Configurar un proyecto Supabase exclusivo y SMTP de producción cuando se autorice la ejecución externa.
- [ ] Verificar remitente/entrega, reenvío y caída de SMTP con mensajes de recuperación coherentes.
- [ ] Comprobar que dos usuarios tienen datos separados y que ninguna clave de servidor llega al navegador.
- [ ] Revisar y hacer commit de la fase.

**Aceptación:** entrar, salir y recuperar acceso funcionan; un usuario no consulta al otro.

## Fase 2: vincular agentes y conexiones al propietario

**Entregable:** el mismo usuario utiliza varias instalaciones revocables.

**Archivos propuestos:**
- Crear `socialctl/saas/pairing.py`, `connections.py` y `socialctl/connections/account_cli.py`.
- Modificar `socialctl/connections/client.py`, `credential_store.py`, `cli.py`,
  `socialctl/cli.py` y los tests de conexión existentes.
- Crear `tests/test_saas_pairing.py`, `test_saas_ownership.py` y `test_saas_recovery.py`.

**Contrato:** un emparejamiento corto se aprueba desde la sesión web del usuario;
solo entrega una credencial de instalación limitada. Las rutas SaaS son
`/api/saas/v1/`. La creación de OAuth ocurre después de la aprobación del agente.
La espera de acceso a SocialCli dura 30 minutos; la del OAuth de proveedor, 600
segundos. Son intentos separados, con sondeo y comprobación de posesión propios.

- [ ] Escribir tests para dos instalaciones del mismo usuario, otro usuario,
  enlace vencido, doble consumo, ausencia de keyring y recuperación interrumpida.
- [ ] Ver fallar los tests e implementar el emparejamiento con prueba de posesión.
- [ ] Añadir al CLI acceso/estado/cierre de sesión de SocialCli, separados de la conexión de cada red.
- [ ] Implementar selección y aprobación de cuenta exacta en el navegador para
  SaaS sin stdin ni `--account-id` conocido de antemano; el CLI verifica el
  resultado ligado a instalación, marca y plataforma. Mantener la regla legacy.
- [ ] Probar sustitución de cuenta, Page/Instagram vinculados, pérdida de respuesta,
  cancelación y rotación de una instalación sin afectar a otra.
- [ ] Conservar la conexión anterior hasta activar la sustituta y guardar sus
  credenciales; probar fallo de OAuth, caída del proceso y límite concurrente.
- [ ] Mantener los 600 segundos del OAuth de proveedor; el registro y pago no consumen esa ventana.
- [ ] Añadir estado JSON y descubrimiento de capacidades/versión; el cliente antiguo
  recibe instrucciones de actualización y no cae silenciosamente a rutas sin cobro.
- [ ] Añadir migración explícita: exige sesión nueva más credencial legacy válida.
- [ ] Verificar desconexión, revocación y recuperación sin robar asociaciones por nombre o correo.
- [ ] Revisar y hacer commit de la fase.

**Aceptación:** otro agente puede acceder con aprobación del mismo dueño sin
repetir consentimiento del proveedor si su autorización sigue válida.

## Fase 3: prueba gratuita y límites del plan

**Entregable:** prueba única de 14 días desde la primera conexión activada.

**Archivos propuestos:**
- Crear `socialctl/saas/access.py` y `trials.py`; extender `store.py` y `models.py`.
- Crear `tests/test_saas_trials.py` y `test_saas_access.py`.

**Contrato:** `access_for(user_id: str, now: datetime)` devuelve una decisión
con `allowed`, `reason` y `access_until`. La activación atómica fija la prueba
solo si no existía; el tiempo se mide en UTC en el servidor.

- [ ] Probar conexión fallida, activación concurrente, frontera exacta de 14 días,
  reinstalación, otra zona horaria y otra marca con el mismo nombre.
- [ ] Implementar activación, límites propuestos de una marca/cuatro conexiones y estado visible en `/account/`.
- [ ] Verificar que ninguna instalación controla el reloj ni reinicia la prueba.
- [ ] Mantener acceso a pago, estado y desconexión después del vencimiento.
- [ ] Probar sustitución de una conexión y activaciones entre plataformas bajo
  unicidad por propietario/plataforma; desconectar no libera una nueva prueba.
- [ ] Persistir recordatorios de prueba a 3 días y 1 día del fin, con una entrega
  por usuario/hito y fechas exactas compartidas por cuenta, CLI y correos.
- [ ] Revisar y hacer commit de la fase.

**Aceptación:** dos activaciones simultáneas producen una sola fecha de prueba.

## Fase 4: Stripe test, contratación y gestión de suscripción

**Entregable:** contratar, recuperar un pago y cancelar desde páginas de Stripe.

**Archivos propuestos:**
- Crear `socialctl/saas/billing.py`, `webhooks.py` y `billing_reconcile.py`.
- Modificar `settings.py`, `routes.py` y el ejemplo privado de configuración de despliegue.
- Crear `tests/test_saas_billing.py` y `test_saas_webhooks.py`.

**Configuración:** `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`,
`STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID` y `SOCIALCLI_BILLING_MODE=test`.
El operador podrá mapear las actuales `SECRET_KEY`/`PUBLISH_KEY` al archivo
privado de despliegue. No se añaden claves a Git ni se imprime su contenido.
El secreto de webhook se configura aparte; no es la clave API.

- [ ] Crear producto/precio exclusivamente en Stripe test al ejecutar esta fase:
  `currency=usd`, `unit_amount=1000`, `recurring.interval=month`, `recurring.interval_count=1`.
- [ ] Probar que el servidor rechaza precios de otra moneda, importe o intervalo;
  comprobar USD 10 al mes en web, Checkout, portal y correos.
- [ ] Probar creación idempotente del cliente, checkout de una suscripción, cuenta
  ajena, precio manipulado y redirección de éxito sin webhook.
- [ ] Implementar Checkout y Customer Portal con el cliente Stripe del usuario autenticado.
- [ ] Serializar checkouts concurrentes con unicidad local e idempotencia Stripe;
  recuperar sesiones después de un timeout antes de crear otra suscripción.
- [ ] Habilitar checkout solo después del fin de la prueba, con fecha de servidor;
  probar una petición directa anterior al vencimiento y un Stripe `trialing`
  inesperado. No añadir setup, cobro futuro o una segunda prueba al MVP.
- [ ] Mostrar importe/moneda/fecha del primer cobro, cancelación y renovación;
  aplicar el branding compartido a Checkout, portal y correos de Stripe.
- [ ] Probar firma inválida, modo live inesperado, duplicados, eventos desordenados,
  impago, cancelación al final de periodo y recuperación de pago.
- [ ] Implementar webhooks con verificación de firma y reconciliación desde Stripe.
- [ ] Verificar el cuerpo original de la petición, límites de tamaño, `livemode`,
  cuenta Stripe, versión API fijada y asociación cliente/suscripción/precio.
- [ ] Guardar evento y tarea pendiente antes de confirmar recepción; no usar
  timestamps como autoridad de orden ni hacer llamadas externas dentro de un lock SQLite.
- [ ] Registrar reembolso y disputa sin asumir que cancelan la suscripción;
  fijar política comercial antes de automatizar esos efectos en live.
- [ ] Contratar con tarjetas de prueba y Stripe test clocks cuando proceda;
  verificar pago que exige acción adicional, checkout abandonado,
  cancelación, fin de periodo y suscripción recuperada. El reloj de Stripe no
  sustituye al reloj controlado de la prueba local de SocialCli.
- [ ] Revisar y hacer commit de la fase.

**Aceptación:** un callback manipulado no activa nada; el pago de prueba confirmado sí.

## Fase 5: aplicar el acceso en el broker y migrar sin atajos

**Entregable:** solo prueba válida o suscripción habilitada obtienen nuevos tokens SaaS.

**Archivos propuestos:**
- Crear `socialctl/saas/token_routes.py` y `errors.py`.
- Modificar `socialctl/connection_service/app.py`, `store.py`,
  `socialctl/connections/client.py` y `docs/connections.md`.
- Crear `tests/test_saas_token_access.py` y `test_saas_legacy_boundary.py`.

- [ ] Probar todas las filas de la tabla de acceso del diseño y los límites del plan.
- [ ] Probar cliente antiguo, credencial SaaS por `/v1/`, conexión legacy ajena,
  ausencia de vinculación, cambio de dueño y caída de Stripe durante una emisión.
- [ ] Cerrar la creación anónima por `POST /v1/authorizations` al corte comercial;
  registrar la lista de conexiones existentes y drenar solo los intentos
  anteriores durante 600 segundos. No otorgar excepciones por versión o marca.
- [ ] Probar reinstalación de 0.2.6, inicio de OAuth legacy antes/después del corte,
  creación directa por HTTP y restauración del release anterior con el cierre conservado.
- [ ] Implementar decisiones desde estado verificado del servidor, sin consultar Stripe en cada emisión.
- [ ] Mantener bloqueadas las rutas SaaS con estado indeterminado y ofrecer recuperación.
- [ ] Probar precedencia: suspensión, periodo pagado, prueba original vigente y
  denegación; un checkout fallido no elimina días gratis que aún correspondan.
- [ ] Verificar que estado, pago, desconexión y revocación siguen disponibles sin suscripción.
- [ ] Implementar revocación de instalación y eliminación de cuenta con limpieza
  idempotente de credenciales, identidad y suscripción; fallos externos quedan
  pendientes. Probar que no se borra ni factura una cuenta ajena.
- [ ] Probar eliminación/recreación de identidad contra el marcador mínimo de
  elegibilidad; definir su retención publicada sin conservar tokens sociales borrados.
- [ ] Documentar que tokens ya entregados conservan su vigencia y que los trabajos
  aceptados por las redes no se cancelan al vencer el plan.
- [ ] Revisar y hacer commit de la fase.

**Aceptación:** modificar el CLI o llamar a una ruta legacy no evita el control SaaS.

## Fase 6: distribución PyPI y web para agentes

**Entregable:** una URL oficial permite instalar y comenzar el flujo sin clonar GitHub.

**Archivos propuestos:**
- Crear `.github/workflows/release.yml`, `docs/agents.md` y la ruta pública `/agents`.
- Modificar `README.md`, `docs/installation.html`, `docs/ai-agents.md`, sus páginas
  HTML, `docs/index.html`, `SECURITY.md` y `pyproject.toml`.
- Modificar `deploy/connection-service/nginx-locations.conf` y la guía de despliegue.

- [ ] Confirmar propiedad del nombre en PyPI y configurar Trusted Publishing.
- [ ] Preparar release candidata, construir wheel/sdist y comprobar archivos incluidos.
- [ ] Probar primero TestPyPI con dependencias desde PyPI sin anunciar una instalación pública inexistente.
- [ ] Separar TestPyPI/PyPI, fijar permisos OIDC al entorno de release, publicar
  desde tag revisado y verificar el wheel realmente descargado antes de anunciarlo.
- [ ] Verificar instalación fuera del checkout, ayuda, keyring y backend cifrado explícito.
- [ ] Publicar instrucciones de `uv tool install socialcli` y alternativa pip en entorno virtual.
- [ ] Incluir instrucciones específicas para agentes sin stdin, persistencia de
  workspace/credenciales y el caso en que aún no conocen el ID de la cuenta.
- [ ] Cubrir Python/uv/ffprobe ausentes, PATH, actualizaciones, storage cifrado y
  nueva conversación con entorno efímero; probar macOS/Linux sin anunciar Windows.
- [ ] Comprobar proxy, CSP, navegación móvil, flujo por teclado y rutas de media existentes.
- [ ] Publicar el paquete real solo tras aprobación del artefacto revisado; hacer commit de documentación coherente.

**Aceptación:** Muse/Grok siguen una misma URL y completan una instalación limpia.

## Fase 7: fiabilidad y piloto del SaaS

**Entregable:** diagnóstico preciso y piloto de extremo a extremo con Stripe test.

**Archivos propuestos:**
- Modificar `socialctl/connections/client.py`, `socialctl/connection_service/providers.py`,
  `app.py`, `socialctl/identity.py`, `socialctl/metricas/analytics_client.py` y guías de agentes.
- Crear `tests/test_connection_error_codes.py` y `test_saas_end_to_end.py`.
- Extender `tests/test_connection_service.py` y pruebas de Analytics/identidad.

- [ ] Separar errores de renovación pendiente, `invalid_grant`, red/proxy, permisos,
  archivo local ausente y expiración propia del broker.
- [ ] Reproducir que hoy HTTP 409 se convierte siempre en una recomendación de reconectar.
- [ ] Implementar códigos y mensajes saneados; ningún fallo de red ordena desconectar la cuenta.
- [ ] Diseñar y aprobar expresamente la política de renovación en lecturas antes
  de cambiar las pruebas existentes de `refresh=False`.
- [ ] Revisar el límite propio de 90 días sin mezclarlo con los 7 días de Google Testing.
- [ ] Diseñar rotación de credenciales de instalación y expiración de autorización
  por 90 días de inactividad, separadas de la caducidad impuesta por cada red;
  probar limpieza, rotación interrumpida y dos instalaciones del mismo usuario.
- [ ] Ejecutar dos usuarios, dos agentes, prueba vencida, pago, cancelación,
  reconexión necesaria y pérdida de almacenamiento en fixtures ficticias.
- [ ] Ejecutar `uv run pytest -q`, `uv build` y smoke de wheel instalado.
- [ ] Preparar despliegue aislado del piloto, backup consistente y rollback de código y datos compatibles.
- [ ] Añadir mantenimiento SaaS cada 5 minutos para eventos, conciliación,
  recordatorios y eliminación pendiente; unidades propias, sin timers privados.
- [ ] Probar reinicio a mitad de tarea, mantenimiento atrasado, fallo de terceros,
  lock SQLite, webhook antes/después de migración y restauración aislada.
- [ ] Comprobar health/readiness saneados y alertas privadas para correo, pagos,
  renovación y trabajos pendientes; los logs nunca contienen tokens, OTP ni códigos OAuth.
- [ ] Verificar el catálogo visual completo del diseño y flujos reales de agentes
  en un workspace ficticio. No presentar tests simulados como piloto Muse/Grok completado.
- [ ] Verificar piloto autorizado sin publicaciones reales salvo preview y aprobación específicos.

**Aceptación:** no se confunde un fallo temporal con una revocación y no hay acceso cruzado.

## Fase posterior: habilitar producción y cobros reales

- [ ] Verificar en Google Cloud el estado real, scopes, dominios y callbacks.
- [ ] Separar testing/producción, completar branding y las verificaciones aplicables;
  un cambio de cliente OAuth puede requerir una reconexión controlada.
- [ ] Tratar verificación OAuth y auditoría/restricciones de YouTube como procesos distintos.
- [ ] Comprobar un piloto externo y continuidad más allá de siete días; no prometer fecha de aprobación.
- [ ] Confirmar acceso externo y permisos necesarios de cada integración Meta/TikTok ofrecida.
- [ ] Verificar que el precio live mantiene USD 10 al mes; revisar términos,
  tratamiento de datos y el valor propio del servicio.
- [ ] Aprobar retención, eliminación, reembolsos/disputas, impuestos y precio total
  mostrado; preparar canal de soporte y mensajes de disponibilidad por red.
- [ ] Configurar claves, precio y webhook Stripe live separados de test; comprobar el importe mostrado.
- [ ] Presentar el despliegue y la oferta concretos para aprobación antes de cobrar o abrir acceso general.

## Decisiones pendientes antes de implementar

1. Aprobar o cambiar la propuesta de 14 días sin tarjeta.
2. Confirmar el límite propuesto de una marca/cuatro conexiones; el precio ya está confirmado en USD 10 al mes.
3. Aprobar Supabase Auth dedicado y el remitente de correo de acceso.
4. Revisar el diseño y elegir ejecución nativa o por subagentes; por ahora no se ha iniciado ninguna.
5. Aprobar retención de autorizaciones por inactividad y política de reembolsos/disputas antes del lanzamiento live.

## Matriz de cobertura de la revisión

| Riesgo/experiencia | Fase y verificación |
|---|---|
| UI diferente entre pantallas y proveedores de cobro | 0; capturas completas y branding de Stripe/Supabase |
| Acceso inicial, correo, CSRF y sesiones revocadas | 1; tests de sesión/OTP y recuperación |
| Agente sin stdin o sin ID previo | 2; selección explícita en navegador y sondeo autenticado |
| Dos agentes, cuentas ajenas y rotación | 2 y 7; aislamiento y recuperación sin repetir OAuth |
| Trial concurrente, reinstalación y recordatorios | 3; reloj controlado y activación única |
| Cobro anticipado o prueba Stripe duplicada | 4; checkout posterior al vencimiento y prueba local única |
| Doble suscripción y pago que requiere acción | 4; unicidad, idempotencia y tarjetas de prueba |
| Webhooks duplicados/desordenados/perdidos | 4 y 7; persistencia y mantenimiento cada 5 minutos |
| Atajo instalando cliente 0.2.6 | 5; cierre real de nuevos OAuth legacy y prueba HTTP directa |
| Revocación, eliminación y limpieza incompleta | 5 y 7; bloqueo y tareas idempotentes |
| Instalación limpia y dependencias del sistema | 6; wheel descargado, macOS/Linux y backend seguro |
| Proxy, CSP, assets y media existentes | 0 y 6; empaquetado y comprobación de rutas |
| Dominio nuevo, TLS y callbacks antiguos | Preparación del dominio; DNS/TLS, POST y cookies por origen |
| Backup, migraciones y rollback que abre el cobro | 7; ensayo aislado conservando el cierre legacy |
| Permisos públicos y cobros reales | Fase posterior; aprobación de oferta y despliegue concretos |

## Evidencia y revisión de este plan

Se revisaron el código actual del broker, los clientes de conexiones, el keyring,
las rutas de lectura que usan `refresh=False`, los tests y el despliegue documentado.
Context7 aportó documentación de Google OAuth, Stripe y Supabase; se contrastó
con las fuentes oficiales enlazadas en el diseño. La preparación del plan no
demuestra disponibilidad pública de proveedores ni funcionalidades aún no implementadas.
