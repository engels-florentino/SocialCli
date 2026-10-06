# Seguridad

No publiques `.env`, `.secrets/`, tokens OAuth, client secrets, credenciales SSH ni datos privados de tus cuentas. `.gitignore` protege esos nombres en este repositorio; no borra secretos que ya estuvieran en otro historial Git.

Cada marca guarda credenciales localmente con permisos restringidos (directorio 0700, archivos 0600). Son archivos de texto, no una bóveda cifrada: protege el usuario del sistema, el disco y las copias de seguridad. Las llamadas a APIs transmiten los datos necesarios a sus proveedores.

El código público no contiene credenciales de una aplicación compartida. Cada operador configura su propia aplicación. Una futura autorización común de SocialCli necesitará un diseño que mantenga el secreto en un servicio controlado, fuera del CLI distribuido.

Para informar de una vulnerabilidad, usa **Security → Report a vulnerability** en el repositorio. Si ese canal no aparece habilitado, contacta al mantenedor mediante [su perfil](https://github.com/engels-florentino) antes de publicar detalles explotables. No abras un issue público con secretos o pruebas que expongan cuentas.

Si sospechas una filtración, revoca y rota la credencial en su proveedor; borrar el archivo o el commit no la invalida.
