# Dart y Flutter modernos — Base práctica y verificable

## Propósito

Este paquete introduce una base local para comprender proyectos Dart y Flutter sin conceder permisos de ejecución. Alejandría explica conceptos y procedencia; las skills controladas inspeccionan descriptores, revisan formato, ejecutan análisis y lanzan tests únicamente tras aprobación explícita.

## Dart y Flutter

Dart es un lenguaje orientado a objetos utilizado en aplicaciones, herramientas, servidores y especialmente en Flutter. Flutter es un framework de interfaz multiplataforma que usa Dart para aplicaciones móviles, web y escritorio. Los archivos fuente usan la extensión `.dart`.

## pubspec.yaml

`pubspec.yaml` declara el nombre del paquete, restricciones del SDK, dependencias, recursos y configuración específica de Flutter. Elyndra lo interpreta como YAML acotado. La inspección no ejecuta `dart`, `flutter` ni comandos de Pub.

## pubspec.lock y package_config

`pubspec.lock` fija versiones resueltas. `.dart_tool/package_config.json` describe paquetes disponibles localmente. Elyndra no crea ni actualiza estos archivos. Analyze y tests dependen de que las dependencias necesarias ya estén disponibles.

## Dependencias locales y remotas

Una dependencia puede usar una versión publicada, una ruta local o un repositorio Git. Las rutas que salen del proyecto autorizado requieren revisión. Elyndra no ejecuta `dart pub get`, `flutter pub get`, `pub upgrade` ni descargas automáticas.

## Formato

`dart format --output=none --set-exit-if-changed` comprueba formato sin reescribir archivos. Un exit code distinto de cero indica diferencias o problemas. Elyndra no usa modos de escritura automáticos.

## Análisis estático

`dart analyze --fatal-infos` revisa tipos, imports, lints y errores estáticos en proyectos Dart. En proyectos Flutter, `flutter analyze --no-pub` utiliza el contexto del SDK Flutter sin resolver paquetes. El análisis no demuestra que la aplicación funcione en ejecución.

## Tests Dart

`dart test --reporter compact` ejecuta suites que normalmente usan `package:test`. Los tests ejecutan código del proyecto y requieren aprobación explícita. Elyndra no instala el paquete de testing ni crea dependencias faltantes.

## Tests Flutter

`flutter test --no-pub --reporter compact` ejecuta tests de unidades, widgets e integración compatibles con el runner. `--no-pub` evita una resolución automática previa. Los tests pueden cargar plugins, assets y código del proyecto.

## analysis_options.yaml

`analysis_options.yaml` configura lints, includes y reglas del analizador. Elyndra valida su estructura YAML como datos, pero no descarga paquetes de lints ni interpreta includes remotos.

## Generación de código

Herramientas como `build_runner`, Freezed, json_serializable o generadores de assets ejecutan código y escriben archivos. Esta toolchain no ejecuta `dart run`, `flutter pub run`, build_runner ni generadores automáticos.

## Build y ejecución

Compilar aplicaciones Flutter puede requerir Android SDK, Xcode, navegadores, toolchains nativas y aceptar licencias. Elyndra 0.7.15-alpha no ejecuta `flutter build`, `flutter run`, emuladores ni despliegues. Estas capacidades deberán diseñarse como skills independientes y explícitamente aprobadas.

## Perfiles y autorización

Un perfil Dart/Flutter puede elegir descriptores, formato, análisis, tests, runner Dart o Flutter, límites, exclusiones, fail-fast y política de herramientas obligatorias. El perfil no concede acceso al proyecto.

## Límites de confianza

Elyndra restringe variables de proxy y desactiva telemetría de las herramientas, pero no afirma aislamiento total de red. Analyze y tests pueden cargar paquetes y ejecutar código ya disponible en el equipo. El usuario debe tratar proyectos desconocidos como no confiables.

## Flujo recomendado

1. Inspeccionar rutas y `pubspec.yaml` sin ejecutar herramientas.
2. Validar YAML, dependencias y rutas locales.
3. Comprobar formato sin modificar archivos.
4. Ejecutar análisis sin `pub get` automático.
5. Ejecutar tests únicamente tras aprobación.
6. Guardar estado, duración y resultados acotados en el historial.
