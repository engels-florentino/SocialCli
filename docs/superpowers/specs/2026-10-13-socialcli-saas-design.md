# SocialCli SaaS — diseño para revisión

Fecha: 13 de octubre de 2026.
Estado: propuesta para revisión; autoriza planificación, no implementación ni despliegue.
Revisión ampliada: 13 de octubre de 2026. Incluye el requisito expreso del usuario
de mantener una UI consistente en todo lo que vea el cliente.

## Objetivo y alcance confirmado

Convertir la aplicación pública SocialCli 0.2.6 en un servicio de suscripción que
usuarios de Muse, Grok y otros agentes con terminal puedan instalar, conectar y
utilizar desde sus conversaciones. La instalación debe ser sencilla y la cuenta
del cliente debe funcionar con distintos agentes.

El usuario pide el SaaS completo, rápido y sencillo. Google se pasará a producción
después; la construcción inicial y las pruebas de cobro no dependen de hacerlo ahora.
El repositorio objetivo es `app/SocialCli`, con Git propio. El proyecto privado
Social y su ejecutor `socialctl 0.1.0` quedan fuera del cambio.

El usuario dispone de Stripe y confirmó que se trabaje con claves de prueba.
Se comprobó que `/Users/admin/Desktop/Projects/Social/.env` contiene las variables
`SECRET_KEY` y `PUBLISH_KEY`, ambas de prueba; el archivo está ignorado y no
versionado. Sus valores no se copian a documentos, código ni conversaciones.
El usuario confirmó una suscripción de **USD 10 al mes**. No se ha confirmado
un producto/precio creado en Stripe ni un secreto de webhook.

## Precio confirmado

- Suscripción mensual: **USD 10.00**.
- Precio recurrente Stripe: `currency=usd`, `unit_amount=1000`,
  `recurring.interval=month`, `recurring.interval_count=1`.
- El identificador del precio se configura en el servidor; el CLI no decide
  el importe. Web, Checkout, portal y correos deben mostrar el mismo precio mensual.
- Tratamiento fiscal pendiente de definir antes de live; no asumir que se
  autorizó un cargo adicional ni anunciar un total incompatible con Checkout.

## Dominio confirmado y preparación del servidor

El usuario confirmó **socialcli.app** como dominio principal. Origen canónico:
`https://socialcli.app`; `www.socialcli.app` redirige a ese origen. La web pública,
cuenta, precios, instalación e instrucciones para agentes utilizan ese dominio.
Las rutas SaaS se publican bajo `https://socialcli.app/api/saas/v1/`.

DNS en Namecheap: A para `@` a la IPv4 pública del servidor y CNAME `www` a
`socialcli.app`, conservando MX/TXT y eliminando solo conflictos de esos hosts.
La consulta del 13-oct-2026 encontró nameservers de Namecheap y una A de
`socialcli.app` distinta de la del servidor. `florentino.pro` y
`social.florentino.pro` resolvían a `71.250.63.180`; confirmar esa dirección al
ejecutar, sin incrustarla en la aplicación. Posteriormente el usuario actualizó
el DNS raíz y se preparó en NPM el host 39 con HTTPS verificado, sirviendo la web
actual. `www` sigue pendiente de DNS. Evidencia en
`deploy/connection-service/domain-socialcli-app-2026-10-13.md`.

Preparar virtual host en Nginx Proxy Manager, certificado válido para raíz y www,
renovación automática y HTTPS antes de probar usuarios: `.app` exige HTTPS.
El piloto usa rutas/servicio aislados y datos ficticios. La configuración del
origen, TrustedHost, cookies, CSP, redirecciones, Supabase, Stripe, emails y
documentación debe coincidir con el nuevo dominio.

Conservar `social.florentino.pro` para clientes existentes, media y callbacks
actuales durante una migración explícita. No redirigir ciegamente POST de API o
callbacks OAuth entre dominios: los cookies de navegador y redirect URIs no son
intercambiables. Registrar los nuevos callbacks en cada proveedor y probarlos
antes de usarlos; mantener los anteriores hasta agotar intentos pendientes y
resolver la transición. Un hostname antiguo no habilita nuevos OAuth anónimos
después del corte comercial. Este cambio de dominio no pasa Google a producción.

