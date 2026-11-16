"""File-safety regression tests for the installer; no network or AI calls."""

import contextlib
import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from bootstrap_ai import git_blob_sha, install


class InstallerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.destination = Path(self.temporary.name) / 'skills'
        self.skill_text = b'---\nname: example\n---\nReviewed skill.\n'
        self.skill = {'name': 'example', 'path': 'skills/.curated/example',
                      'skill_md_blob_sha': git_blob_sha(self.skill_text)}
        self.manifest = {'source': {'repository': 'https://example.invalid/repo',
                                    'commit': 'a' * 40}}

    def archive(self, extra=None):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('repo/skills/.curated/example/SKILL.md', self.skill_text)
            archive.writestr('repo/skills/.curated/example/references/guide.md', b'guide')
            for name, content in extra or []:
                archive.writestr(name, content)
        return output.getvalue()

    def run_install(self, archive):
        with contextlib.redirect_stdout(io.StringIO()):
            install(archive, self.manifest, [self.skill], self.destination)

    def test_repeat_preserves_reviewed_files(self):
        archive = self.archive()
        self.run_install(archive)
        before = (self.destination / 'example/references/guide.md').stat().st_mtime_ns
        self.run_install(archive)
        self.assertEqual(before, (self.destination / 'example/references/guide.md').stat().st_mtime_ns)
        receipt = json.loads((self.destination / 'example/.ips-skill-source.json').read_text())
        self.assertEqual(receipt['commit'], 'a' * 40)

    def test_modified_installation_is_not_overwritten(self):
        self.run_install(self.archive())
        target = self.destination / 'example/references/guide.md'
        target.write_text('personal edits')
        with self.assertRaisesRegex(ValueError, 'modified installation'):
            self.run_install(self.archive())
        self.assertEqual(target.read_text(), 'personal edits')

    def test_unmanaged_directory_is_not_overwritten(self):
        target = self.destination / 'example'
        target.mkdir(parents=True)
        (target / 'SKILL.md').write_text('my existing skill')
        with self.assertRaisesRegex(ValueError, 'unmanaged'):
            self.run_install(self.archive())
        self.assertEqual((target / 'SKILL.md').read_text(), 'my existing skill')

    def test_traversal_is_rejected_before_any_write(self):
        archive = self.archive([('repo/skills/.curated/example/../../escape', b'bad')])
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.run_install(archive)
        self.assertFalse(self.destination.exists())

    def test_symlink_is_rejected_before_any_write(self):
        member = zipfile.ZipInfo('repo/skills/.curated/example/references/link')
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive = self.archive([(member, b'../../escape')])
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.run_install(archive)
        self.assertFalse(self.destination.exists())

    def test_wrong_pin_is_rejected_before_any_write(self):
        self.skill['skill_md_blob_sha'] = '0' * 40
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.run_install(self.archive())
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
