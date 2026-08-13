# Kotlin moderno — Base práctica y verificable

## Propósito

Este paquete introduce una base local para comprender proyectos Kotlin/JVM sin conceder permisos de ejecución. Alejandría explica conceptos y procedencia; las skills controladas inspeccionan, compilan y ejecutan verificaciones únicamente tras aprobación explícita.

## Kotlin y la JVM

Kotlin puede compilarse para la JVM, JavaScript o plataformas nativas. La toolchain inicial de Elyndra se limita a Kotlin/JVM. Los archivos habituales usan extensiones `.kt` y `.kts`. Un script `.kts` puede contener código ejecutable, por lo que inspeccionarlo como texto no equivale a ejecutarlo.

## Estructura de proyecto

Los proyectos Kotlin/JVM suelen usar Maven o Gradle. Gradle Kotlin DSL utiliza `build.gradle.kts` y `settings.gradle.kts`; esos archivos son scripts y pueden ejecutar código durante la configuración. Los fuentes suelen residir en `src/main/kotlin` y los tests en `src/test/kotlin`.

## Compilación directa

`kotlinc` puede comprobar y compilar proyectos independientes. Elyndra envía los resultados a una carpeta temporal fuera del proyecto y no acepta plugins, classpaths o flags arbitrarios proporcionados por el usuario.

En proyectos administrados por Maven o Gradle, el build declarado controla dependencias, classpath, plugins y generación de código. Por eso Elyndra omite `kotlinc` directo por defecto cuando detecta uno de esos gestores, evitando falsos fallos por dependencias ausentes.

## Maven y Gradle

Maven y Gradle pueden ejecutar plugins y lógica del proyecto. Elyndra solo usa binarios globales, tareas fijas y modo offline. Los wrappers `mvnw` y `gradlew` se detectan, pero no se ejecutan automáticamente.

El modo offline evita nuevas descargas. Una dependencia o plugin ausente puede provocar un fallo correcto en vez de abrir acceso de red.

## Tests

Los tests ejecutan código del proyecto y sus dependencias. Requieren aprobación explícita. Un resultado correcto demuestra únicamente que pasaron los casos cubiertos por la suite; no garantiza ausencia total de defectos.

## Frameworks habituales

Ktor se usa para servicios y aplicaciones Kotlin. Compose Multiplatform se usa para interfaces. Android y AndroidX forman parte de muchos proyectos móviles. Spring también puede utilizar Kotlin sobre la JVM. Detectar un framework por dependencias o descriptores no concede permisos ni ejecuta su build.

## Perfiles y autorización

Un perfil Kotlin puede elegir etapas, herramienta de build, objetivo JVM, límites, exclusiones y política de herramientas obligatorias. El perfil no concede acceso. El proyecto debe estar dentro de una raíz persistente, ser confiable o recibir autorización puntual.

## Flujo recomendado

1. Inspeccionar estructura y herramientas sin ejecutar código.
2. Validar descriptores Maven y Gradle como datos.
3. En proyectos independientes, compilar con `kotlinc` hacia una carpeta temporal.
4. En proyectos Maven o Gradle, usar el build offline como verificación principal.
5. Ejecutar tests únicamente tras aprobación.
6. Guardar estado, duración y resultados acotados en el historial.

Elyndra no instala Kotlin, Maven, Gradle, plugins ni dependencias automáticamente.