Configurar también remitente y SPF/DKIM/DMARC del correo transaccional según el
proveedor elegido; no reutilizar ni alterar registros de otros dominios.

## Alternativas y elección propuesta

| Alternativa | Resultado | Decisión |
|---|---|---|
| Ampliar el servicio de conexión existente | Conserva CLI, OAuth y ejecución en el agente; añade identidad y facturación | Recomendada para el MVP |
| Construir una plataforma que ejecute todo en la nube | Añade almacenamiento de media, trabajos y operaciones centralizadas | Posponer; aumenta alcance y coste |
| Cobrar solo por descargar el código | No controla el uso del servicio y complica la distribución abierta | No elegida |

La oferta comercial debe aportar gestión y coordinación para agentes y redes,
conexiones administradas y soporte. No se vende simplemente el acceso a una API
gratuita de un proveedor. El cliente abierto y el modo de aplicaciones propias
siguen disponibles; una suscripción controla el servicio alojado del operador.

## Decisiones propuestas, pendientes de revisión

- Un plan mensual por usuario, utilizable desde varios agentes.
- Una marca administrada y hasta cuatro conexiones: una por plataforma soportada.
- Catorce días de prueba sin tarjeta, iniciados al activar la primera conexión válida.
- Una única prueba por cuenta SocialCli; cambiar de agente o reinstalar no la reinicia.
- Importe confirmado de USD 10 al mes configurado en Stripe, sin precio incrustado en el cliente.
- Supabase Auth dedicado para acceso por código de correo; no reutilizar el proyecto
  Supabase de otras aplicaciones instalado en el servidor.
- Conservar FastAPI, las páginas existentes y SQLite para el primer despliegue de
  una sola instancia. Supabase se utiliza para identidad; los tokens sociales
  permanecen en el almacén cifrado actual del servicio.
- Nueva versión candidata `0.3.0`; mantener 0.2.6 como referencia y rollback.

## Experiencia del cliente

1. El usuario comparte la página oficial `/agents` con su agente.
2. El agente instala la versión publicada en PyPI y verifica la ayuda del CLI.
3. El agente inicia el acceso a SocialCli y entrega un enlace al usuario.
4. El usuario inicia sesión por correo y aprueba esa instalación del agente.
5. El usuario conecta una red y confirma la cuenta exacta.
6. La primera conexión activada inicia los catorce días; un intento fallido no los consume.
7. El agente opera dentro de los permisos concedidos y las aprobaciones de contenido.
8. El usuario gestiona sus conexiones, instalaciones y suscripción en `/account/`.
9. Durante la prueba ve el precio y la fecha de fin. Al terminarla puede
   contratar en Stripe Checkout; el cobro comienza tras su confirmación explícita.
10. Los clientes de pago usan Stripe Customer Portal para facturas, tarjeta y cancelación.

La autenticación y la comprobación de acceso se completan antes de iniciar el OAuth
del proveedor. Un usuario elegible para prueba no pasa por pago; un usuario cuya
prueba terminó sí debe resolver la suscripción antes de conectar nuevas redes.
El enlace OAuth actual dura 600 segundos; no se reutiliza durante un registro o
checkout que pueda agotarlo. Tras esos pasos se crea un intento OAuth nuevo.
El agente no abre enlaces de autorización ni recibe contraseñas o sesiones web.

Para agentes sin stdin, el navegador del usuario muestra marca, plataforma,
cuenta exacta, permisos y cualquier sustitución de credenciales. El usuario
selecciona y confirma allí; el CLI consume únicamente esa selección firmada y
vinculada al emparejamiento. No se requiere conocer el ID antes de descubrirlo
ni se elige la primera cuenta como sustituto del consentimiento. En el protocolo
legacy se conserva su regla actual de `--account-id` y aprobación previa.

El acceso inicial a SocialCli tiene un emparejamiento de 30 minutos, separado
del OAuth de 600 segundos. Si el agente se interrumpe, el usuario puede continuar
con un intento nuevo sin reiniciar su prueba; un resultado incierto se consulta
antes de reintentarlo. Ningún secreto de sondeo se imprime en el chat.

## Diseño UI consistente — requisito obligatorio

