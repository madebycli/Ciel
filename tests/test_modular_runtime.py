import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from great_sage.characters.loader import CharacterLoadError, load_profile
from great_sage.hardware.accelerator import detect_accelerator
from great_sage.services.registry import validate_services


class CharacterTests(unittest.TestCase):
    def make_pack(self, root: Path, prompt_file="prompt.md"):
        pack = root / "test"
        pack.mkdir()
        (pack / "prompt.md").write_text("hello", encoding="utf-8")
        (pack / "character.json").write_text(json.dumps({
            "schema_version": 1,
            "id": "test",
            "display_name": "Test",
            "prompt_file": prompt_file,
            "services": ["chat", "voice", "chat"]
        }), encoding="utf-8")

    def test_loads_and_deduplicates_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_pack(root)
            profile = load_profile("test", root)
            self.assertEqual(profile.services, ("chat", "voice"))
            self.assertEqual(profile.read_prompt(), "hello")

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_pack(root, "../secret.md")
            (root / "secret.md").write_text("secret", encoding="utf-8")
            with self.assertRaises(CharacterLoadError):
                load_profile("test", root)

    def test_service_allowlist(self):
        with self.assertRaises(ValueError):
            validate_services(("chat", "run_arbitrary_python"))


class AcceleratorTests(unittest.TestCase):
    def test_cpu_can_be_forced_without_torch(self):
        with patch.dict(os.environ, {"CIEL_ACCELERATOR": "cpu"}):
            acc = detect_accelerator()
        self.assertEqual(acc.backend, "cpu")
        self.assertEqual(acc.torch_device, "cpu")


if __name__ == "__main__":
    unittest.main()
