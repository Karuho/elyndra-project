# Elyndra 0.7.8-dev

Elyndra 0.7.8 adds a controlled C and C++ toolchain and corrects the Java verification behavior observed with dependency-heavy Maven projects such as Minecraft plugins.

## Java classpath correction

A raw `javac` invocation does not automatically receive Maven or Gradle dependencies. This produced a false project-level failure when `javac` could not resolve Bukkit or Paper classes even though the Maven build and tests passed.

The complete Java flow now skips raw `javac` by default when Maven or Gradle is the detected build system. The build and test stages remain authoritative because they own the project classpath. Direct `javac -proc:none` remains available as an explicit command and remains the default for standalone Java projects.

## Controlled C and C++ flow

The native toolchain can inspect source trees, validate CMake metadata without executing it, run direct GCC or Clang syntax checks, execute cppcheck, configure and build CMake in a temporary directory and run CTest after approval.

Direct syntax stages are skipped by default for CMake projects because the build system can supply generated headers, include paths and definitions that a raw compiler invocation does not know. Users may enable them explicitly when needed.

## Safety boundaries

- No shell or arbitrary command execution.
- No Make or Meson execution.
- Fixed compiler, CMake, CTest and cppcheck arguments.
- Temporary build output outside the repository.
- Bounded timeout and output.
- Explicit approval and project authorization.
- No automatic installation or download of tools.
- Optional Alexandria knowledge remains separate from permissions.

## Optional knowledge

`knowledge-packs/c-cpp-modern-basic` documents source layout, syntax-only compilation, CMake, cppcheck, CTest and the limits of direct compiler checks. It is not installed or reviewed automatically.
