# Elyndra 0.8.4-alpha

This release adds an optional local scheduler around the existing policy-bounded automation dispatcher.

The scheduler may run attached to a CLI terminal or inside the active loopback web runtime. An exclusive private lock prevents duplicate processes. Durable sessions record heartbeats and clean shutdown, while one-shot cycles remain available for testing and manual use.

Prepared local inbox results can now be copied once into a bounded local-notification table. CLI sessions can print them, and the Personal web workspace can display browser notifications while open and explicitly permitted. No remote delivery is introduced.

SQLite schema 45 adds scheduler sessions and local notifications. Existing schema-44 organizer, wellbeing, coaching and automation data remains intact. Skills remain at 102.
