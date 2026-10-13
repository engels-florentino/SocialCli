# Revisión del plan SocialCli SaaS

Fecha: 13 de octubre de 2026. Revisión del diseño y plan; no es una auditoría de
software implementado ni acredita acceso público de los proveedores.

## Hallazgos incorporados

| Hallazgo | Importancia | Corrección en los documentos |
|---|---|---|
| Un cliente 0.2.6 puede crear autorizaciones anónimas y evitar el cobro | Bloqueante | Corte de nuevos OAuth legacy, lista acotada de conexiones anteriores y drenaje de 600 segundos; fase 5 |
| Un agente sin stdin no conoce el ID antes de descubrir las cuentas | Bloqueante | Selección y consentimiento exactos en navegador SaaS, ligados a instalación/marca/plataforma; fase 2 |
| Rollback a 0.2.6 podría reabrir el acceso anónimo | Bloqueante | Mantener cierre legacy en candidato de rollback o entrar en mantenimiento; fase 7 |
| Faltaba el requisito explícito de UI consistente | Importante | Sistema visual único, fase 0, plantillas de correo y branding de Stripe |
| Los JWT de Supabase pueden seguir válidos después de signout | Importante | Revocación propia de sesión/instalación, sin confiar solo en firma JWT; fases 1 y 2 |
| Registro o pago pueden agotar el enlace OAuth | Importante | Emparejamiento SocialCli de 30 minutos y OAuth nuevo de 600 segundos; fase 2 |
| Dos checkouts pueden crear dos suscripciones | Importante | Unicidad local, sesión pendiente, idempotencia y consulta tras timeout; fase 4 |
| Trial y estados de pago podían contradecirse | Importante | Precedencia explícita y checkout solo al terminar la prueba en el MVP; fases 3–5 |
| Sustituir una conexión podía dejar al cliente sin acceso si falla | Importante | Autorización provisional, conservación de la anterior y activación atómica; fase 2 |
| Webhook recibido no equivale a trabajo completado | Importante | Guardado durable, tarea persistida y reconciliación cada 5 minutos; fases 4 y 7 |
| Faltaba limpieza efectiva al eliminar una cuenta o revocar un agente | Importante | Bloqueo inmediato de nuevas emisiones y limpieza idempotente recuperable; fases 5 y 7 |
| Borrar y recrear la cuenta podía reiniciar la prueba | Importante | Marcador mínimo de elegibilidad con retención publicada, separado de tokens borrados |
| Reembolso/disputa no equivale a cancelación de Stripe | Importante | Eventos y revisión explícita; política comercial pendiente antes de live |
| Faltaban recordatorios, entrega SMTP y alertas | Importante | Recordatorios a 3/1 días, remitente verificado y mantenimiento separado del ejecutor privado |
| TTL del broker y pérdida de entorno pueden forzar otro OAuth | Importante | Separar autorización/instalación, rotación e inactividad; piloto con dos agentes |
| Instalar un wheel no demuestra soporte en cualquier agente o sistema | Importante | Pruebas reales Muse/Grok pendientes, macOS/Linux, ffprobe/PATH y almacenamiento persistente |

## Control del alcance

Dominio confirmado: `socialcli.app`. Se añadió preparación DNS/HTTPS en Namecheap
y Nginx Proxy Manager y una migración que conserva el dominio, media y callbacks
anteriores. Cambiar DNS no se presenta como despliegue del SaaS ni como aprobación OAuth.

Se mantiene el MVP pequeño: ejecución en el agente, un plan mensual y web mínima.
La contratación anticipada durante la prueba queda fuera: Checkout se abre al
terminar los 14 días. No se añade un segundo motor de trials ni un flujo setup
de pagos futuros. Equipos, MCP y ejecución cloud siguen fuera del MVP.

El requisito de UI abarca también correos, estados vacíos/de carga/errores,
cancelación y vuelta desde Stripe. Las pantallas alojadas por terceros se
personalizan dentro de sus capacidades, sin prometer control de su HTML.

## Evidencia revisada

- Código `/v1/authorizations`: actualmente permite iniciar OAuth sin identidad SaaS.
- CLI de conexión: actualmente exige ID autorizado en ejecución sin stdin.
- CSS público y de conexión: actualmente tienen colores/componentes separados.
- Protocolo actual: OAuth de 600 segundos y conexión de 90 días.
- Documentación Stripe vía Context7: webhooks fuera de orden e idempotencia;
  branding de Checkout/portal y límites de trial.
- Documentación Supabase vía Context7: signout no invalida de inmediato un JWT ya emitido.

## Pendiente antes de ejecución o lanzamiento

- Precio confirmado por el usuario: **USD 10 al mes**. El diseño sigue sujeto
  a revisión en cuanto a prueba sin tarjeta, una marca/cuatro conexiones y Supabase Auth.
- Cada fase requiere su plan de implementación específico antes de ejecutarse.
- Antes de live: retención, reembolsos/disputas, impuestos, disponibilidad externa
  por red, aprobación del despliegue y Stripe live separado de test.
- Google a producción permanece posterior, por instrucción del usuario.

Los hallazgos están resueltos en el plan, no en código. Queda la incertidumbre
propia del piloto y de las aprobaciones externas; no se promete cobertura absoluta.
