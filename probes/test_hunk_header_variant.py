import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hunk_header_variant import create_variant,recount,repair,signature,syntax,verify_header_only

GOOD=b'--- a/code.py\n+++ b/code.py\n@@ -1 +1,2 @@ function\n-old = 1\n+new = 2\n+extra = True\n'
BAD=GOOD.replace(b'@@ -1 +1,2 @@',b'@@ -1,9 +1,7 @@')


class HunkHeaderVariantTests(unittest.TestCase):
    def test_repair_changes_only_counts_and_real_git_applies_the_exact_body(self):
        after,info=repair(BAD)
        self.assertEqual(info['status'],'recounted');self.assertTrue(syntax(after)[0])
        self.assertTrue(verify_header_only(BAD,after));self.assertEqual(info['changes'][0]['old_start'],1)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'code.py').write_bytes(b'old = 1\n')
            p=subprocess.run(['git','-c','core.autocrlf=false','apply','-'],input=after,cwd=root,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual((root/'code.py').read_bytes(),b'new = 2\nextra = True\n')

    def test_already_valid_empty_and_idempotent_inputs_are_not_normalized(self):
        self.assertEqual(repair(GOOD)[0],GOOD)
        after,_=repair(BAD);self.assertEqual(repair(after)[0],after)
        # A valid hunk body can look like file headers; do not reinterpret it.
        looks=b'--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n--- a/example\n+++ b/example\n'
        self.assertTrue(syntax(looks)[0]);self.assertEqual(repair(looks)[0],looks)

    def test_start_positions_suffix_and_crlf_unicode_bytes_are_preserved(self):
        raw=('--- a/code.py\r\n+++ b/code.py\r\n@@ -17,8 +19,9 @@ f\u00fcnction\r\n-a\r\n+b\r\n'
             '@@ -30,8 +32,9 @@ other\r\n c\r\n-d\r\n+e\r\n').encode()
        after,edits=recount(raw)
        self.assertEqual(len(edits),2);self.assertEqual(signature(raw),signature(after))
        self.assertIn(b'@@ -17,1 +19,1 @@ f\xc3\xbcnction\r\n',after)
        self.assertIn(b'@@ -30,2 +32,2 @@ other\r\n',after)

    def test_new_file_deletion_and_no_newline_marker_are_supported(self):
        new=b'--- /dev/null\n+++ b/code.py\n@@ -0,0 +1,8 @@\n+abc\n\\ No newline at end of file\n'
        after,_=recount(new);self.assertIn(b'@@ -0,0 +1,1 @@',after)
        deleted=b'--- a/code.py\n+++ /dev/null\n@@ -1,8 +0,0 @@\n-abc\n'
        self.assertIn(b'@@ -1,1 +0,0 @@',recount(deleted)[0])

    def test_explicit_git_boundaries_support_multiple_files(self):
        raw=(b'diff --git a/code.py b/code.py\nindex 12345..67890 100644\n'+BAD+
             b'diff --git a/other.py b/other.py\n'+BAD.replace(b'code.py',b'other.py'))
        after,changes=recount(raw);self.assertEqual(len(changes),2)
        self.assertTrue(syntax(after)[0]);self.assertEqual(signature(raw),signature(after))

    def test_ambiguous_plain_multi_file_headers_are_not_guessed(self):
        raw=BAD+BAD.replace(b'code.py',b'other.py')
        after,info=repair(raw);self.assertEqual(after,raw)
        self.assertEqual(info['status'],'unrepairable_counts_only');self.assertIn('Ambiguous',info['reason'])

    def test_incomplete_unsupported_unsafe_and_ambiguous_content_is_rejected(self):
        samples=[BAD[:-1],BAD+b'```\n',BAD+b'\n',BAD.replace(b'+extra = True\n',b'extra = True\n'),
            BAD.replace(b'code.py',b'../escape.py'),BAD.replace(b'@@ -1,9',b'@@@ -1,9'),
            b'--- a/code.py\n+++ b/code.py\n@@ -1,8 +1,9 @@\n\\ No newline at end of file\n-old\n+new\n',
            BAD+BAD[BAD.index(b'@@ '):],BAD.replace(b'@@ -1,9',b'@@ -0,9')]
        for raw in samples:
            with self.subTest(raw=raw):
                after,info=repair(raw);self.assertEqual(after,raw)
                self.assertEqual(info['status'],'unrepairable_counts_only')

    def test_byte_guard_rejects_code_paths_starts_or_context_changes(self):
        for altered in [BAD.replace(b'extra = True',b'extra = False'),BAD.replace(b'code.py',b'evil.py'),
                        BAD.replace(b'@@ -1,9',b'@@ -2,9'),BAD.replace(b'@@ function',b'@@ other')]:
            with self.assertRaises(ValueError):verify_header_only(BAD,altered)

    def test_variant_keeps_source_metadata_rows_and_eligible_scope_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'raw.jsonl';target=root/'variant.jsonl';manifest=root/'manifest.json'
            rows=[dict(instance_id='repo__case-'+str(i),model_name_or_path='same',model_patch=body,extra={'keep':i})
                  for i,body in enumerate([BAD.decode(),BAD.decode(),GOOD.decode(),''])]
            raw=''.join(json.dumps(r)+'\n' for r in rows).encode();source.write_bytes(raw)
            result=create_variant(source,target,manifest,['repo__case-0'])
            self.assertEqual(source.read_bytes(),raw);self.assertEqual(result['changed_patch_count'],1)
            actual=[json.loads(s) for s in target.read_text(encoding='utf-8').splitlines()]
            self.assertNotEqual(actual[0]['model_patch'],rows[0]['model_patch'])
            self.assertEqual(actual[0]['extra'],rows[0]['extra']);self.assertEqual(actual[1:],rows[1:])
            with self.assertRaises(ValueError):create_variant(source,target,manifest)

    def test_broken_git_environment_is_not_mislabeled_as_bad_predictions(self):
        with tempfile.TemporaryDirectory() as d,patch('hunk_header_variant.syntax',return_value=(False,'broken')):
            with self.assertRaises(RuntimeError):create_variant(Path(d)/'raw',Path(d)/'variant',Path(d)/'manifest')


if __name__=='__main__':unittest.main()
