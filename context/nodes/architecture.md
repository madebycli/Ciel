# Architecture

The runtime is split into stable subsystems with narrow boundaries:

- `great_sage/characters`: data-only character profiles
- `great_sage/services`: capability registry and service policy
- `great_sage/context_graph`: bounded context retrieval
- `great_sage/hardware`: accelerator selection
- `great_sage/platform`: Linux and Wayland environment integration
- `great_sage/core`: provider-independent chat and reasoning logic
- `great_sage/ui/linux`: native GTK and Wayland hosts
- `legacy/windows`: reference only

Do not add character-specific branches to the core. Do not let a character manifest import Python modules. Do not make the context database the source of truth.