Toda superficie propia utiliza un sistema visual compartido basado en el logo
y las pantallas aprobadas de conexión de 0.2.6. Fuente canónica de tokens:
`socialctl/ui/assets/tokens.css`; componentes en `socialctl/ui/assets/components.css`.
Base: azul `#1464ec`, texto `#10213d`, secundario `#627087`, fondo `#f5f7fb`,
superficie blanca y tipografía del sistema con la familia de reserva existente.
El contraste y el tamaño se verifican por uso; no se reutiliza texto pequeño
de baja legibilidad solo para mantener una apariencia anterior.

Compartir logo, colores, escala tipográfica, espaciado, radios, botones,
formularios, tarjetas, mensajes y navegación. Los assets publicados y los
incluidos en el wheel derivan de la misma fuente; no mantener hojas de estilo
independientes con colores divergentes. Los correos reutilizan estos tokens
mediante plantillas compatibles con email. La UI actual del producto está en
inglés; las superficies nuevas mantienen ese idioma hasta decidir localización.

Cobertura obligatoria: landing, precios, instalación, registro y código de
correo, aprobación de agente, selección de red/cuenta, cuenta, prueba, cobro,
cancelación, desconexión, eliminación y estados de carga, vacío, éxito, error,
sesión expirada, proveedor no disponible y acceso vencido. El cliente ve mensajes
comprensibles y una acción de recuperación, no trazas ni detalles de implementación.

Checkout, Customer Portal y emails de Stripe se configuran con nombre, logo,
colores y tipografía compatible. Sus interfaces son alojadas por Stripe: se
exige identidad coherente dentro de sus opciones, no control total del HTML.
Los correos de Supabase y del servicio llevan el mismo remitente e identidad.

Aceptar cada pantalla con capturas de fixtures en anchos 360, 390 y 1440 px,
teclado, foco visible, errores asociados a campos, zoom y movimiento reducido.
Comprobar el recorrido completo, incluyendo ida y vuelta a Stripe y proveedores.
No se generan logos, imágenes o media nuevos.

## Identidad y separación entre usuarios

La identidad es el identificador verificado del usuario, nunca un correo enviado
por el cliente, el nombre local de la marca o una posición en una lista.
Cada marca alojada y conexión tiene propietario. Cada instalación del CLI recibe
su propia credencial revocable, vinculada a ese propietario y a sus conexiones.
La sesión web no se copia al CLI. El almacenamiento local seguro sigue siendo
obligatorio, con el backend cifrado explícito para entornos sin keyring.

Una instalación nueva puede volver a vincularse mediante la cuenta SocialCli,
sin repetir Google si la autorización del proveedor sigue válida. No hay
recuperación automática por coincidencia de nombre de marca o de correo.

Cada petición del agente comprueba instalación vigente, propietario, conexión
y permiso solicitado. Revocar la instalación invalida nuevas emisiones de esa
instalación sin desconectar las de otro agente. Cerrar sesión web y revocar una
instalación son acciones diferentes y se explican como tales. Las sesiones web
tienen revocación propia en el servidor; la validación criptográfica de un JWT
de Supabase por sí sola no demuestra que una sesión no haya sido revocada.

Los cambios web usan cookies Secure/HttpOnly/SameSite, comprobación de origen y
protección CSRF. Los destinos de continuación son rutas propias permitidas,
nunca URLs arbitrarias enviadas por el cliente. Email OTP y emparejamiento tienen
límites por usuario/intento además del límite global existente detrás del proxy.

## Suscripción y autorización del servicio

La base del servidor conserva propietario, fecha de inicio y fin de prueba,
cliente Stripe, suscripción y eventos procesados. Las nuevas tablas utilizan
migraciones versionadas; los secretos sociales conservan el cifrado actual.

El acceso se decide en el servidor al activar conexiones y al emitir tokens:

