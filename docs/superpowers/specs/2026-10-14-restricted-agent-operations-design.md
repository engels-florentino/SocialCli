# Permisos efectivos por agente — propuesta para revisión

Estado: diseño propuesto; **no aprobado ni implementado**. La versión desplegada
solo limita qué conexiones puede obtener cada instalación. Este documento no
autoriza operaciones reales ni cambia las condiciones de lanzamiento.

## Resultado que pidió el usuario

Un cliente de SocialCli debe poder decidir qué permite a cada agente: consultar
métricas, leer comentarios, responder o publicar, sin conceder automáticamente
acciones como borrar. Debe funcionar para agentes remotos, mantener la interfaz
consistente y conservar el precio acordado de10USD/mes y14días de prueba.

Los videos y documentos grandes nunca se alojarán, descargarán, transformarán ni
reenviarán a través del servidor de SocialCli. El usuario entrega el contenido
producido. Cada escritura conserva la vista previa completa y aprobación explícita.

## El cambio de arquitectura que requiere una decisión

Hoy el servidor entrega tokens sociales al cliente. Google y Meta agrupan varias
acciones bajo un mismo permiso; un agente que tiene ese token puede llamar a la
red directamente. Ocultar comandos o añadir comprobaciones al CLI no impide eso.
El análisis de proveedores y sus fuentes está en
[límites de permisos](../../architecture/agent-permission-boundaries.md).

Alternativas consideradas:

1. **Operaciones restringidas en el servidor, recomendado.** El servidor conserva
   el token y ejecuta únicamente operaciones pequeñas y definidas. Añade consumo
   de API, textos de propuestas y responsabilidad operativa, pero permite imponer
   permisos fuera del cliente editable. No requiere alojar medios.
2. **Solo permisos OAuth más estrechos.** Útil donde el proveedor realmente los
   distingue, pero insuficiente para responder sin poder borrar en varias APIs.
   Debe verificarse el alcance efectivamente concedido, incluso tras renovaciones.
3. **Solo permisos por conexión, como ahora.** Mantiene la arquitectura más simple,
   pero no satisface la separación de acciones solicitada en el punto7 de la crítica.

La recomendación combina1con2donde esté comprobado. No introduce un proxy HTTP
genérico: el agente no elige una URL, un método ni encabezados arbitrarios para
que SocialCli use su token.

## Dos formas explícitas de vincular una instalación

**Restringida:** el dueño elige redes y acciones. Nunca recibe tokens de proveedor,
ni siquiera mediante rutas antiguas, renovación, recuperación o adjuntar una
conexión existente. El servidor verifica instalación, propietario, cuenta,
suscripción y permiso en cada operación. Un permiso desconocido o corrupto deniega.

**Acceso directo:** conserva la publicación cliente→plataforma y la compatibilidad
actual. La pantalla explica que el token puede autorizar otras acciones y cuentas
vinculadas. No muestra controles que aparenten prohibir acciones que ese token
todavía permite. Las instalaciones existentes conservan este modo explícitamente;
no se modifican permisos ya aprobados sin intervención del dueño.

No se convierte una instalación que recibió tokens amplios en una instalación
supuestamente restringida. Se crea una instalación nueva. Revocar la anterior
bloquea futuras entregas; tokens ya exportados pueden seguir vigentes. El dueño
necesita conocer esa diferencia y, si corresponde, revocar el consentimiento en
la plataforma, con sus posibles efectos sobre otras instalaciones.

## Matriz de acciones y límites de publicación

| Acción | Ejecución restringida prevista | Condición de aceptación |
| --- | --- | --- |
| Leer métricas | Operaciones tipadas de consulta, sin token exportado | Cuenta exacta, permisos de proveedor y cobertura verificados; conservar resultados parciales |
| Leer comentarios | Bandeja sobre contenido de la cuenta autorizada | Comprobar pertenencia del contenido/comentario; paginación y límites explícitos |
| Responder comentarios | Propuesta inmutable, aprobación del dueño y envío desde el servicio | YouTube/Facebook/Instagram según capacidades actuales; no prometer comentarios de TikTok |
| Borrar, ocultar, editar o moderar | Denegado en el modo restringido inicial | Una respuesta permitida nunca abre estas operaciones |
| Publicar un archivo local | Transferencia directa a la red | No exportar un token amplio para eludir restricciones; bloquear si no existe un mecanismo compatible demostrado |
| Publicar desde una URL del cliente | Posible operación tipada si la red descarga el contenido por sí misma | Verificar API, titularidad, formato, privacidad y vigencia de URL; SocialCli no descarga ni conserva el archivo |

