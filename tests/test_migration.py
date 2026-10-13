import importlib.util
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('migrate',Path(__file__).resolve().parents[1]/'scripts'/'migrate-local.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MigrationTests(unittest.TestCase):
    def test_retarget_keeps_job_name_and_steps(self):
        text='name: CI\non: [push, pull_request]\njobs:\n  verify:\n    runs-on: ubuntu-latest\n    steps:\n      - run: npm test\n'
        changed,warnings=module.migrate(text)
        self.assertFalse(warnings)
        self.assertIn('  verify:\n',changed)
        self.assertIn('      - run: npm test\n',changed)
        self.assertIn(module.GUARD+module.RUNNER,changed)
        self.assertEqual(module.migrate(changed)[0],changed)

    def test_existing_condition_is_not_destroyed(self):
        text='jobs:\n  build:\n    if: always()\n    runs-on: ubuntu-latest\n    steps: []\n'
        changed,warnings=module.migrate(text)
        self.assertEqual(changed,text)
        self.assertEqual(len(warnings),1)

    def test_windows_matrix_and_reusable_are_not_changed(self):
        text='jobs:\n  windows:\n    runs-on: windows-latest\n  matrix:\n    runs-on: ${{ matrix.os }}\n  call:\n    uses: o/r/.github/workflows/a.yml@main\n'
        self.assertEqual(module.migrate(text)[0],text)

    def test_privileged_pr_target_is_not_changed(self):
        text='on: pull_request_target\njobs:\n  build:\n    runs-on: ubuntu-latest\n'
        self.assertEqual(module.migrate(text)[0],text)


if __name__=='__main__': unittest.main()
