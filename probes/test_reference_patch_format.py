import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from reference_patch_format import append_terminal_lf,create_variant


class TerminalLfTests(unittest.TestCase):
    def test_preserves_blank_existing_newlines_and_no_newline_marker(self):
        for patch in ['', ' \t', 'diff\n', 'diff\n\n']:
            self.assertEqual(append_terminal_lf(patch),patch)
        patch='diff\n\\ No newline at end of file'
        self.assertEqual(append_terminal_lf(patch),patch+'\n')

    def test_real_git_parser_reproduces_missing_terminal_lf_failure(self):
        patch='diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new'
        def parse(s):return subprocess.run(['git','apply','--numstat','-'],input=s.encode(),capture_output=True)
        self.assertNotEqual(parse(patch).returncode,0)
        self.assertEqual(parse(append_terminal_lf(patch)).returncode,0)
        self.assertEqual(append_terminal_lf(append_terminal_lf(patch)),patch+'\n')

    def test_variant_retains_source_and_metadata_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'source.jsonl';target=root/'variant.jsonl';manifest=root/'manifest.json'
            rows=[dict(instance_id='repo__case-1',model_name_or_path='label',model_patch='diff',extra='keep')]
            original=(json.dumps(rows[0])+'\n').encode();source.write_bytes(original)
            result=create_variant(source,target,manifest)
            self.assertEqual(source.read_bytes(),original)
            actual=json.loads(target.read_text());self.assertEqual(actual,dict(rows[0],model_patch='diff\n'))
            self.assertEqual(result['changed_patch_count'],1)
            self.assertFalse(result['hunk_headers_or_code_lines_modified'])
            with self.assertRaises(ValueError):create_variant(source,target,manifest)


if __name__=='__main__':unittest.main()
