# Ciel Character Packs

Ein Character-Pack verändert Identität und Darstellung, nicht den vertrauenswürdigen Ciel-Core.

## Struktur

```text
characters/<id>/
  character.json
  prompt.md
  voice/        optional
  ui/           optional
```

`character.json` ist deklarativ. Character-Packs dürfen keinen Python-Code automatisch ausführen.

## Minimalbeispiel

```json
{
  "schema_version": 1,
  "id": "example",
  "display_name": "Example",
  "description": "Example profile",
  "prompt_file": "prompt.md",
  "voice": {"engine": "inherit"},
  "theme": {"accent": "#ffffff", "background": "#000000"},
  "services": ["chat", "voice", "memory", "overlay"]
}
```

Verfügbare Services werden zentral in `great_sage/services/registry.py` definiert. Ein Pack kann nur bekannte Services auswählen.

Assets wie Sprachsamples, Bilder und Animationen müssen selbst erstellt, lizenziert oder lokal vom Nutzer bereitgestellt werden. Sie gehören nicht automatisch zum Ciel-Core.