| Estado | Acceso a tokens del servicio |
|---|---|
| Sin primera conexión y elegible para prueba | Permitir únicamente completar la primera conexión |
| Prueba activa de SocialCli | Permitido hasta su fecha exacta de fin; un checkout incompleto no la elimina |
| Stripe `trialing` inesperado | No concede una prueba adicional; decidir solo por la prueba original de SocialCli y señalar configuración incorrecta |
| Suscripción activa y periodo pagado vigente | Permitido |
| Cancelación al final del periodo | Permitido hasta terminar el periodo pagado |
| Prueba terminada sin suscripción | Bloqueado; ofrecer checkout |
| `incomplete`, `incomplete_expired`, `past_due`, `unpaid`, `paused` o cancelada efectiva | Bloqueado; ofrecer recuperación de pago cuando corresponda |

Consultar el estado, pagar, desconectar, revocar una instalación y solicitar la
eliminación de cuenta siguen disponibles aunque no haya suscripción.

La prueba inicial se registra únicamente en SocialCli. En el MVP, Checkout se
habilita al terminarla: no hay tarjeta al alta, contratación anticipada, setup
de cobro futuro ni otra prueba Stripe. Esto evita acortar o ampliar los 14 días
y mantiene sencillo el primer lanzamiento. La UI explica la fecha de fin y
ofrece contratar entonces; el cliente no recibe un cobro sin aceptarlo.
Si posteriormente se ofrece contratación anticipada, necesitará un diseño que
preserve el fin original y respete los límites mínimos de trial de Checkout.
La precedencia de acceso es: suspensión del servicio, periodo pagado válido,
prueba original vigente y, finalmente, denegación. Los estados de Stripe de la
tabla no anulan una prueba local que aún es válida; nunca la prolongan.

Cliente Stripe único por usuario; una única suscripción comercial vigente.
Dos checkouts simultáneos se serializan y reutilizan una sesión pendiente.
La idempotencia de Stripe se combina con unicidad local; al volver de un timeout
se consulta lo creado antes de iniciar otra compra. Desconectar y volver a
conectar la misma red sustituye una conexión, no suma una quinta ni reinicia la prueba.
La sustitución crea una autorización provisional; la anterior se conserva hasta
confirmar la nueva y guardar sus credenciales. Si falla, el usuario no pierde
su conexión válida. Se vuelve a contar el límite bajo lock antes de activar.

Los webhooks firmados actualizan el estado. La URL de éxito de checkout nunca
concede acceso por sí sola. Eventos duplicados no producen efectos repetidos;
eventos fuera de orden se resuelven consultando el estado actual en Stripe.
Una reconciliación cada 5 minutos recupera webhooks perdidos. Un webhook se
confirma después de guardarlo duraderamente; si se procesa después, la tarea
pendiente también debe quedar persistida. Eventos únicos por cuenta Stripe,
modo y event ID; no ordenar por timestamp como autoridad. Ningún reloj del CLI
decide si una suscripción está activa.

La cancelación queda visible junto con la fecha de fin; un impago muestra cómo
recuperar el pago. Los reembolsos y disputas no se confunden con cancelaciones:
registrar sus eventos y permitir revisión del operador. La política comercial
de reembolso y suspensión por disputa debe quedar decidida antes de cobrar en
live, sin emitir reembolsos o cancelar suscripciones automáticamente por asumirla.

El bloqueo detiene nuevas emisiones; los tokens del proveedor ya entregados
siguen su propia caducidad. No se promete bloqueo instantáneo ni se cancelan
publicaciones que el proveedor ya haya aceptado. Tampoco se prometen cuotas
exactas por publicación: el CLI ejecuta directamente contra los proveedores.

## Distribución y web mínima

- GitHub: fuente, revisión y releases.
- PyPI: distribución de versiones verificadas mediante Trusted Publishing.
- `/agents`: instrucciones breves para agentes, requisitos y verificación.
- `/install`: instalación con `uv tool install socialcli`, alternativa con entorno
  virtual y pip, y backend de credenciales para servidores sin keyring.
- `/account/`: estado del plan, conexiones e instalaciones; acceso al portal de cobro.
- Páginas públicas de precio, privacidad, términos y soporte coherentes con el servicio.

El nombre PyPI debe confirmarse al registrarlo; un HTTP 404 no reserva el nombre.
No se anuncia un comando PyPI funcional antes de publicar y probar el paquete.
Los assets y las rutas nuevas respetan la CSP, el proxy y las rutas de media existentes.

