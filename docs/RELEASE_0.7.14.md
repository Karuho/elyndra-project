# Elyndra 0.7.14-alpha

## Controlled Swift project toolchain

Elyndra 0.7.14-alpha adds deterministic inspection and controlled verification for Swift source trees, Swift Package Manager projects and Xcode metadata.

Inspection reads source paths, imports and `Package.swift` as bounded UTF-8 data without evaluating the manifest. It reports package metadata, dependency declarations, targets, products, test files, Xcode projects/workspaces, plugins and common Swift libraries. Direct syntax verification uses `swiftc -parse`; format verification uses `swift-format lint --strict` and never rewrites source files.

SwiftPM build and test stages use fixed argument lists, `--disable-automatic-resolution`, an external temporary scratch directory, temporary home/cache/module directories and defensive proxy settings. Projects with remote dependencies require an existing `Package.resolved` before executable stages. SwiftPM may evaluate the manifest and execute plugins or tests, so these stages require explicit approval and are not described as a complete sandbox. Elyndra does not run Xcode builds, package update/resolve commands, arbitrary Swift scripts or automatic toolchain installation.

The release adds per-project Swift profiles, generic verification history, deterministic chat routing, CLI commands, local control-center data, schema version 22 and the optional `programming.swift.modern-basic` Alexandria package.
