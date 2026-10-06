# SocialCli

**Publica y gestiona contenido propio en YouTube, Facebook, Instagram y TikTok desde la terminal.** SocialCli es un proyecto público para creadores y equipos que quieren revisar sus publicaciones, separar sus marcas y conservar evidencia de las acciones realizadas.

[Sitio y documentación](https://engels-florentino.github.io/SocialCli/) · [Configuración](SETUP.md) · [Seguridad](SECURITY.md) · [Limitaciones de TikTok](docs/tiktok-review.md)

## Instalar

Necesitas Python 3.11 o superior. Para comprobar vídeos necesitas también `ffprobe`, incluido en FFmpeg. SocialCli inspecciona los archivos; no genera, recorta ni reencodea contenido.

```bash
python -m pip install "git+https://github.com/engels-florentino/SocialCli.git"
socialcli --help
```

Para desarrollar desde el repositorio:

```bash
git clone https://github.com/engels-florentino/SocialCli.git
cd SocialCli
uv sync --group dev
uv run socialcli --help
uv run pytest
```

## Tu primera marca

Trabaja en una carpeta de datos que controles. Por defecto se usa la carpeta actual; puedes establecer `SOCIALCLI_ROOT` o pasar `--root` en cada comando. Los archivos de tus cuentas no pertenecen al repositorio del programa.

```bash
mkdir mis-redes
cd mis-redes
socialcli brand new MiMarca
```

Completa `MiMarca/brand.md` y `MiMarca/accounts.yml`. Después configura las aplicaciones de las plataformas y autoriza **tus propias cuentas**, siguiendo [SETUP.md](SETUP.md):

```bash
socialcli auth youtube --brand MiMarca
socialcli auth tiktok --brand MiMarca
socialcli auth status --brand MiMarca --platform tiktok --json
```

Las claves se introducen mediante el flujo local de configuración y se guardan en `MiMarca/.secrets/`. No hay claves de Histopast ni de ningún otro usuario incluidas. Esta versión usa aplicaciones de desarrollador configuradas por cada operador; no ofrece todavía una aplicación OAuth compartida de SocialCli.

## Revisar y publicar

Crea `MiMarca/posts/mi-video/post.yml` y coloca tu archivo ya producido en `MiMarca/media/`. [Ejemplo](examples/post.yml):

```yaml
slug: mi-video
campaign: clip-vertical
platforms:
  tiktok:
    body: "Una historia que vale la pena contar."
    hashtags: []
    media: ["mi-video.mp4"]
```

```bash
socialcli publish mi-video --brand MiMarca --only tiktok --dry-run
socialcli publish mi-video --brand MiMarca --only tiktok
```

El primer comando muestra el preview sin publicar. El segundo vuelve a mostrarlo y solicita confirmación. En TikTok, el modo inicial es **inbox**: el vídeo llega al buzón y tú completas la publicación en TikTok. `pending_confirmation` no significa que el vídeo esté publicado.

## Qué incluye

- Marcas separadas, credenciales locales y renovación de tokens cuando el proveedor lo permite.
- Validación de archivos, preview y confirmación antes de publicar.
- Publicación, inventario, métricas y gestión de contenido, según permisos y capacidades de cada plataforma.
- Programación con comprobaciones de aprobación y tratamiento de resultados ambiguos para reducir duplicados.
- Pruebas de adaptadores con respuestas simuladas; no publican en cuentas reales.

Cada grupo explica sus opciones con `socialcli <grupo> --help`. El paquete Python conserva el nombre interno `socialctl`; el comando público es `socialcli`. `socialctl` permanece como alias de compatibilidad.

## Estado y límites

Publicar el código no otorga acceso de producción a las APIs. Cada plataforma impone requisitos, permisos, cuotas y revisiones. SocialCli **no tiene una aprobación de producción de TikTok acreditada**. El flujo Direct Post requiere auditoría y aún necesita completar los controles de experiencia de usuario descritos en [la revisión técnica](docs/tiktok-review.md); no debe activarse como si la publicación de este repositorio los resolviera.

Los permisos para métricas y gestión son independientes de los de publicación. Una conexión válida no demuestra que todas las funciones estén autorizadas. Consulta las capacidades y verifica los resultados remotos antes de reintentar una operación incierta.

## Contribuir y soporte

Lee [CONTRIBUTING.md](CONTRIBUTING.md). Para dudas o errores, abre un [issue](https://github.com/engels-florentino/SocialCli/issues) sin tokens, claves, archivos `.env` ni registros privados. Para vulnerabilidades, usa el canal privado indicado en [SECURITY.md](SECURITY.md).

Licencia [MIT](LICENSE). SocialCli no está afiliado a las plataformas que integra.
