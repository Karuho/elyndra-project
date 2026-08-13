# Python moderno — Base práctica y verificable

## Propósito

Este paquete introduce una base local para comprender y revisar proyectos Python sin conceder permisos de ejecución. Alejandría explica conceptos y procedencia; las skills controladas inspeccionan, compilan, analizan o ejecutan pruebas únicamente tras una aprobación explícita.

## Estructura de un proyecto

Un proyecto moderno suele declarar metadatos en `pyproject.toml`. La tabla `[project]` puede incluir nombre, versión, versión mínima de Python, dependencias y puntos de entrada. La tabla `[build-system]` declara el backend de construcción y sus requisitos. Leer estas tablas no instala ni construye el proyecto.

El layout `src/` ayuda a evitar importaciones accidentales desde la raíz del repositorio. Los tests suelen ubicarse en `tests/`. Carpetas como `.venv`, `venv`, `build`, `dist` y caches de herramientas no deberían formar parte del análisis de código fuente normal.

## Sintaxis y compilación

Compilar un archivo Python comprueba que el código pueda convertirse en un objeto de código válido. Esto detecta errores sintácticos, indentación inválida y algunos problemas de codificación. No demuestra que la lógica sea correcta y no sustituye las pruebas.

Una validación segura no debe importar módulos del proyecto. Importar puede ejecutar código de nivel superior. Elyndra usa una compilación sin importación y no escribe bytecode dentro del proyecto.

## Ruff

Ruff realiza lint y otras comprobaciones estáticas. Puede detectar imports sin usar, errores de estilo y patrones definidos por sus reglas. Ejecutar `ruff check` sin `--fix` analiza el proyecto sin modificar archivos. La configuración debe permanecer dentro del proyecto autorizado.

Ruff no demuestra el comportamiento en tiempo de ejecución. Un resultado limpio no reemplaza mypy ni Pytest.

## Tipos con mypy

mypy analiza anotaciones de tipo y contratos estáticos. Puede detectar incompatibilidades de tipos, retornos incorrectos y usos inseguros de valores opcionales cuando el proyecto está anotado adecuadamente.

mypy puede cargar plugins declarados por el proyecto. Por ese motivo requiere aprobación explícita. Su caché debe dirigirse a una ubicación temporal cuando se busca evitar modificaciones persistentes.

## Pruebas con Pytest

Pytest ejecuta código de pruebas y normalmente importa partes del proyecto. Es una operación de mayor riesgo que leer metadatos o compilar sintaxis. Debe ejecutarse únicamente dentro de un proyecto autorizado y con argumentos controlados.

Un test aprobado comprueba el comportamiento expresado por ese test. No garantiza cobertura completa ni ausencia de defectos. Un proyecto sin tests puede compilar y pasar lint, pero sigue sin tener validación de comportamiento.

## Excepciones y errores

Las excepciones deben capturarse donde exista una estrategia concreta de recuperación, traducción o registro. Capturar `Exception` indiscriminadamente puede ocultar fallos. Los mensajes de error no deben exponer tokens, contraseñas, variables de entorno ni contenido privado completo.

## Dependencias y entornos

Un entorno virtual separa dependencias del sistema, pero no convierte automáticamente sus ejecutables en confiables. Elyndra prioriza herramientas locales de `.venv/bin` o `venv/bin` dentro del proyecto autorizado y registra la ruta utilizada. Nunca instala dependencias automáticamente.

Las dependencias declaradas en `pyproject.toml` son metadatos. Resolverlas o instalarlas puede usar red y ejecutar backends de construcción, por lo que queda fuera de las skills básicas de verificación.

## Flujo recomendado

Un flujo de verificación controlado puede seguir este orden:

1. Inspeccionar estructura y metadatos sin ejecutar código.
2. Validar `pyproject.toml` de forma determinista.
3. Compilar sintaxis sin importar módulos.
4. Ejecutar Ruff sin aplicar fixes.
5. Ejecutar mypy con caché temporal.
6. Ejecutar Pytest con aprobación explícita.
7. Guardar estado, duración y resultados acotados en el historial.

Una herramienta ausente puede producir un resultado parcial. Si el perfil exige todas las herramientas, la ausencia se trata como fallo. Las autorizaciones puntuales expiran al terminar y no deben modificar silenciosamente la configuración persistente.
