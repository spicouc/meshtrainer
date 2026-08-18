# Certified source synchronization

This branch carries the current certified MeshTrainer source snapshot used to bring the public repository forward from the older July 2026 Qwen-only state.

## Authoritative inputs

- Generic distributed / multi-model runtime: certified artifact `a52580c` (`meshtrainer_minicpm5_portability_r1_2_final_a52580c.tar.gz`), SHA-256 `f9a1f415bbd27071c3494d6fb896861a48f8da57f978a473736d1a2000cc6426`.
- Product MVP Phase 1 application code: functional commit `60ebd29`, evidence-only closeout `4696d35`.
- Phase 1 final artifact SHA-256: `004f745950734b88be200854a06624eb27a68d833e8585322034e886bc6286f4`.

## What is synchronized visibly

The `app/` application layer is published as source and is byte-identical for the files present to the certified Phase 1 artifact. `requirements-app.txt` is also the certified file.

## Complete certified source bundle

`certified-source/meshtrainer_certified_source_sync.tar.gz`

SHA-256:

```
e3274b2840253adef11715e7a8a8dadc02d983285feae376d9fd4393dbc070d1
```

The bundle contains the current certified runtime/core dependency closure, Qwen3/MiniCPM5/Dummy backends, Product MVP Phase 1 application source, tests/gates, requirements, and the approved Product MVP design/architecture documents.

It intentionally excludes model weights, generated checkpoints/adapters, runtime databases, secrets/tokens, virtual environments, `__pycache__`, and host-specific execution evidence.

## Publication note

GitHub's connected-app write path can store the exact binary source bundle and text blobs but cannot server-side unpack a tarball into a Git tree, and app-authored pushes in this repository did not trigger the temporary extraction workflow. The bundle therefore remains the authoritative complete source snapshot on this branch while the expanded tree is synchronized through normal Git from the development host.
