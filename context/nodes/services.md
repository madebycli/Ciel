# Service System

Services are reusable capabilities such as chat, voice, memory, desktop control, screen access, reminders, overlay and web access.

Character manifests select from a central allowlist. Unknown service ids are rejected at load time.

Security boundary: model output and character data never become arbitrary shell commands. OS actions remain validated service operations with explicit inputs.
