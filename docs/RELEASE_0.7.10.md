# Elyndra 0.7.10-dev

Elyndra 0.7.10 adds a controlled Go project toolchain and the optional Go knowledge package for Alexandria.

## Controlled Go flow

The Go toolchain inspects modules without execution, validates `go.mod` and `go.work` deterministically, checks formatting with `gofmt -d`, runs `go vet`, builds packages and executes approved tests. Each stage uses explicit authorization, fixed argument lists, bounded output and comparable verification history.

## Offline and readonly execution

Go subprocesses receive `GOPROXY=off`, `GOSUMDB=off`, `GOTOOLCHAIN=local` and readonly module flags. Caches and temporary build data are redirected outside the project and removed after execution. Elyndra never invokes `go get`, `go install`, `go generate` or `go mod tidy` automatically.

## Project profiles and control center

Profiles can enable stages, select short tests, configure exclusions, limits and required tools without granting project authorization. Go profiles and recent verification results are visible in the local control center and remain manageable from the CLI.

## Optional knowledge

`knowledge-packs/go-modern-basic` documents Go module structure, formatting, vet, builds, tests, offline dependency behavior and the boundary between explanation and execution. It is not installed or reviewed automatically.
