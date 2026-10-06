# Archivos y alojamiento

SocialCli recibe archivos ya producidos. Necesita `ffprobe` para inspeccionar dimensiones, duración y formato; no genera ni reencodea vídeos.

Guarda la media bajo `<Marca>/media/` y referencia una ruta relativa en `post.yml`. Las rutas no pueden escapar de la carpeta de media. Para Instagram, el proveedor debe poder descargar cada archivo mediante HTTPS desde un alojamiento que controles. La base pública se configura en `instagram.media_url_base`.

Los comandos `socialcli media --help` permiten preparar, transferir y verificar bundles en despliegues remotos configurados por el operador. Transferir un archivo no aprueba ni publica un post. Una comprobación de bytes o un HTTP 200 tampoco demuestra que exista una publicación remota.

No incluyas archivos de clientes, derechos de terceros, tokens de URLs ni configuración de tu servidor en el repositorio público del programa.
