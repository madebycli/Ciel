# AI Context Route

```yaml
schema_version: 1
context_repo: https://github.com/madebycli/master-context
project_id: ciel
source_repo: https://github.com/madebycli/Ciel
context_root: projects/ciel/
entrypoint: projects/ciel/INDEX.md
```

## Mandatory AI behavior

This file is the authoritative context route for this repository.

1. Do not guess another context path.
2. Do not scan sibling project folders in the master-context repository.
3. Validate this mapping against `REGISTRY.yaml` when available.
4. If validation fails, stop and report the mismatch.
5. Read the declared `entrypoint` before loading additional project context.
6. Follow only task-relevant graph links from the active project.
7. Cross-project context requires an explicit cross-project link or explicit user instruction.
8. Durable private AI context belongs in the master-context repository, not in ad hoc files in this code repository.
