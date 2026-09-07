#!/usr/bin/env python3
"""Standalone test runner.

pytest is the normal way to run these, but it is not packaged on every Debian or
Raspberry Pi OS install and there is no pip in some environments. This runs the
same test functions with nothing but the standard library.
"""

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODULES = ['tests.test_golden', 'tests.test_config', 'tests.test_scanner', 'tests.test_frequency',
           'tests.test_tuning']


def main() -> int:
	passed = failed = 0

	for module_name in MODULES:
		try:
			module = importlib.import_module(module_name)
		except ImportError as exc:
			print(f'  SKIP {module_name}: {exc}')
			continue

		for name in sorted(dir(module)):
			if not name.startswith('test_'):
				continue

			try:
				getattr(module, name)()
				print(f'  PASS {module_name}.{name}')
				passed += 1
			except Exception:
				print(f'  FAIL {module_name}.{name}')
				traceback.print_exc()
				failed += 1

	print(f'\n{passed} passed, {failed} failed')

	return 1 if failed else 0


if __name__ == '__main__':
	sys.exit(main())