El CLI incorpora descubrimiento de versión/capacidades del servidor y `--json`
para acceso/estado, con progreso por stderr. No declara soporte Windows por
instalar el wheel: el MVP mantiene macOS/Linux y los requisitos POSIX actuales.
Documentar Python, uv/pip, ffprobe, directorio persistente y credenciales
persistentes. Las páginas para agentes nunca invitan a ejecutar un comando que
abra su enlace OAuth o a enviar tokens a una conversación.

## Compatibilidad y conexiones duraderas

El protocolo SaaS se expone como `/api/saas/v1/`, separado del broker actual `/v1/`.
El nuevo cliente reconoce errores estructurados y la versión mínima del servidor.
Una credencial SaaS no puede utilizar la ruta antigua como atajo de facturación.
Los clientes y conexiones legacy permanecen separados mediante una política de
compatibilidad explícita: no se les atribuye una suscripción ni un propietario.
Antes de activar cobros, cerrar la creación anónima de nuevas autorizaciones
`POST /v1/authorizations`; de lo contrario, instalar 0.2.6 evitaría la suscripción.
Conservar únicamente las conexiones legacy activadas y registradas para el
piloto antes del corte, con su vencimiento existente. Dejar finalizar durante
600 segundos los intentos ya iniciados y registrar sus conexiones; después no
admitir nuevos intentos legacy anónimos. La excepción no se amplía al reinstalar
ni por cambiar versión, correo o nombre de marca. No se aplica el corte al
servicio real hasta el despliegue revisado y autorizado.
Una migración requiere demostrar la credencial antigua y la nueva sesión del dueño.

El proyecto posterior de fiabilidad distinguirá renovación pendiente, autorización
revocada, fallo temporal, almacenamiento local ausente y conexión expirada.
El cambio de renovación en lecturas debe actualizar las reglas actuales que
prohíben el refresco implícito; no se cambia esa política silenciosamente.
La expiración propia de 90 días del broker se revisará por separado de la de Google:
no prometer conexiones permanentes conservando ese límite sin modificación.

Separar el ciclo de vida de autorización del proveedor del de la credencial de
instalación. Propuesta para el SaaS: credenciales de instalación rotables antes
de sus 90 días; autorizaciones sociales se conservan durante uso consentido y
se eliminan tras 90 días de inactividad según política publicada. El piloto
validará que rotar una instalación no invalida otras ni obliga a nuevo OAuth.
No extender silenciosamente los registros legacy ni prometer superar una
revocación o caducidad impuesta por Google/Meta/TikTok.

## Operación, eliminación y soporte del MVP

Probar con una instancia SaaS aislada y una copia ficticia del vault. El rollout
del servicio compartido se hace después; no usar claves Stripe live en pruebas.
Fijar versiones API/SDK y dependencias en el lockfile; no depender de valores
por defecto que cambien entre releases. Mantener SQLite con una sola instancia,
transacciones cortas y sin llamadas a Stripe/Supabase dentro de la transacción.

Un mantenimiento propio del SaaS, cada 5 minutos, procesa eventos y conciliación,
envía recordatorios de prueba 3 días y 1 día antes de vencer y reintenta trabajo
pendiente con idempotencia. No reutiliza los timers del ejecutor privado.
SMTP requiere remitente verificado y pruebas de entrega; una caída del correo
deja una recuperación visible. No se incluyen tokens o códigos en registros.

Añadir health/readiness saneados y alertas privadas ante mantenimiento atrasado,
fallo de correo, errores de pagos y de renovación. Separar estado de suscripción
de disponibilidad de redes: una caída de un proveedor no se etiqueta como impago.
El operador puede consultar identificadores y estados auditados; no necesita
un panel administrativo completo ni acceso a tokens desde el navegador.

El cliente puede desconectar una red, revocar un agente y eliminar su cuenta.
La eliminación detiene nuevas emisiones inmediatamente y completa la limpieza
idempotente de secretos, sesiones, identidad y suscripción. Los fallos externos
quedan pendientes para mantenimiento. Se explica la retención mínima de
facturas y del historial de prueba, y qué datos locales del agente debe borrar
el usuario. No afirmar revocación del proveedor si solo se borró el acceso local.
La retención concreta y política de reembolsos se aprueban antes del lanzamiento.
Eliminar y volver a crear la identidad no reinicia automáticamente la prueba:
conservar un marcador mínimo de elegibilidad separado de los datos sociales,
con retención publicada y aprobada. No se promete impedir todo abuso por
usuarios con varias identidades; el piloto mide ese riesgo sin añadir fingerprinting.

