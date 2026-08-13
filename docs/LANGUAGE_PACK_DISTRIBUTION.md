# Distribución de paquetes lingüísticos

Elyndra funciona sin paquetes externos, pero el diccionario español completo de
`0.8.8-alpha` requiere el bundle desmontable `elyndra-es-core` versión
`2026.08.01-r1`. Los datos no forman parte del repositorio ni de la licencia del código.

El bundle contiene cuatro archivos independientes (Informal, Wikcionario, MCR/OMW y
CLDR), su manifiesto `elyndra-language-bundle.json`, un alias versionado y
`SHA256SUMS`. Cada pack conserva su manifiesto, SQLite, atribución y textos de licencia.
Los assets mayores que el umbral configurable se dividen en partes ordenadas; el
manifiesto registra tanto sus hashes como el hash del archivo reconstruido.

La construcción es reproducible mediante `--build-epoch` y no usa red:

```bash
elyndra alexandria language-bundle-create --pack RUTA_PACK_1 --pack RUTA_PACK_2 \
  --pack RUTA_PACK_3 --pack RUTA_PACK_4 --output-dir RUTA_RELEASE \
  --build-epoch 1700000000 --approve
elyndra alexandria language-bundle-inspect RUTA_RELEASE/elyndra-language-bundle.json
elyndra alexandria language-bundle-verify RUTA_RELEASE/elyndra-language-bundle.json
elyndra alexandria language-bundle-install RUTA_RELEASE/elyndra-language-bundle.json \
  --approve --enable
```

Inspect y verify son de solo lectura. Install preinspecciona todo, exige Elyndra
`>=0.8.8a0,<0.9.0a0`, verifica espacio, partes, archives, manifiestos, bases y contenido,
y deja los packs deshabilitados salvo `--enable`. Un fallo revierte únicamente las
instalaciones nuevas del intento. No hay descarga automática en 0.8.8; una futura
pasarela controlada podrá adquirir únicamente bundles verificados y con aprobación.

Release de datos recomendada: repositorio `Elyndra-Language-Packs`, tag
`spanish-core-2026.08.01-r1`. Esta implementación no crea ni publica esa release.

Las prioridades canónicas de consulta e instalación son: Informal `400`, Wikcionario
`300`, MCR/OMW `250` y CLDR `200`. Las atribuciones se deduplican en orden estable tanto
dentro de cada pack como en el manifest global.
