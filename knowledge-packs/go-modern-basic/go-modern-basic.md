# Go moderno — Base práctica y verificable

## Propósito

Este paquete ofrece una base local para comprender proyectos Go sin conceder permisos de ejecución. Alejandría explica conceptos y procedencia; las skills controladas inspeccionan, validan y ejecutan herramientas solo tras aprobación explícita.

## Estructura de proyectos

Los proyectos Go utilizan archivos `.go`. `go.mod` declara el módulo y sus dependencias, `go.sum` registra sumas verificadas y `go.work` puede agrupar varios módulos locales. Los tests suelen terminar en `_test.go`; los comandos suelen ubicarse bajo `cmd/`.

## Validación determinista del módulo

Elyndra puede leer y validar de forma acotada `go.mod` y `go.work` sin ejecutar el comando `go`. Esta comprobación detecta directivas básicas ausentes, archivos ilegibles y reemplazos locales que salen del proyecto autorizado. No resuelve dependencias ni confirma que el código compile.

## Formato con gofmt

`gofmt -d archivo.go` muestra las diferencias de formato sin modificar el archivo. Elyndra nunca utiliza `gofmt -w` durante una comprobación. Un resultado de formato correcto no demuestra que el programa compile ni que sus tests pasen.

## go vet

`go vet ./...` analiza construcciones sospechosas que el compilador acepta. Puede detectar usos incorrectos de formatos, copias de locks y otros patrones, pero no sustituye tests, revisión de seguridad ni análisis especializado.

## Build y tests

`go build ./...` compila los paquetes sin ejecutar sus tests. `go test -count=1 ./...` ejecuta código del proyecto y requiere aprobación explícita. El modo corto agrega `-short`, pero cada suite decide cómo interpretarlo.

## Red y dependencias

Una verificación local no debe descargar módulos ni toolchains automáticamente. Elyndra ejecuta Go con `GOPROXY=off`, `GOSUMDB=off`, `GOTOOLCHAIN=local` y modo de módulo readonly. Si una dependencia no existe en la caché local, la etapa falla de forma controlada en vez de acceder a la red.

Elyndra no ejecuta `go get`, `go install`, `go generate` ni `go mod tidy` automáticamente.

## Caché temporal

Build, vet y tests pueden escribir cachés. Elyndra dirige `GOCACHE` y `GOTMPDIR` a una carpeta temporal externa que se elimina después de la ejecución. No debe dejar binarios ni cachés dentro del repositorio.

## Seguridad y límites

Elyndra no usa `shell=True`, no acepta flags libres, no instala Go, no descarga módulos, no cambia `go.mod` ni `go.sum` y no persiste nuevas raíces sin autorización. Los perfiles controlan etapas, modo de tests, exclusiones, timeout, límite de archivos y herramientas obligatorias, pero nunca conceden permisos por sí mismos.

## Flujo recomendado

1. Inspeccionar archivos, módulo y framework sin ejecutar código.
2. Validar `go.mod` y `go.work` de forma determinista.
3. Comprobar formato con `gofmt -d`.
4. Ejecutar `go vet` con red desactivada.
5. Compilar todos los paquetes con caché temporal.
6. Ejecutar tests únicamente tras aprobación explícita.
7. Guardar estado, duración, exit code y salida acotada.