No se declara completado el punto7 con lecturas/respuestas solamente. La petición
«publicar sí, borrar no» requiere rutas verificadas por plataforma. YouTube puede
ofrecer distinciones mediante permisos efectivos de subida; una autorización
existente más amplia no se convierte en estrecha por pedir menos scopes. Meta y
TikTok requieren demostrar un flujo de publicación compatible con las restricciones
y con el origen de medios del cliente. Las rutas aún no verificadas se anuncian
como no disponibles, sin cambiar a acceso directo automáticamente.

## Flujo del usuario y del agente

1. El dueño abre la vinculación y escoge modo, redes y acciones disponibles. La
   pantalla usa los mismos componentes, colores y tipografía de SocialCli.
2. El CLI consulta capacidades del servidor y de la instalación. Los comandos
   normales de métricas y bandeja seleccionan el transporte autorizado; nunca
   intentan obtener un token como alternativa a un rechazo del servicio.
3. Para responder, el agente prepara una propuesta con cuenta, publicación,
   comentario original, autor, texto exacto y acción. El servidor verifica los
   vínculos, guarda el contenido pequeño cifrado y calcula la huella inmutable.
4. El dueño ve y aprueba esa propuesta exacta en su sesión web. La credencial del
   agente no puede fabricar esa aprobación. Los lotes muestran todos sus elementos
   y conservan sus resultados individuales si hay éxito parcial.
5. El agente solicita ejecutar la propuesta aprobada. El servidor revalida permiso,
   cuenta, vigencia, suscripción y referencia original antes del envío. Un cambio
   de texto, destino o cuenta necesita otra propuesta y otra aprobación.
6. Se guarda el resultado o la incertidumbre y se consulta el estado. Un timeout
   posterior al envío no autoriza repetirlo con un identificador nuevo.

La aprobación por navegador es un cambio visible respecto a aprobar únicamente
en el chat: permite al servidor comprobar que el dueño autorizó el contenido.
Este coste de interacción forma parte de la decisión sobre el diseño.

## Componentes y contratos

- `saas/permissions.py`: política versionada con modo y acciones por plataforma;
  descripciones humanas/JSON de restricciones reales. Nueva migración conserva
  los registros directos existentes y bloquea versiones de servidor incompatibles.
- `saas/connections.py`: separar obtención interna de token de su exportación.
  Ambas rutas conservan las exclusiones, leases y estados de renovación actuales.
  La exportación comprueba acceso directo antes y después de cualquier renovación.
- `saas/agent_operations.py`: rutas tipadas bajo
  `/api/saas/v1/connections/{connection_id}/operations/`: POST `metrics/read`,
  POST `comments/read`, POST `replies/prepare`, POST `{operation_id}/execute` y
  GET `{operation_id}`. Autenticación mediante la capacidad existente, sin aceptar
  user_id/account_id como autoridad del cuerpo. Aprobación exclusiva mediante
  POST `/account/operations/{operation_id}/approve`, con sesión del dueño y CSRF.
- `saas/operation_providers/`: adaptadores de operaciones específicas, host y
  campos fijos, sin URLs/headers de transporte elegidos por el agente. Reutilizar
  verificadores de pertenencia actuales sin cargar workspaces privados en el servidor.
- `saas/operation_proposals.py`: propuestas, aprobación web con CSRF y diario de
  ejecución cifrado. Huella ligada a propietario, instalación, cuenta, operación,
  contenido exacto y revisión de política.
- `connections/managed_client.py`: transporte limitado para esas operaciones;
  salida JSON coherente, cobertura parcial y acciones de recuperación existentes.
- Integración en métricas, bandeja y propuestas de comunidad: mismos flujos
  canónicos del agente, con descubrimiento de capacidades; errores claros para
  comandos que todavía no tienen implementación restringida.

