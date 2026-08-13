# Rust moderno — Base práctica y verificable

## Propósito

Este paquete ofrece una base local para comprender proyectos Rust sin conceder permisos de ejecución. Alejandría explica conceptos y procedencia; las skills controladas inspeccionan, validan y ejecutan herramientas únicamente tras aprobación explícita.

## Cargo y la estructura del proyecto

`Cargo.toml` describe el paquete o workspace, sus dependencias, edición y configuración. `Cargo.lock` fija las versiones resueltas. El código suele estar bajo `src/`, los tests de integración bajo `tests/` y los benchmarks bajo `benches/`.

Un workspace puede agrupar varios crates. Los miembros y dependencias `path` deben revisarse porque pueden apuntar a carpetas externas al proyecto principal.

## Inspección determinista

Elyndra puede analizar `Cargo.toml` como TOML UTF-8 sin ejecutar Cargo, rustc, build scripts, macros de procedimiento ni código del proyecto. Esta inspección detecta metadatos, workspaces, crates conocidos y rutas locales, pero no demuestra que el código compile.

## Formato

`cargo fmt --all -- --check` comprueba el formato sin reescribir archivos. Elyndra no ejecuta `cargo fmt` sin `--check` y no aplica cambios automáticos.

## Cargo check

`cargo check` analiza y compila metadatos más rápido que una compilación completa, pero puede ejecutar `build.rs` y macros de procedimiento. Por ello requiere aprobación explícita. Algunos problemas que aparecen durante generación completa de código pueden no detectarse con `cargo check`.

## Clippy

Clippy añade lints sobre corrección, estilo, complejidad y prácticas idiomáticas. Elyndra ejecuta Clippy sin `--fix` y sin modificar el proyecto. Un resultado limpio no sustituye revisión de seguridad, pruebas ni validación funcional.

## Tests

`cargo test` compila y ejecuta tests unitarios, tests de integración y, según la configuración, doctests. Ejecuta código del proyecto y requiere aprobación explícita.

## Ejecución offline y locked

Elyndra usa `--offline` y `--locked`. La red queda desactivada y Cargo no puede cambiar `Cargo.lock`. Si el lockfile falta o una dependencia no está disponible localmente, la etapa se omite o falla de forma controlada en vez de descargar crates o modificar el proyecto.

El directorio `target` se redirige a una carpeta temporal externa y se elimina al finalizar.

## Build scripts y macros

`build.rs` y las macros de procedimiento son código ejecutable durante check, Clippy y tests. Elyndra los detecta y los muestra en la inspección, pero no puede convertir esas fases en análisis puramente estático. El propietario debe inspeccionar repositorios desconocidos antes de aprobarlos.

## Seguridad y límites

Elyndra no usa `shell=True`, no acepta flags libres, no ejecuta `cargo install`, `cargo update`, `cargo fix` ni comandos arbitrarios, no instala toolchains o componentes y no concede acceso persistente mediante perfiles.

Los perfiles solo controlan etapas, features, exclusiones, timeout, límite de archivos y herramientas obligatorias. La autorización del proyecto sigue siendo independiente.

## Flujo recomendado

1. Inspeccionar archivos, manifiesto, workspace y herramientas sin ejecutar código.
2. Validar `Cargo.toml` de forma determinista.
3. Comprobar formato sin modificar archivos.
4. Ejecutar `cargo check` con red desactivada y lockfile inmutable.
5. Ejecutar Clippy sin correcciones automáticas.
6. Ejecutar tests únicamente tras aprobación explícita.
7. Guardar estado, duración, exit code y salida acotada para comparación.
