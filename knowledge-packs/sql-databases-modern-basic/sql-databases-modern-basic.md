# SQL y bases de datos — Base práctica y verificable

## Propósito

Este paquete introduce fundamentos para leer, revisar y diagnosticar SQL sin conceder permisos de escritura. Alejandría explica conceptos y procedencia; las skills controladas inspeccionan archivos, migraciones y metadatos SQLite únicamente tras aprobación explícita.

## SQL y dialectos

SQL describe consultas, estructuras y cambios sobre bases relacionales. SQLite, PostgreSQL, MySQL y MariaDB comparten gran parte del lenguaje, pero difieren en tipos, funciones, DDL, transacciones y extensiones. Elyndra puede detectar señales de dialecto, pero una validación genérica no reemplaza al motor de destino.

## Consultas de lectura

`SELECT` y las expresiones comunes de tabla con `WITH` consultan información. Deben revisarse filtros, joins, cardinalidad, índices y límites. `SELECT *` puede aumentar transferencia, acoplar consumidores al esquema y ocultar columnas innecesarias.

## DDL y DML

DDL crea o modifica objetos: `CREATE`, `ALTER`, `DROP` y `TRUNCATE`. DML modifica filas: `INSERT`, `UPDATE`, `DELETE`, `MERGE` y variantes como `REPLACE`. Elyndra rechaza estas operaciones fuera de migraciones por defecto y nunca las ejecuta automáticamente.

## Migraciones

Las migraciones deben ser ordenadas, reproducibles y revisables. Conviene usar identificadores únicos, transacciones cuando el motor lo permite, backups verificados y una estrategia explícita de reversión. Operaciones como `DROP`, `TRUNCATE`, eliminación de columnas o actualizaciones sin `WHERE` requieren revisión especial.

## Operaciones sensibles

Funciones o sentencias como `ATTACH`, `load_extension`, `PRAGMA writable_schema`, exportaciones a archivos o ejecución de programas pueden ampliar el alcance de una consulta. Elyndra las identifica como sensibles y no las autoriza dentro de su análisis estático.

## SQLite en modo de solo lectura

Una base SQLite puede abrirse mediante URI con `mode=ro`. `PRAGMA query_only=ON` y el authorizer de SQLite agregan defensas contra escritura. La inspección de Elyndra lee `sqlite_master` y metadatos PRAGMA de tablas, columnas, índices y claves foráneas; no cuenta ni devuelve filas de usuario.

## EXPLAIN QUERY PLAN

`EXPLAIN QUERY PLAN` muestra una descripción del plan elegido por SQLite para una consulta. Puede revelar scans completos, uso de índices y joins. No mide por sí solo tiempos reales ni sustituye pruebas con datos representativos. Elyndra solo lo permite para una consulta `SELECT` o `WITH` y con la base abierta en modo lectura.

## Índices

Un índice puede acelerar filtros, joins y ordenamientos, pero también consume espacio y aumenta el costo de escrituras. El orden de columnas, selectividad y cobertura deben corresponder a consultas reales. Un índice existente no garantiza que el optimizador lo use.

## Transacciones

Las transacciones agrupan cambios y permiten confirmar o revertirlos. Su comportamiento depende del motor y del tipo de DDL. Una migración con múltiples sentencias debería documentar si puede ejecutarse dentro de una transacción y qué ocurre ante un fallo intermedio.

## Parámetros y seguridad

Los valores de usuario deben enviarse mediante parámetros del driver, no concatenarse en SQL. La parametrización reduce inyección SQL, pero los identificadores dinámicos, permisos excesivos y consultas construidas parcialmente siguen requiriendo validación.

## Backups y restauración

Un backup solo es confiable si puede restaurarse. Antes de cambios destructivos se deben verificar consistencia, retención, cifrado, permisos, espacio y procedimiento de recuperación. Elyndra no crea, restaura ni modifica backups desde esta toolchain.

## Perfiles y autorización

Un perfil SQL puede elegir dialecto, etapas, límites, exclusiones y políticas para mutaciones o migraciones destructivas. El perfil no concede acceso al proyecto ni permiso para ejecutar sentencias.

## Límites de confianza

El análisis estático no implementa parsers completos de todos los dialectos. Puede detectar estructura, categorías y patrones peligrosos, pero no garantiza semántica, permisos, rendimiento o compatibilidad con una versión específica del servidor. La inspección SQLite es local y de solo lectura; PostgreSQL, MySQL y MariaDB no reciben conexiones automáticas en esta versión.

## Flujo recomendado

1. Inspeccionar archivos, migraciones, dialectos y bases locales.
2. Validar SQL de lectura y separar claramente migraciones.
3. Revisar versiones duplicadas y operaciones destructivas.
4. Inspeccionar metadatos SQLite sin leer filas de usuario.
5. Analizar planes de consultas `SELECT` concretas.
6. Aplicar migraciones o cambios solo mediante herramientas externas, backups y aprobación humana.
