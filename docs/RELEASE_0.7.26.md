# Elyndra 0.7.26-alpha

## Purpose

Make web and CLI responses use local first-aid and translation knowledge before Ollama, and complete the first reviewed preference-learning workflow.

## Included

- Direct emergency phrases such as `me ahogo` route immediately to local first aid.
- A local first-aid catalog answers capability questions without a model.
- Six bundled first-aid cards, including severe trouble breathing.
- Translation request parsing for web and CLI.
- Local dictionary, phrasebook and name templates before model fallback.
- Romanization for stored Chinese and Japanese templates.
- Honest translation capability response.
- Reviewed preference proposals with edit, approval, rejection, scope, expiration and forgetting.
- Bounded approved-preference context for language responses, without permission effects.
- SQLite schema 34 and `reviewed_preferences`.
- Loopback control endpoints for translation and preferences.

## Safety

The release does not download language or medical data, learn preferences silently, grant tools to models, or treat the model as policy authority. Emergency fast paths are deterministic and offline.
