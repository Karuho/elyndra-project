# Elyndra 0.7.13-alpha

## Controlled C#/.NET project toolchain

Elyndra 0.7.13-alpha adds deterministic inspection and controlled verification for C#, F# and Visual Basic projects managed by the .NET SDK.

The toolchain inspects `.sln`, `.slnx`, `.csproj`, `.fsproj`, `.vbproj`, `global.json`, central package files and Directory.Build files without executing MSBuild. It detects target frameworks, package references, project references, test files and common frameworks such as ASP.NET Core, Blazor, Entity Framework Core, .NET MAUI, xUnit, NUnit and MSTest.

Executable stages use fixed argument lists. Formatting runs in verify-only mode. Build and tests use `--no-restore`, disable build servers and require .NET SDK 8 or newer so `--artifacts-path` can place all generated output in a temporary directory outside the project. Conventional HTTP proxy variables are redirected to an unavailable loopback endpoint as an additional defense. MSBuild targets and tests can still execute project code and therefore require explicit approval; Elyndra does not describe them as a complete sandbox.

The release adds per-project .NET profiles, generic verification history, deterministic chat routing, CLI commands, local control-center data, schema version 21 and the optional `programming.dotnet.modern-basic` Alexandria package.
