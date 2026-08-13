# Elyndra 0.6.3-dev — Alejandría basada en evidencia

Elyndra 0.6.3 corrige la principal limitación observada en 0.6.2: el motor
seleccionaba mejores bibliotecas, pero todavía pedía a un modelo pequeño que
reescribiera hechos que ya estaban disponibles localmente. Esto introducía
latencia, afirmaciones no respaldadas y respuestas inconsistentes.

## Respuesta estricta basada en evidencia

Las consultas que comienzan con expresiones como `Según Alejandría` intentan
responderse directamente con fragmentos recuperados de las bibliotecas activas.
Cuando existe cobertura suficiente:

- no se carga Ollama;
- no se generan afirmaciones nuevas;
- cada punto conserva una cita `[A#]`;
- se indica si alguna fuente todavía no fue revisada;
- el resultado declara `engine=alexandria-evidence` y `generated=false`.

Si una subtarea no tiene evidencia suficiente, Elyndra lo indica en lugar de
rellenarla silenciosamente con conocimiento general.

## Índice por secciones Markdown

El índice Alejandría pasa a la versión 2. Los libros Markdown se dividen usando
sus encabezados reales y solo se fragmentan por tamaño dentro de cada sección.
Esto evita títulos derivados de líneas incompletas de código y mejora la
selección de capítulos especializados.

Al iniciar 0.6.3, Elyndra reindexa una sola vez las fuentes existentes. La
revisión de una fuente se conserva si todas sus unidades anteriores estaban
marcadas como revisadas. Los archivos originales no se modifican.

Reindexación manual:

```bash
./scripts/elyndra-dev alexandria reindex --approve
```

Si una fuente no puede reprocesarse, el estado queda `partial` y la versión del
índice no se confirma, para permitir un nuevo intento posterior.

## Recuperación especializada

La búsqueda añade:

- coincidencias técnicas con límites de palabra;
- sinónimos controlados;
- anclas específicas para PHP, PDO, webhooks, OPcache y testing;
- expansión temática para recuperar capítulos complementarios;
- descarte de secciones débiles como referencias o propuestas de skills;
- prioridad para el dominio especializado exacto.

El libro general continúa disponible como respaldo, pero no se mezcla cuando
la biblioteca especializada ofrece cobertura suficiente.

## Diagnóstico local

Con `?diagnostics=1`, la interfaz muestra la fase de síntesis de evidencia, la
confianza de cobertura y el motor utilizado. Una respuesta extractiva correcta
debe registrar generación de modelo igual a cero.

## Límites de esta versión

- La síntesis es extractiva y puede conservar errores gramaticales presentes en
  la fuente; Alejandría no corrige silenciosamente los libros.
- Las respuestas no estrictas todavía pueden usar el modelo local.
- Una fuente no revisada puede utilizarse cuando es la mejor coincidencia, pero
  la respuesta muestra una advertencia visible.
- El conocimiento continúa separado de las skills y no concede ejecución de
  comandos, acceso a archivos ni permisos adicionales.
