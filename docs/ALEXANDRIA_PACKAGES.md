# Paquetes locales de Alejandría

Los paquetes permiten distribuir conocimientos opcionales sin aumentar la
instalación doméstica básica de Elyndra.

## Estructura mínima

```text
mi-paquete/
├── elyndra-package.json
└── libro.md
```

Ejemplo de manifiesto:

```json
{
  "schema_version": 1,
  "package_id": "programming.web.html-basics",
  "name": "HTML — Fundamentos",
  "version": "1.0.0",
  "tier": "optional",
  "domain": "programming/web/html",
  "language": "es",
  "license_id": "CC-BY-4.0",
  "publisher": "Autor o proyecto",
  "tags": ["html", "web"],
  "sources": [
    {
      "path": "libro.md",
      "title": "HTML — Fundamentos",
      "sha256": "SHA256_HEXADECIMAL_DEL_ARCHIVO"
    }
  ]
}
```

## Seguridad

- Solo se aceptan rutas relativas dentro del paquete.
- Los manifiestos y fuentes enlazados simbólicamente se rechazan.
- Cada fuente debe declarar su SHA-256.
- No se usa red.
- No se ejecuta código.
- No se instalan dependencias.
- Las fuentes importadas quedan no revisadas.
- Reemplazar una versión requiere eliminar primero la anterior.

## CLI

```bash
./scripts/elyndra-dev alexandria package-inspect /ruta/paquete
./scripts/elyndra-dev alexandria package-install /ruta/paquete --approve
./scripts/elyndra-dev alexandria package-list
./scripts/elyndra-dev alexandria package-disable ID --approve
./scripts/elyndra-dev alexandria package-enable ID --approve
./scripts/elyndra-dev alexandria package-remove ID --approve
```

## Crear un paquete desde fuentes locales

```bash
./scripts/elyndra-dev alexandria package-create \
  /ruta/destino \
  --package-id programming.web.html-basics \
  --name "HTML — Fundamentos" \
  --version 1.0.0 \
  --domain programming/web/html \
  --language es \
  --license-id CC-BY-4.0 \
  --source /ruta/libro.md \
  --approve
```

Elyndra copia las fuentes, crea nombres internos seguros, calcula el SHA-256 y valida el manifiesto
resultante. La carpeta de destino debe estar vacía.

## Exportar un paquete instalado

```bash
./scripts/elyndra-dev alexandria package-export \
  programming.web.html-basics \
  /ruta/exportado \
  --approve
```

La exportación reconstruye un paquete autocontenido desde las fuentes privadas de la biblioteca. No
marca fuentes como revisadas y no modifica el paquete instalado.

La creación, instalación y exportación también están disponibles en el centro de control local:

```text
http://127.0.0.1:8765/control
```

## Paquete opcional incluido: Python moderno

Elyndra incluye un primer paquete opcional de referencia en:

```text
knowledge-packs/python-modern-basic
```

Puede inspeccionarse e instalarse localmente sin red:

```bash
./scripts/elyndra-dev alexandria package-inspect \
  knowledge-packs/python-modern-basic

./scripts/elyndra-dev alexandria package-install \
  knowledge-packs/python-modern-basic \
  --approve
```

El paquete documenta estructura de proyectos, `pyproject.toml`, compilación sintáctica, Ruff, mypy,
Pytest y límites de seguridad. Sus fuentes quedan no revisadas hasta que el propietario las apruebe.

## Paquete opcional incluido: Go moderno

Elyndra incluye un paquete opcional para módulos, `gofmt`, vet, build, tests y límites de ejecución segura:

```text
knowledge-packs/go-modern-basic
```

Puede inspeccionarse e instalarse localmente sin red. Sus fuentes quedan no revisadas hasta que el propietario las apruebe.


## Paquete opcional incluido: Rust moderno

Elyndra incluye un paquete opcional para Cargo, workspaces, rustfmt, Clippy, tests y límites de ejecución segura:

```text
knowledge-packs/rust-modern-basic
```

Puede inspeccionarse e instalarse localmente sin red. Sus fuentes quedan no revisadas hasta que el propietario las apruebe.


## Paquete opcional incluido: Kotlin moderno

Elyndra incluye un paquete opcional para Kotlin/JVM, Maven, Gradle Kotlin DSL, Ktor, Compose, Android y límites de ejecución segura:

```text
knowledge-packs/kotlin-modern-basic
```

Puede inspeccionarse e instalarse localmente sin red. Sus fuentes quedan no revisadas hasta que el propietario las apruebe.


## Paquete opcional incluido: C# y .NET modernos

Elyndra incluye un paquete opcional para proyectos C#, soluciones, MSBuild, NuGet, formato, build, tests y límites de ejecución segura:

```text
knowledge-packs/dotnet-modern-basic
```

Puede inspeccionarse e instalarse localmente sin red. Sus fuentes quedan no revisadas hasta que el propietario las apruebe.


## Paquete opcional incluido: Swift moderno

Elyndra incluye un paquete opcional para Swift, SwiftPM, Xcode, sintaxis, formato, build, tests y límites de ejecución segura:

```text
knowledge-packs/swift-modern-basic
```

Puede inspeccionarse e instalarse localmente sin red. Sus fuentes quedan no revisadas hasta que el propietario las apruebe.


## Paquete opcional incluido: Dart y Flutter modernos

Elyndra incluye un paquete opcional para Dart, Flutter, Pub, formato, análisis, tests y límites de ejecución segura:

```text
knowledge-packs/dart-flutter-modern-basic
```

Puede inspeccionarse e instalarse localmente sin red. Sus fuentes quedan no revisadas hasta que el propietario las apruebe.


## Paquete opcional incluido: SQL y bases de datos

Elyndra incluye un paquete opcional para SQL, SQLite, migraciones, esquemas, índices, transacciones, backups y límites de ejecución segura:

```text
knowledge-packs/sql-databases-modern-basic
```

Puede inspeccionarse e instalarse localmente sin red. Sus fuentes quedan no revisadas hasta que el propietario las apruebe.