Backup SQLite consistente más copia protegida de claves por separado; ensayo de
restauración en aislado. Un rollback posterior al corte no puede restaurar 0.2.6
abriendo otra vez autorizaciones anónimas: el candidato previo al lanzamiento
debe incluir ese cierre y reconocer el esquema migrado, o el SaaS entra en
mantenimiento. No sobrescribir datos de pagos o conexiones que hayan avanzado.

## Límites del MVP y lanzamiento

No incluye un agente propio, edición/generación de media, un editor social web,
equipos, agencias, MCP, ejecución cloud de publicaciones o planes por consumo.
Conectar y pagar no conceden aprobación para publicar. Se conservan preview
completo, aprobación exacta, verificación de cuentas y conciliación de resultados inciertos.

Se construye con Stripe test y proveedores de prueba. El piloto real utiliza
solo cuentas elegibles autorizadas. Google a producción y las habilitaciones
necesarias de Meta/TikTok son trabajo posterior y condición de la disponibilidad
general de cada integración. No se cobra prometiendo una red inaccesible al cliente.
Publicar el repositorio, pagar o pasar Google a producción no acredita por sí
solo aprobación OAuth, auditoría de YouTube o autorización de otras plataformas.

## Criterios de aceptación

- Dos usuarios ficticios no pueden acceder a las conexiones o cobros del otro.
- Un usuario utiliza dos instalaciones sin pagar dos veces ni reiniciar la prueba.
- La prueba dura 14 días desde la activación, incluso con reinstalaciones.
- Un checkout no pagado o su redirección manipulada no desbloquean acceso.
- Cancelación, impago, webhooks duplicados y recuperación quedan comprobados.
- Una pérdida de almacenamiento local se informa sin repetir publicaciones.
- Una instalación limpia fuera del checkout funciona con el paquete distribuido.
- El piloto no altera el ejecutor privado, sus datos, timers ni publicaciones.
- El registro por navegador termina sin stdin ni conocimiento previo del ID de la cuenta.
- El cliente 0.2.6 anónimo no puede crear conexiones nuevas después del corte comercial.
- El checkout se habilita tras los 14 días y evita cobros anticipados o suscripciones duplicadas.
- Revocar un agente y eliminar la cuenta bloquean nuevas emisiones y dejan limpieza verificable.
- Las capturas de todo el recorrido demuestran UI coherente en escritorio y móvil.
- Webhooks y mantenimiento resisten reinicio, caída de terceros y restauración aislada.

## Fuentes consultadas con Context7 y documentación oficial

- [Supabase Auth por correo](https://supabase.com/docs/guides/auth/auth-email-passwordless)
- [Configuración SMTP de producción](https://supabase.com/docs/guides/auth/auth-smtp)
- [Stripe Checkout](https://docs.stripe.com/payments/checkout)
- [Eventos de suscripción Stripe](https://docs.stripe.com/billing/subscriptions/webhooks)
- [Stripe Customer Portal](https://docs.stripe.com/customer-management)
- [Branding de Checkout](https://docs.stripe.com/payments/checkout/customization/appearance)
- [Stripe: pruebas de suscripciones y límite de trial en Checkout](https://docs.stripe.com/billing/testing)
- [Supabase: cierre de sesión y vigencia del JWT](https://supabase.com/docs/reference/javascript/auth-signout)
- [uv: herramientas](https://docs.astral.sh/uv/guides/tools/)
- [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
- [Google: preparación para producción](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)
- [Google: verificación de scopes](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)
- [YouTube: valor independiente y políticas](https://developers.google.com/youtube/terms/developer-policies-guide)
- [Namecheap: registros A](https://www.namecheap.com/support/knowledgebase/article.aspx/319/2237/how-can-i-set-up-an-a-address-record-for-my-domain/)
- [.app y HTTPS](https://get.app/)
