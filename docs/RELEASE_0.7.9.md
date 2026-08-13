# Elyndra 0.7.9-dev

Elyndra 0.7.9 adds a controlled Ruby project toolchain and the optional Ruby knowledge package for Alexandria.

## Controlled Ruby flow

The Ruby toolchain can inspect source trees and Gem metadata without execution, validate UTF-8 descriptors without evaluating them, run `bundle check`, check syntax with `ruby -c`, run RuboCop without autocorrection and execute approved RSpec or Minitest suites.

Each stage uses a fixed argument list, bounded output, timeout, explicit approval and the existing project authorization policy. Results are stored in the generic verification history and can be compared across runs of the same project.

## Tool resolution

Elyndra prefers allowlisted project binstubs under `bin/`, then existing local tool locations and finally global tools. It never executes arbitrary binstubs, Rake tasks, `bundle install`, `bundle update` or package installation commands.

## Safety boundaries

- No shell or unrestricted command execution.
- No automatic gem installation or update.
- No RuboCop autocorrection.
- Descriptor inspection never evaluates Gemfile or gemspec code.
- RSpec and Minitest require explicit approval because they execute project code.
- Profiles store safe defaults but never grant authorization.
- Output, runtime and file counts remain bounded.

## Optional knowledge

`knowledge-packs/ruby-modern-basic` documents Ruby project structure, Bundler, syntax checks, RuboCop, RSpec, Minitest, Rails-related boundaries and the difference between explanation and execution. The package is not installed or reviewed automatically.
