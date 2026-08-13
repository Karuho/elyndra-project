# Elyndra 0.7.1 — Políticas centralizadas y proyectos confiables

Elyndra 0.7.1 consolida la autorización de skills en una política común y mantiene la ejecución local, explícita y auditable.

## Alcances de autorización

- `single_file`: acceso a un archivo concreto durante una ejecución.
- `project_once`: acceso temporal a un proyecto externo mediante aprobación explícita.
- `project_persistent`: proyecto cubierto por una raíz configurada o registrado como confiable.
- `denied`: operación bloqueada por política.

## Proyectos confiables

Las raíces adicionales se registran explícitamente en SQLite y no modifican silenciosamente `config.toml`:

```bash
./scripts/elyndra-dev project trust /ruta/al/proyecto --approve
./scripts/elyndra-dev project trusted
./scripts/elyndra-dev project trust-inspect /ruta/al/proyecto
./scripts/elyndra-dev project untrust /ruta/al/proyecto --approve
```

No se permiten como proyecto confiable `/`, el directorio HOME completo ni archivos individuales.

## Planificación sin ejecución

```bash
./scripts/elyndra-dev skill inspect phpstan.analyse
./scripts/elyndra-dev skill plan phpstan.analyse --params '{"path":"/ruta/al/proyecto"}'
```

`skill plan` resuelve herramienta, ruta, alcance, riesgo, timeout y argumentos sin iniciar procesos.

## Aprobaciones web

Cada aprobación web es de un solo uso, está vinculada al chat y a la solicitud exacta, expira localmente y queda invalidada al cancelar. Reutilizar el token no ejecuta nuevamente la skill.

## Auditoría

```bash
./scripts/elyndra-dev audit list --action skill.execute
./scripts/elyndra-dev audit show ID
```

Los detalles sensibles continúan redactándose antes de guardarse.

## Herramientas PHP

PHPStan y PHPUnit priorizan los binarios locales del proyecto:

1. `vendor/bin/phpstan` o `vendor/bin/phpunit`.
2. Binario global disponible en `PATH`.
3. Error controlado si no existe herramienta.

Elyndra no instala ni descarga Composer, PHPStan o PHPUnit automáticamente.
