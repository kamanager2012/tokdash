"""Ensure install.sh CLI shim does not hardcode a developer home path."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestInstallShim(unittest.TestCase):
    def test_install_sh_has_no_hardcoded_home(self) -> None:
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertNotIn("/home/jamesoldman", text)
        self.assertIn('exec python3 "$DIR/usage.30s.py"', text)
        # Unquoted heredoc so $DIR expands at install time; \$@ becomes "$@" in shim.
        self.assertRegex(text, r'cat << CLI_EOF > "\$BIN_DIR/cognitally"')

    def test_package_json_points_at_tokdash_repo(self) -> None:
        text = (ROOT / "package.json").read_text(encoding="utf-8")
        self.assertIn("github.com/kamanager2012/tokdash", text)
        self.assertNotIn("github.com/kamanager2012/cognitally.git", text)


if __name__ == "__main__":
    unittest.main()
