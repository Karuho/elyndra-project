# Elyndra 0.7.4.dev0

Elyndra 0.7.4 inicia la toolchain web controlada y la arquitectura de paquetes
opcionales de Alejandría.

## Toolchain web

Skills nuevas:

- `web.project_inspect`
- `html.validate`
- `css.validate`
- `javascript.syntax_validate`
- `typescript.check`
- `web.verify_project`

HTML y CSS utilizan validadores estructurales internos. JavaScript usa
`node --check`. TypeScript prioriza `node_modules/.bin/tsc` y ejecuta
`tsc --noEmit --pretty false`. Ninguna skill usa shell, ejecuta scripts npm,
instala dependencias o descarga herramientas.

Los perfiles web permiten controlar etapas, fail-fast, herramientas obligatorias,
límites de archivos, exclusiones, timeout y salida. Las verificaciones se guardan
en el historial genérico junto con su plan y resumen.

## Enrutamiento determinista

Órdenes incompletas como `php verify` o `web verify` ya no llegan al modelo
lingüístico. Elyndra solicita la ruta del proyecto mediante el router
determinista, sin cargar Qwen.

## Paquetes opcionales de Alejandría

Un paquete local usa una carpeta con `elyndra-package.json` y fuentes declaradas
por SHA-256. Elyndra valida rutas, tamaños, identificadores, licencia y hashes
antes de crear una biblioteca e importar sus fuentes.

La instalación es local, sin red ni ejecución de código. Las fuentes se importan
como no revisadas y requieren aprobación posterior igual que cualquier otra
fuente de Alejandría.

Los paquetes pueden listarse, habilitarse, deshabilitarse y eliminarse desde CLI.
El centro de control muestra los paquetes instalados y las verificaciones web
recientes.
