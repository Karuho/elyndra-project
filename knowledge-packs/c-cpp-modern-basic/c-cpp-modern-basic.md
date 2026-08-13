# C y C++ modernos — Base práctica y verificable

## Propósito

Este paquete ofrece una base local para comprender proyectos C y C++ sin conceder permisos de ejecución. Alejandría explica conceptos y procedencia; las skills controladas inspeccionan, validan y compilan únicamente tras aprobación explícita.

## Archivos y estructura

Los fuentes C usan normalmente `.c`; C++ usa `.cc`, `.cpp` o `.cxx`. Las cabeceras suelen usar `.h`, `.hpp`, `.hh` o `.hxx`. Carpetas de salida como `build`, `out`, `dist` y `cmake-build-*` no deberían mezclarse con el escaneo del código fuente.

## Compilación sintáctica

GCC y Clang permiten comprobar traducción sin enlazar mediante `-fsyntax-only`. Esta comprobación detecta errores de sintaxis, tipos y símbolos visibles, pero puede fallar si faltan includes, defines o dependencias configuradas por el sistema de build.

En proyectos administrados por CMake, una compilación directa puede producir falsos fallos porque el compilador no recibe automáticamente todos los includes, defines y artefactos generados. Por eso Elyndra omite por defecto la etapa directa cuando CMake es el build principal y deja esa validación al build configurado.

## CMake

`CMakeLists.txt` es código de configuración y puede ejecutar procesos, localizar herramientas o intentar descargas. Leerlo no equivale a ejecutarlo. Elyndra valida estructura básica sin ejecutar CMake y señala funciones sensibles como `execute_process`, `file(DOWNLOAD)`, `ExternalProject_Add` o `FetchContent_MakeAvailable`.

Cuando se aprueba un build, Elyndra usa una carpeta temporal fuera del proyecto. La configuración fuerza `FETCHCONTENT_FULLY_DISCONNECTED=ON`, proxies locales inválidos y argumentos fijos. Esto reduce descargas accidentales, pero no convierte CMake en un sandbox completo.

## Análisis estático

cppcheck inspecciona fuentes C y C++ sin compilar ni ejecutar el programa. Puede detectar problemas de estilo, portabilidad, rendimiento y algunas construcciones peligrosas. No reemplaza al compilador, sanitizers, tests ni revisión manual.

## Tests

CTest ejecuta binarios construidos por el proyecto. Requiere aprobación porque puede ejecutar código arbitrario del repositorio. Un resultado correcto solo valida los tests definidos y no demuestra ausencia total de defectos.

## Seguridad y límites

Elyndra no usa shell, no ejecuta Make ni Meson automáticamente, no acepta flags libres y no instala compiladores, CMake o cppcheck. Los perfiles controlan etapas, estándares, límites, exclusiones, timeout y herramientas obligatorias, pero nunca conceden permisos por sí mismos.

## Flujo recomendado

1. Inspeccionar estructura, lenguajes y herramientas.
2. Validar descriptores sin ejecutarlos.
3. Usar sintaxis directa solo cuando no exista un build administrado o cuando el usuario la solicite explícitamente.
4. Ejecutar cppcheck si está disponible.
5. Configurar y compilar CMake en una carpeta temporal.
6. Ejecutar CTest únicamente con aprobación.
7. Guardar estado, duración, exit code y salida acotada.
