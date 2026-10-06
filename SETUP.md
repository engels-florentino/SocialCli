# Configurar SocialCli con tus cuentas

SocialCli es una aplicación de escritorio por línea de comandos. Esta versión no distribuye credenciales compartidas del desarrollador: cada operador configura sus aplicaciones de proveedor, con los permisos y revisiones que correspondan. No copies credenciales de otra persona ni las publiques en GitHub.

## Carpeta de trabajo

```bash
mkdir mis-redes
cd mis-redes
socialcli brand new MiMarca
```

Completa `MiMarca/accounts.yml` con los identificadores de **tus** cuentas. Los tokens y secretos van en `MiMarca/.secrets/`, creada con permisos restringidos. Para reutilizar esta carpeta desde otro lugar, establece `SOCIALCLI_ROOT` con su ruta absoluta o usa `--root RUTA`.

## YouTube

1. Crea tu proyecto en [Google Cloud](https://console.cloud.google.com/).
2. Activa YouTube Data API v3 y, si usarás métricas, YouTube Analytics API.
3. Configura la pantalla de consentimiento y añade tu cuenta a los usuarios de prueba si tu proyecto está en testing.
4. Crea un cliente OAuth de escritorio.
5. Ejecuta `socialcli auth youtube --brand MiMarca` y proporciona tu client ID y secreto cuando se soliciten.

El callback local es `http://localhost:8723/callback`. Configura el canal en `accounts.yml`. Para gestión adicional de contenido consulta `socialcli auth youtube --help`: los permisos adicionales son opt-in. La renovación automática necesita un refresh token válido; una revocación o cambio de permisos puede requerir autorizar de nuevo.

## Facebook e Instagram

Configura tu aplicación en [Meta for Developers](https://developers.facebook.com/), con los productos y permisos requeridos para tus cuentas. Facebook publica en Páginas. Instagram requiere una cuenta profesional vinculada a una Página para el flujo implementado. Las pruebas y el uso con usuarios externos pueden requerir revisión y acceso avanzado.

```bash
socialcli auth facebook --brand MiMarca
socialcli auth instagram --brand MiMarca
```

Configura `facebook.page_id` e `instagram.ig_user_id`. Para Instagram, los archivos de publicación deben tener URLs HTTPS accesibles al proveedor; configura tu alojamiento y `instagram.media_url_base`. Lee [MEDIA-INGESTION.md](MEDIA-INGESTION.md). La expiración de un token de Meta no equivale al flujo de refresh de YouTube o TikTok; sigue el diagnóstico del comando y las políticas del proveedor.

## TikTok

1. Crea tu aplicación de escritorio en [TikTok for Developers](https://developers.tiktok.com/).
2. Configura Login Kit, Content Posting API y el redirect `http://localhost:8723/callback`.
3. Para el flujo inbox solicita `user.info.basic` y `video.upload`. Habilita únicamente los permisos adicionales que realmente utilizarás.
4. Si estás en sandbox, añade tu cuenta como target user.
5. Ejecuta `socialcli auth tiktok --brand MiMarca` y proporciona las credenciales de **tu aplicación** localmente.

El identificador `open_id` se guarda a partir de la autorización. La plantilla mantiene `mode: inbox` y `auditada: false`. El vídeo se sube al buzón; debes completar la publicación desde TikTok. No publiques dos veces el mismo archivo para probar el flujo.

La existencia de una autorización o una subida sandbox correcta no acredita aprobación de producción. Revisa [docs/tiktok-review.md](docs/tiktok-review.md) antes de preparar una solicitud. Nunca distribuyas el secreto de una aplicación compartida junto con el CLI.

## Verificar

```bash
socialcli auth status --brand MiMarca --platform tiktok --json
socialcli doctor --brand MiMarca --json
socialcli capabilities --brand MiMarca --json
socialcli publish mi-video --brand MiMarca --only tiktok --dry-run
```

Usa `socialcli COMANDO --help` para ver las opciones efectivas. No publiques salidas privadas de diagnóstico completas en issues. Si eliminas una marca, guarda antes lo necesario y revoca por separado los permisos desde cada plataforma; borrar un fichero local no revoca permisos remotos.
