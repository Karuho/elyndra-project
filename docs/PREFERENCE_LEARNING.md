# Reviewed preference learning

Elyndra can detect or receive preference proposals, but it never learns them silently.

## Lifecycle

`observed or explicit preference → pending proposal → owner edit/review → approve or reject → durable memory → optional expiration or forgetting`

A reviewed preference records category, scope, optional project, confidence, provenance and optional expiration. Approval creates a semantic memory of kind `preference`. Active reviewed global preferences are added to the bounded language context as guidance, explicitly without granting permissions or overriding safety and evidence. Expiration or forgetting logically deletes both the preference and its linked memory.

## Boundaries

- No approval is inferred from conversation.
- The model cannot approve preferences.
- Sensitive or temporary observations are not automatically promoted.
- Project preferences do not become global preferences.
- Expired preferences are not retrieved as active memory.

## CLI

```bash
./scripts/elyndra-dev preferences status
./scripts/elyndra-dev preferences propose "Prefiero rutas exactas" --category style
./scripts/elyndra-dev preferences proposals
./scripts/elyndra-dev preferences edit ID "Prefiero rutas exactas y bloques pequeños" --approve
./scripts/elyndra-dev preferences approve ID --approve
./scripts/elyndra-dev preferences list
./scripts/elyndra-dev preferences forget ID_PUBLICO --approve
```
