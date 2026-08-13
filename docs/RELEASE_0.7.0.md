# Elyndra 0.7.0-dev — Skills PHP controladas

Elyndra 0.7.0 conecta el conocimiento de Alejandría con cuatro verificaciones
locales explícitas. La biblioteca explica qué debería comprobarse; una skill
aprobada ejecuta la herramienta real y conserva exit code, duración, argv y cwd
en la auditoría sin almacenar la salida completa en el evento.

## Skills iniciales

- `php.syntax_validate`: ejecuta `php -l` sobre un archivo autorizado.
- `composer.validate`: valida `composer.json` y `composer.lock` con plugins,
  scripts e interacción desactivados y una solicitud de entorno sin red.
- `phpstan.analyse`: usa primero `vendor/bin/phpstan` y después el PATH global.
- `phpunit.run`: usa primero `vendor/bin/phpunit` y después el PATH global.

Las cuatro skills tienen riesgo medio y requieren aprobación explícita. No usan
shell. Las rutas se resuelven únicamente dentro de `allowed_roots`, los procesos
tienen timeout, la salida queda limitada y un timeout termina el grupo de
procesos. Los valores predeterminados son 120 segundos y 12.000 caracteres,
configurables mediante `[skills.php]`.

## Interfaz web

Una orden natural como:

```text
Ejecuta php -l en /home/usuario/Proyectos/app/src/example.php
```

primero muestra un diálogo de Elyndra. Si el propietario aprueba, la misma orden
se reenvía con aprobación y el resultado queda guardado como un turno del chat.
Cancelar no inicia el proceso ni persiste un turno incompleto.

## CLI

```bash
elyndra php status
elyndra php syntax RUTA --approve
elyndra php composer-validate PROYECTO --approve
elyndra php phpstan PROYECTO --level max --approve
elyndra php phpunit PROYECTO --testsuite unit --approve
```

El comando genérico `elyndra skill run` continúa disponible.

## Límite de aislamiento

`php -l` no ejecuta el archivo PHP. Composer se invoca sin plugins ni scripts.
PHPStan y PHPUnit sí pueden cargar o ejecutar código del proyecto, configuración,
autoloaders y extensiones. La aprobación es obligatoria y el proceso está
limitado, pero 0.7.0 no crea un namespace de red del kernel ni un contenedor.
No deben ejecutarse proyectos desconocidos o no confiables.

## Alejandría

La síntesis determinista para `php -l`, PHPStan y PHPUnit ahora distingue
explícitamente sintaxis, análisis estático y pruebas ejecutadas cuando las
secciones recuperadas respaldan cada afirmación.

## Configuración opcional

```toml
[skills.php]
timeout_seconds = 120
max_output_chars = 12000
```

Las configuraciones anteriores que no contienen esta sección siguen usando los
valores predeterminados.
