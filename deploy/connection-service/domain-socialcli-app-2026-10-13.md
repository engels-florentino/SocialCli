# socialcli.app — preparación del dominio

Verificado el 13 de octubre de 2026. Cambio limitado a un host nuevo en el
Nginx Proxy Manager del servidor, autorizado por el usuario durante la sesión.

- NPM instalado: 2.15.1.
- Proxy Host ID: 39, dominio `socialcli.app`, habilitado y online.
- Destino: `http://social-static:80`, web pública actual de SocialCli.
- Certificado Let's Encrypt ID: 43, administrado por NPM para renovación.
- Certificado válido para `socialcli.app`, caduca el 11-ene-2027 a las 10:58:31 UTC.
- Force SSL, HTTP/2 y HSTS habilitados; no se habilitó HSTS para subdominios.
- Backup SQLite consistente previo al cambio, dentro del contenedor NPM:
  `/data/backups/socialcli-domain-2026-10-13T11-56-01-174Z/database.sqlite`.
- Las huellas de los archivos de hosts anteriores no cambiaron.

## Verificación

- `http://socialcli.app/`: 301 a `https://socialcli.app/`.
- `https://socialcli.app/`: 200; TLS y hostname verificados sin desactivar validación.
- `/style.css`: 200, `text/css`; `/assets/socialcli-logo.png`: 200, `image/png`.
- Inicio de `socialcli.app` y `social.florentino.pro`: mismo SHA-256
  `885393fa0778af124cc8580b9e6daa035640650d7a5241aa1898bc00fb3f1afb`.
- `GET https://social.florentino.pro/healthz`: 200; los cuatro conectores siguen configurados.
- `nginx -t`: configuración válida.

## Pendientes y límites

`www.socialcli.app` devuelve NXDOMAIN en el DNS autoritativo de Namecheap.
Falta crear CNAME `www` a `socialcli.app`; después añadir certificado y
redirección a la raíz. No se intentó emitir un certificado para un nombre sin DNS.

Este paso sirve la web actual; no implementa registro, facturación, pruebas
gratuitas ni el SaaS. Tampoco cambia el origen del broker, las credenciales,
callbacks OAuth, la configuración de Google o el ejecutor privado.

Para revertir solo este cambio, deshabilitar el Proxy Host 39 mediante NPM.
No restaurar toda la base antigua sobre cambios posteriores de otros hosts.
