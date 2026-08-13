# Elyndra 0.7.3.dev0 — Cierre de verificación PHP controlada

Elyndra 0.7.3 completa la primera toolchain de programación con un flujo PHP
repetible, auditable y extensible a futuros lenguajes.

## Flujo determinista

`php.verify_project` ejecuta, según el perfil y la aprobación concedida:

1. inspección local del proyecto;
2. validación segura de Composer;
3. `php -l` sobre los archivos PHP permitidos;
4. análisis con PHPStan local o global autorizado;
5. pruebas con PHPUnit local o global autorizado.

Cada etapa queda registrada con estado, duración, exit code, timeout y resumen
acotado. Las herramientas ausentes pueden marcar la ejecución como parcial o
como fallo cuando el perfil exige todas las herramientas.

## Inspección y sintaxis de proyecto

```bash
./scripts/elyndra-dev php inspect /ruta/proyecto --approve --allow-root-once
./scripts/elyndra-dev php syntax-project /ruta/proyecto --approve --allow-root-once
./scripts/elyndra-dev php verify /ruta/proyecto --approve --allow-root-once
```

La inspección lee únicamente metadatos permitidos. Los scripts de Composer se
muestran por nombre, pero sus comandos no se copian a respuestas ni auditoría.
El escaneo sintáctico excluye por defecto `.git`, `vendor` y `node_modules`, no
sigue archivos enlazados fuera del proyecto y limita la cantidad de PHP.

## Historial comparable

```bash
./scripts/elyndra-dev php history /ruta/proyecto
./scripts/elyndra-dev php report ID
./scripts/elyndra-dev php compare ID_ANTERIOR ID_NUEVO
```

Las comparaciones solo aceptan ejecuciones del mismo proyecto y toolchain.
El centro web `/control` muestra las verificaciones PHP recientes.

## Perfiles PHP

Los perfiles pueden controlar etapas, fail-fast, obligación de herramientas,
máximo de archivos y exclusiones internas al proyecto. Un perfil nunca concede
autorización y no admite flags o comandos arbitrarios.

## Seguridad

- procesos mediante argv, sin shell;
- sin instalación ni descarga automática;
- raíces persistentes o autorización puntual;
- binarios locales `vendor/bin` prioritarios;
- rutas normalizadas y contenidas en el proyecto;
- timeout y salida limitada;
- auditoría sin contenido completo ni secretos.
