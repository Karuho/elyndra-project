# Elyndra 0.7.12-alpha

## Alcance

Elyndra 0.7.12-alpha introduce la toolchain Kotlin/JVM controlada y cambia la etiqueta visible de las versiones activas desde `.dev0`/`-dev` a `-alpha`.

La etiqueta humana y los tags Git usan:

```text
0.7.12-alpha
v0.7.12-alpha
```

Python normaliza esta versión preliminar a `0.7.12a0` en metadatos de paquetes y nombres de wheels. Elyndra conserva `0.7.12-alpha` en `doctor`, la interfaz web y su documentación.

## Skills Kotlin

- `kotlin.project_inspect`
- `kotlin.descriptor_validate`
- `kotlin.kotlinc_compile`
- `kotlin.build_project`
- `kotlin.test_project`
- `kotlin.verify_project`

El flujo completo inspecciona el proyecto, valida descriptores, decide si corresponde compilación directa y ejecuta build/tests aprobados. Los resultados se guardan en el historial genérico bajo la toolchain `kotlin`.

## Límites de confianza

- La inspección no evalúa `build.gradle.kts` ni otros scripts Kotlin.
- Los wrappers `mvnw` y `gradlew` solo se detectan; no se ejecutan.
- Maven y Gradle usan binarios globales, argumentos fijos y modo offline.
- La compilación directa incluye únicamente archivos `.kt`; los `.kts` se cuentan como scripts pero no se entregan a `kotlinc`.
- `kotlinc` escribe únicamente en un directorio temporal externo.
- En proyectos Maven o Gradle, `kotlinc` directo se omite por defecto porque el build administra classpath, plugins y código generado.
- No se instalan Kotlin, Maven, Gradle, plugins ni dependencias.

## Perfiles y control

Los perfiles Kotlin permiten definir etapas, build principal, objetivo JVM, `fail-fast`, herramientas obligatorias, exclusiones, máximo de archivos, timeout y límite de salida. Un perfil no concede acceso al proyecto.

El centro de control muestra perfiles y verificaciones Kotlin recientes. Las API locales permiten consultar, guardar y eliminar perfiles persistentes.

## Alejandría

Se agrega el paquete opcional:

```text
knowledge-packs/kotlin-modern-basic
```

El paquete explica Kotlin/JVM, Gradle Kotlin DSL, Maven, `kotlinc`, Ktor, Compose, Android, tests y límites de ejecución. La instalación es local, verifica SHA-256 y deja las fuentes como no revisadas.

## Migración

La base SQLite avanza al esquema 20 mediante `CREATE TABLE IF NOT EXISTS`, sin eliminar perfiles ni verificaciones anteriores.
