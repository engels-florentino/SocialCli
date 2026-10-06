# Programación y evidencia

Consulta `socialcli native-schedule --help` para el flujo completo. Preparar un
manifest con `--dry-run` muestra la propuesta; persistir o aprobar requiere su
digest. Los cambios de contenido, cuenta o archivos invalidan la aprobación.

Los estados distinguen preparación, entrega nativa, handoff y reconciliación.
Una cola local no demuestra que un post aparezca en el calendario del proveedor.
Verifica el identificador y el resultado remoto antes de reintentar una entrega
incierta. No uses un marcador manual como prueba de publicación sin evidencia.

La compatibilidad con `schedule`, `schedule-status` y `run-due` conserva colas
anteriores. La configuración de un ejecutor remoto es propia de cada operador;
no se distribuye un servidor o calendario de otro creador.
