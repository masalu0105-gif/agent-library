import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from agent_library import __version__


class VersionTests(unittest.TestCase):
    def test_runtime_and_package_versions_agree(self):
        declared = re.search(r'^version = "([^"]+)"$', (ROOT / 'pyproject.toml').read_text(), re.M).group(1)
        self.assertEqual(__version__, declared)
