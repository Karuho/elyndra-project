# Java moderno — Base práctica y verificable

## Propósito

Este paquete introduce una base local para comprender y revisar proyectos Java sin conceder permisos de ejecución. Alejandría explica conceptos y procedencia; las skills controladas inspeccionan, compilan y ejecutan verificaciones únicamente tras aprobación explícita.

## Estructura de proyecto

Los proyectos Maven suelen usar `pom.xml`. Los proyectos Gradle suelen usar `settings.gradle`, `settings.gradle.kts`, `build.gradle` o `build.gradle.kts`. Leer estos descriptores no equivale a ejecutar el build. Los scripts Gradle son código y los plugins Maven pueden ejecutar lógica durante el ciclo de vida.

El código principal suele estar en `src/main/java` y las pruebas en `src/test/java`. Directorios como `target`, `build`, `.gradle`, `.idea` y `out` no deberían formar parte del escaneo normal del código fuente.

## Compilación con javac

`javac` comprueba sintaxis, tipos y referencias disponibles para los archivos compilados. Un proyecto puede necesitar dependencias externas o módulos adicionales para compilar. Un error de símbolos no encontrados puede indicar dependencias ausentes y no necesariamente un error sintáctico.

En proyectos administrados por Maven o Gradle, el build declarado es la fuente principal del classpath, las dependencias y el código generado. Elyndra omite `javac` directo por defecto en esos proyectos para evitar falsos fallos por dependencias ausentes. La compilación directa sigue disponible cuando el usuario la solicita expresamente.

Elyndra usa `-proc:none` para impedir que procesadores de anotaciones se ejecuten durante la compilación directa. Las clases generadas se escriben en una carpeta temporal fuera del proyecto y se eliminan al terminar.

## Maven

Maven describe el proyecto en `pom.xml` y organiza tareas mediante fases como `compile` y `test`. Los plugins pueden ejecutar código y resolver artefactos. Por eso las ejecuciones requieren aprobación explícita.

El modo `--offline` evita nuevas descargas. Si una dependencia o plugin no existe en el repositorio local, el build puede fallar correctamente en lugar de acceder a la red.

## Gradle

Gradle utiliza scripts de construcción que pueden ejecutar código durante la configuración. Elyndra no ejecuta `gradlew` ni otros wrappers incluidos en el proyecto. Solo puede invocar un binario global de Gradle con tareas fijas, modo offline, sin daemon y salida acotada.

Un wrapper detectado se informa como metadato, pero nunca se considera automáticamente confiable.

## Pruebas

Las pruebas ejecutan código del proyecto y de sus dependencias. Una suite aprobada comprueba únicamente los comportamientos cubiertos por sus tests. Un resultado correcto no demuestra ausencia total de defectos ni cobertura completa.

Las pruebas Maven o Gradle se ejecutan mediante objetivos fijos. Elyndra no acepta tareas arbitrarias, argumentos libres ni scripts proporcionados por el usuario.

## Perfiles y autorización

Un perfil Java puede elegir etapas, herramienta de build, release de Java, límites, exclusiones y política de herramientas obligatorias. El perfil no concede acceso. El proyecto debe permanecer dentro de una raíz persistente, estar registrado como confiable o recibir autorización puntual para una sola ejecución.

## Flujo recomendado

1. Inspeccionar estructura y herramientas sin ejecutar código.
2. Validar `pom.xml` y la presencia de descriptores Gradle.
3. En proyectos sin gestor, compilar con `javac -proc:none`.
4. En proyectos Maven o Gradle, usar su build offline como compilación autoritativa.
5. Ejecutar tests con aprobación explícita.
6. Guardar estado, duración y resultados acotados en el historial.

Una herramienta ausente puede producir un resultado parcial. Si el perfil exige todas las herramientas, la ausencia se trata como fallo. Elyndra no instala Java, Maven, Gradle ni dependencias automáticamente.
