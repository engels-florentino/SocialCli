# Contribuir

```bash
git clone https://github.com/engels-florentino/SocialCli.git
cd SocialCli
uv sync --group dev
uv run pytest
uv run socialcli --help
```

Abre un issue con el comportamiento esperado, el resultado observado y pasos reproducibles sin credenciales. Usa marcas ficticias y respuestas HTTP simuladas en pruebas. No llames a cuentas reales desde CI ni incluyas datos de operaciones particulares.

Los cambios de publicación deben conservar el preview, la aprobación, el aislamiento de marcas y el tratamiento de resultados ambiguos. No marques como publicado un envío pendiente de confirmación. Las funciones de proveedores deben describir permisos y límites reales, sin dar por aprobada una aplicación.

El módulo Python sigue siendo `socialctl` para conservar compatibilidad; la distribución y el comando principal son `socialcli`. Envía PRs pequeñas y explica qué cambió y cómo lo verificaste. Las contribuciones se distribuyen bajo la licencia MIT del proyecto.
