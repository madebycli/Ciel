# Character System

Character packs live under `characters/<id>/`.

A character pack owns data such as:

- display name and identity
- system prompt
- visual theme
- voice configuration
- enabled service ids
- future animation and asset references

Character packs do not own ChatEngine, model provider implementations, desktop APIs or arbitrary executable hooks.

Adding a new character should require data and assets, not a new runtime fork.
