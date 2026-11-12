# Development Guide

## Prerequisites

- Python 3.10+ (for entrypoint logic)
- Podman 3+ or Docker 20.10+ (supporting zstd compressed layers)
- `just` task runner

## Build & Test

```bash
# Build the default 8-player image locally
just build

# Build the vanilla 4-player server without mods
just build-vanilla

# Run full smoke tests (scratch volume boot, A2S query check, plugin stack verification)
just smoke
```

## Contribution Workflow

1. Create a topic branch off `main`.
2. Run `just verify` before committing; `just check` currently performs no validation.
3. Keep documentation aligned with build argument defaults and entrypoint variables.