Las lecturas aceptan selectores de contenido y cursores acotados, nunca un destino
de transporte. Una respuesta preparada recibe un operation_id y una huella;
execute acepta esa referencia y no acepta otro texto o destino. No se habilita
una operación solo por aparecer en una
lista de permisos. El descubrimiento refleja la intersección entre implementación,
configuración del proveedor, selección del dueño y cuenta realmente conectada.

## Límites, conservación y errores

No hay campos binarios, multipart, descarga de medios ni URLs de transporte libres.
Solicitudes de metadatos limitadas a64KiB y respuestas a1MiB; máximo50elementos por
página. La ruta aplica un presupuesto de30s y devuelve la cobertura observada,
sin convertir un resultado incompleto en una bandeja vacía definitiva. La app
mantiene el límite externo de1MiB existente, además del límite específico por ruta.

Rate limits iniciales por instalación:60solicitudes de lectura/minuto y10preparaciones
o solicitudes de ejecución/minuto. Topes por propietario compartidos entre
instalaciones:240lecturas y20preparaciones/ejecuciones por minuto. Los límites no
modifican cuotas, cadencia ni autorización editorial y se muestran al devolver429.

Propuestas sin ejecutar: caducan a las24horas; la aprobación no extiende esa fecha.
Recibos cerrados: conservar30días y después eliminar su contenido. Operaciones
inciertas: conservar la evidencia cifrada hasta conciliación o eliminación de la
cuenta; no caducar una incertidumbre para permitir el reenvío. A partir de100
operaciones inciertas del mismo dueño se bloquean nuevas escrituras hasta resolver
pendientes. La interfaz explica ese estado y la cuenta permite consultar/eliminar
sus datos conforme al flujo vigente. Estos plazos propuestos deben figurar en la
política visible de datos antes de habilitar el modo; no se aplican silenciosamente
a los clientes actuales. La eliminación de cuenta revoca acceso y borra también
las propuestas/recibos; no deshace acciones ya realizadas por las redes.

No se hacen peticiones externas dentro de una transacción SQLite. La toma de una
operación para despacho es una transición durable y autenticada. Revocación impide
nuevos despachos; una llamada ya iniciada puede terminar. La UI y el estado de la
operación deben distinguir ese caso de una operación todavía no enviada.

Los errores incluyen código, acción y carácter reintentable. Las lecturas pueden
reintentarse dentro del presupuesto; escrituras inciertas exigen conciliación.
Se conservan únicamente campos necesarios y se excluyen credenciales, encabezados
y URLs sensibles de respuestas, logs, propuestas visibles y diagnósticos.

## Entrega y pruebas obligatorias

Implementar por etapas revisables sin publicar una interfaz restringida incompleta:

1. Política versionada, aislamiento, bloqueo de exportación y renovación interna.
2. Operaciones de lectura con cuenta exacta y clientes integrados.
3. Propuestas de respuesta, aprobación del dueño, diario y conciliación.
4. Publicación restringida solo por rutas proveedor/origen demostradas; mantener
   explícitos los huecos que impidan cumplir «publicar sin borrar».
5. Guía empaquetada, misma UI, migración/recuperación compatible y pilotos reales.

Pruebas contra endpoints, no solo contra comandos cooperativos: una instalación
restringida no obtiene tokens, no llama operaciones no concedidas, no actúa sobre
otra cuenta ni falsifica aprobación; tampoco evade límites por rutas legacy,
rotación o una renovación concurrente. Probar cambio de permiso durante renovación,
revocación antes del despacho, publicación/comentario sustituido, cuerpo manipulado,
reinicio después de enviar, respuesta perdida y conciliación sin duplicados.

La migración necesita el mismo procedimiento de detener todos los escritores y
conservar una recuperación compatible ya aplicado a schema7. Las pruebas usan
cuentas ficticias; los pilotos reales requieren las aprobaciones correspondientes.
No se considera terminada esta mejora con una suite verde si la ruta real de una
plataforma o la propiedad de los permisos todavía no está demostrada.

## Decisión pendiente

Aprobar el modo restringido con operaciones pequeñas ejecutadas por el servidor
y aprobación web de cada propuesta, manteniendo las subidas fuera del servidor.
Tras esa revisión se concreta el plan de implementación nativa. Mantener solo
permisos por red sería una reducción explícita del punto7, no su cumplimiento.
