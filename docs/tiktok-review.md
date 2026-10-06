# TikTok: preparación de revisión

SocialCli es ahora un proyecto público que otros creadores pueden instalar para gestionar sus cuentas. Esta intención debe reflejarse fielmente en una solicitud nueva. La solicitud histórica de Histopast describía una herramienta privada de un solo operador; publicar este repositorio no modifica esa solicitud ni demuestra cumplimiento de todos los controles.

## Arquitectura de esta distribución

Cada operador usa su propia aplicación de proveedor y conserva sus secretos localmente. No se incluye el client secret de Histopast. Esto permite distribuir el programa sin compartir sus credenciales, pero no proporciona una autorización común de SocialCli para todos los usuarios. Para ese producto hace falta resolver un servicio de autorización que conserve el secreto fuera del CLI y actualizar arquitectura, políticas y demo antes de presentarlo como existente.

## Estado que debe declararse

- El código es público y admite marcas configuradas por cada operador.
- Inbox es el modo inicial; el usuario completa el post en TikTok.
- La aprobación de producción y la auditoría de Direct Post no están acreditadas.
- Hay una grabación histórica adjunta a la solicitud; no se da por verificado que muestre el producto público actualizado.
- El sitio público documenta el producto y sus límites; la evaluación final corresponde a TikTok.

## Antes de solicitar Direct Post

El adaptador existente consulta creator info y aplica comprobaciones de duración y auditoría, pero fija la privacidad pública. No ofrece todavía todos los controles de UX que exige TikTok: selección explícita de privacidad sin valor por defecto, controles de comentarios/Duet/Stitch compatibles con creator info, identificación visible del creador, divulgación comercial y declaraciones correspondientes. El preview actual del terminal no acredita por sí solo cumplimiento de la interfaz exigida.

No activar `auditada: true` sin aprobación real ni enviar una demo que oculte estas limitaciones. Completar y comprobar la experiencia necesaria antes de solicitar ese alcance. Para inbox, revisar igualmente los requisitos generales, permisos y arquitectura; cambiar de endpoint no exonera la revisión general.

## Preparar la solicitud

1. Registrar el nombre e identidad de SocialCli de forma consistente en el portal y el sitio.
2. Describir el producto público y el modelo de autorización realmente implementado, sin conservar frases de uso exclusivo ni anunciar servicios inexistentes.
3. Pedir solo los productos/scopes necesarios. Retirar Data Portability si no hay un caso implementado.
4. Completar descripción y explicación: los campos históricos estaban truncados.
5. Aportar una grabación actual, creada por el usuario, del flujo completo en sandbox, mostrando cada producto y permiso solicitado.
6. Revisar políticas y URLs activas, y enviar únicamente cuando el producto y las evidencias estén listos.

Fuentes oficiales: [App Review Guidelines](https://developers.tiktok.com/docs/en/app-review-guidelines) y [Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines), consultadas el 6 de octubre de 2026. Estas páginas exigen una aplicación destinada a usuarios externos, una web completa y evidencia del flujo. Direct Post tiene controles adicionales y exige mantener el secreto confidencial.
