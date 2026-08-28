"""Puts filter_plugins/ on the import path, so the filters can be tested as plain Python.

Ansible loads that directory itself (ansible.cfg sets filter_plugins), which is why it is
not a package and cannot simply be imported by name.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "filter_plugins"))
