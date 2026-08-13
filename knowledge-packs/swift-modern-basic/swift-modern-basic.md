# Swift moderno — Base práctica y verificable

## Propósito

Este paquete introduce una base local para comprender proyectos Swift sin conceder permisos de ejecución. Alejandría explica conceptos y procedencia; las skills controladas inspeccionan, verifican sintaxis, revisan formato, compilan y ejecutan tests solo tras aprobación explícita.

## Swift y sus proyectos

Swift es un lenguaje compilado utilizado en aplicaciones Apple, servicios, herramientas de línea de comandos y proyectos multiplataforma. Los archivos fuente usan la extensión `.swift`. Un proyecto puede estar administrado por Swift Package Manager, Xcode o una combinación de ambos.

## Package.swift

`Package.swift` es un manifiesto escrito en Swift. Declara paquete, productos, targets, dependencias y versión de herramientas. Aunque parece configuración, SwiftPM lo compila y ejecuta para obtener el modelo del paquete. Inspeccionarlo como texto no equivale a evaluarlo.

## Dependencias y Package.resolved

Las dependencias pueden ser locales o remotas. `Package.resolved` fija versiones resueltas. Elyndra desactiva la resolución automática y exige un archivo resuelto cuando detecta dependencias remotas antes de build o tests. No ejecuta actualizaciones o resoluciones silenciosas.

## Sintaxis

`swiftc -parse archivo.swift` comprueba que el compilador pueda analizar la sintaxis sin enlazar un ejecutable. No demuestra que tipos, dependencias o el proyecto completo sean correctos, pero permite detectar errores sintácticos de forma acotada.

## Formato

`swift-format lint --strict` informa diferencias y problemas de formato sin modificar los archivos. Elyndra no usa comandos de reescritura ni modos in-place automáticos.

## Build con SwiftPM

`swift build` evalúa el manifiesto, resuelve la estructura del paquete y puede ejecutar plugins. Elyndra usa argumentos fijos, desactiva la resolución automática y dirige scratch, cachés y archivos temporales fuera del proyecto. Esto reduce modificaciones, pero no convierte SwiftPM en un sandbox completo.

## Tests

`swift test` compila y ejecuta tests. Las suites pueden usar XCTest o Swift Testing y ejecutan código del proyecto y sus dependencias. Requieren aprobación explícita. Un resultado correcto solo cubre los casos implementados por el proyecto.

## Xcode

Los directorios `.xcodeproj` y `.xcworkspace` contienen metadatos de proyectos y workspaces. Elyndra puede detectarlos, pero esta toolchain no invoca `xcodebuild` ni ejecuta esquemas automáticamente.

## Plugins y macros

Los plugins de SwiftPM, macros y otras extensiones pueden ejecutar código durante build. Elyndra los detecta de forma conservadora y muestra advertencias antes de las etapas ejecutables.

## Perfiles y autorización

Un perfil Swift puede elegir etapas, configuración debug o release, límites, exclusiones, fail-fast y política de herramientas obligatorias. El perfil no concede acceso. El proyecto debe estar dentro de una raíz persistente, ser confiable o recibir autorización puntual.

## Límites de confianza

Elyndra no instala Swift, no ejecuta scripts Swift arbitrarios, no actualiza dependencias, no invoca Xcode y no afirma aislamiento total de red. Las restricciones de proxy son defensivas; manifiestos, plugins, compilación y tests pueden ejecutar código del proyecto.

## Flujo recomendado

1. Inspeccionar rutas y metadatos sin evaluar `Package.swift`.
2. Validar estructura textual, dependencias y límites de confianza.
3. Comprobar sintaxis con `swiftc -parse`.
4. Revisar formato sin reescribir archivos.
5. Ejecutar build y tests SwiftPM únicamente tras aprobación.
6. Guardar estado, duración y resultados acotados en el historial.
