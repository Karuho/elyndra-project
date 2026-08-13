# Elyndra 0.7.7-dev

Elyndra 0.7.7 adds a controlled Java/JVM project toolchain while preserving the same local-first authorization, audit and bounded-process model used by the PHP, frontend and Python toolchains.

## Controlled Java/JVM flow

The new verification pipeline can inspect a project, validate `pom.xml` and Gradle descriptor presence, compile source with `javac -proc:none`, run an offline Maven or Gradle build, execute approved tests and store a comparable verification summary.

Maven and Gradle are invoked only through fixed global binaries and fixed arguments. Elyndra does not execute `mvnw`, `gradlew`, arbitrary goals, arbitrary tasks or package installers. Offline mode prevents dependency downloads; missing local artifacts produce a controlled failure.

Direct compilation writes class files to a temporary directory and disables annotation processors. The project tree is not modified.

## Profiles and history

Java profiles can select enabled stages, build tool, Java release, fail-fast behavior, required tools, scan limits, exclusions, timeout and output limits. Profiles do not grant access. Authorization remains persistent only for configured or trusted roots and one-time elsewhere.

Verification runs use the existing generic history repository and can be listed, reported and compared from the CLI or control-center API.

## Optional Alexandria package

The repository includes `knowledge-packs/java-modern-basic`, a local optional package covering project structure, `javac`, Maven, Gradle, tests, profiles and authorization boundaries. It is not installed automatically and remains unreviewed until the owner approves its sources.
