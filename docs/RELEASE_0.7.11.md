# Elyndra 0.7.11-dev

Elyndra 0.7.11 adds a controlled Rust project toolchain and the optional Rust knowledge package for Alexandria.

## Controlled Rust flow

The Rust toolchain inspects `Cargo.toml` without execution, validates manifests deterministically, checks formatting without rewriting files, runs `cargo check`, Clippy and approved tests, and stores comparable verification history.

## Offline and immutable execution

Cargo stages use fixed arguments with `--offline` and `--locked`. Build output is redirected to a temporary target directory outside the project. Elyndra does not install toolchains or components and never invokes `cargo install`, `cargo update`, `cargo fix` or arbitrary commands.

Cargo check, Clippy and tests may execute build scripts or procedural macros. They therefore require explicit project authorization and approval. Projects without `Cargo.lock` are not modified; execution stages are skipped rather than creating a lockfile.

## Project profiles and control center

Rust profiles can enable stages, select default or all features, configure exclusions and limits, and require tools without granting project authorization. Rust profiles and recent verification results are visible in the local control center and remain manageable from the CLI.

## Optional knowledge

`knowledge-packs/rust-modern-basic` documents Cargo manifests, workspaces, rustfmt, check, Clippy, tests, build scripts, procedural macros and offline execution boundaries. It is not installed or reviewed automatically.
