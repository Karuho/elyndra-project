# Ruby moderno — Base práctica y verificable

## Propósito

Este paquete ofrece una base local para comprender proyectos Ruby sin conceder permisos de ejecución. Alejandría explica conceptos y procedencia; las skills controladas inspeccionan, validan y ejecutan herramientas únicamente tras aprobación explícita.

## Estructura de proyectos

Los archivos Ruby usan normalmente `.rb`; tareas Rake pueden usar `.rake` o `Rakefile`. `Gemfile` declara dependencias para Bundler, `Gemfile.lock` fija versiones resueltas y los archivos `.gemspec` describen gemas publicables. Carpetas como `.bundle`, `vendor`, `tmp`, `log` y `coverage` suelen excluirse de escaneos generales.

## Sintaxis con ruby -c

`ruby -c archivo.rb` comprueba la sintaxis sin ejecutar el cuerpo normal del archivo. No valida dependencias, comportamiento, tipos dinámicos ni integración con Rails. Elyndra ejecuta esta comprobación archivo por archivo, sin shell, con timeout y salida limitada.

## Bundler

`bundle check` verifica si las gemas declaradas están disponibles localmente. No equivale a `bundle install` y no debería instalar ni actualizar dependencias. El `Gemfile` es código Ruby y Bundler lo evalúa, por lo que esta etapa requiere aprobación incluso cuando se usa solo `check`.

Elyndra no ejecuta `bundle install`, `bundle update`, tareas Rake ni comandos arbitrarios. Las dependencias git o path pueden cargar código local y deben revisarse con cautela.

## RuboCop

RuboCop realiza análisis estático y de estilo. Puede detectar convenciones, complejidad y algunos errores comunes, pero no reemplaza tests ni revisión manual. Elyndra no utiliza autocorrección y limita los argumentos a una forma conocida y auditable.

## RSpec y Minitest

RSpec y Minitest ejecutan código del proyecto. Una suite correcta demuestra únicamente que los casos definidos pasaron en ese entorno. Los tests requieren aprobación explícita y pueden tener efectos secundarios si el proyecto fue diseñado de forma insegura.

## Rails y otros frameworks

Rails, Sinatra y Hanami agregan convenciones, carga automática, tareas y dependencias. La inspección determinista puede reconocer señales de estos frameworks sin iniciar la aplicación. La verificación completa no sustituye una revisión de migraciones, configuración, secretos o servicios externos.

## Seguridad y límites

Elyndra no usa `shell=True`, no acepta flags libres, no instala Ruby ni gemas, no ejecuta Rake automáticamente y no persiste nuevas raíces sin autorización. Los perfiles controlan etapas, framework de tests, exclusiones, timeout, límite de salida y herramientas obligatorias, pero nunca conceden permisos por sí mismos.

## Flujo recomendado

1. Inspeccionar estructura, dependencias y frameworks sin ejecutar código.
2. Validar que Gemfile y gemspecs sean UTF-8 legibles sin evaluarlos.
3. Ejecutar `bundle check` solo con aprobación.
4. Comprobar sintaxis mediante `ruby -c`.
5. Ejecutar RuboCop sin autocorrección cuando esté disponible.
6. Ejecutar RSpec o Minitest únicamente con aprobación.
7. Guardar estado, duración, exit code y salida acotada.
