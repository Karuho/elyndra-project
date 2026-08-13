# Elyndra 0.7.5-dev

Elyndra 0.7.5 closes the first controlled frontend-quality toolchain and improves the maintenance of
the public repository documentation.

## Frontend verification

The web pipeline now supports deterministic framework checks plus controlled ESLint and Stylelint
execution. Project-local tools under `node_modules/.bin` take priority. Elyndra does not invoke npm,
`npx`, yarn, pnpm, bun or project scripts.

Web profiles can enable or disable each stage, choose a framework preset and point to validated ESLint
or Stylelint configuration files inside the authorized project.

## Framework inspection

Angular workspaces, Vite configuration, package-manager lockfiles, package scripts and workspace
metadata are inspected without executing project code. Conflicting lockfiles and incomplete framework
metadata are reported separately from blocking errors.

## Alexandria packages

Local knowledge packages can now be created from source files and exported from installed libraries.
The web control center exposes local create, install and export workflows. Every source is copied into
the package, hashed with SHA-256 and revalidated before installation.

## Documentation maintenance

The changelog is backfilled from 0.3.5 through 0.7.5. README, SECURITY and CONTRIBUTING now describe
the current authorization model, controlled toolchains, optional knowledge packages and release
validation requirements.
