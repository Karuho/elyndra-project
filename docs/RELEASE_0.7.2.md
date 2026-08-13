# Elyndra 0.7.2.dev0

Elyndra 0.7.2 agrega un centro de control local para proyectos confiables,
perfiles PHP y auditoría de ejecuciones.

## Principios

- Un perfil PHP no concede autorización.
- Solo se puede guardar para una raíz configurada o un proyecto confiable.
- Los parámetros explícitos de una ejecución prevalecen sobre el perfil.
- Las rutas de configuración se mantienen dentro del proyecto.
- No se descargan ni instalan herramientas.
- No se habilita un shell ni comandos arbitrarios.

## Perfil PHP

Puede conservar valores seguros para:

- configuración y nivel de PHPStan;
- configuración y suite de PHPUnit;
- modo estricto de Composer validate;
- timeout;
- límite de salida.

## Interfaz web

La ruta local `/control` permite:

- registrar y revocar proyectos confiables;
- crear, editar y eliminar perfiles PHP;
- consultar ejecuciones y autorizaciones auditadas;
- filtrar auditoría sin exponer secretos.
