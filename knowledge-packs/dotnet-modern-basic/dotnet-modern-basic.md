# C# y .NET modernos — Base práctica y verificable

## Propósito

Este paquete introduce una base local para comprender proyectos C# y .NET sin conceder permisos de ejecución. Alejandría explica conceptos y procedencia; las skills controladas inspeccionan, validan, compilan y ejecutan tests solo tras aprobación explícita.

## SDK, runtime y proyectos

El SDK de .NET incluye el comando `dotnet`, compiladores, MSBuild y herramientas de desarrollo. El runtime ejecuta aplicaciones ya compiladas. Un archivo `.csproj`, `.fsproj` o `.vbproj` describe un proyecto; una solución `.sln` o `.slnx` agrupa varios proyectos.

Los archivos MSBuild son XML, pero pueden declarar targets y tareas que ejecutan código durante build o tests. Inspeccionar el XML como datos no equivale a ejecutar MSBuild.

## Target frameworks

`TargetFramework` y `TargetFrameworks` declaran los marcos objetivo, por ejemplo `net8.0`. Una solución puede contener proyectos con marcos diferentes. El framework objetivo condiciona APIs disponibles, paquetes y runtime requerido.

## Dependencias y restore

NuGet administra paquetes. `dotnet restore` puede acceder a fuentes remotas y modificar archivos intermedios. Elyndra no ejecuta restore automáticamente. Build, formato y tests usan `--no-restore`; si faltan assets o paquetes locales, la etapa falla de forma controlada.

## Formato y analizadores

`dotnet format --verify-no-changes` comprueba estilo y analizadores sin aplicar cambios. Puede cargar analizadores ya restaurados y declarados por el proyecto. Elyndra no usa opciones de corrección automática.

## Build seguro

`dotnet build` ejecuta MSBuild y puede cargar targets, tareas, generadores y analizadores del proyecto. Elyndra requiere aprobación, desactiva el restore implícito y, con SDK .NET 8 o superior, dirige todos los artefactos a una carpeta temporal externa mediante `--artifacts-path`.

## Tests

`dotnet test` compila y ejecuta suites como xUnit, NUnit o MSTest. Los tests ejecutan código del proyecto y sus dependencias, por lo que requieren aprobación explícita. Un resultado correcto solo demuestra que pasaron los casos cubiertos.

## Frameworks habituales

ASP.NET Core se usa para servicios y aplicaciones web. Blazor permite interfaces web con .NET. Entity Framework Core ofrece acceso a datos. .NET MAUI se usa en aplicaciones multiplataforma. Detectar estas referencias no instala paquetes ni ejecuta el proyecto.

## Perfiles y autorización

Un perfil .NET puede elegir etapas, configuración Debug o Release, límites, exclusiones, fail-fast y política de herramientas obligatorias. El perfil no concede acceso. El proyecto debe estar dentro de una raíz persistente, ser confiable o recibir autorización puntual.

## Límites de confianza

Elyndra no ejecuta `dotnet restore`, `dotnet tool restore`, `dotnet run`, `dotnet publish`, instalaciones de workloads ni comandos arbitrarios. Las variables de proxy convencionales se redirigen de forma defensiva, pero esto no constituye aislamiento de red y MSBuild no se presenta como sandbox completo.

## Flujo recomendado

1. Inspeccionar proyectos, soluciones y frameworks sin ejecutar MSBuild.
2. Validar XML, `global.json` y estructura de solución como datos.
3. Comprobar formato sin aplicar cambios.
4. Compilar sin restore y con artefactos externos.
5. Ejecutar tests únicamente tras aprobación.
6. Guardar estado, duración y resultados acotados en el historial.
