import hashlib,io,shutil,subprocess,tempfile,tarfile,unittest
from pathlib import Path

from batch_patch import apply,sections
import checked_patch
from checked_pipeline import copy_applicator


class UnifiedPatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.repo=self.root/'repo';self.repo.mkdir()
        self.git('init');self.git('config','core.autocrlf','false')
        (self.repo/'code.py').write_bytes(b'value = "old"\n')
        self.git('add','code.py');self.commit();self.base=self.git('rev-parse','HEAD').strip()
        self.patch=self.root/'prediction.diff'
        self.patch.write_bytes(b'--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n-value = "old"\n+value = "fixed"\n')
    def tearDown(self):self.temp.cleanup()
    def git(self,*args):return subprocess.check_output(['git',*args],cwd=self.repo,text=True,stderr=subprocess.DEVNULL)
    def commit(self):self.git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-m','fixture')

    def test_plain_unified_is_applied_without_repairing_prediction_bytes(self):
        original=self.patch.read_bytes();result=apply(self.repo,self.patch,self.base)
        self.assertTrue(result['applied'],result);self.assertEqual(self.patch.read_bytes(),original)
        self.assertEqual(result['prediction_sha256'],hashlib.sha256(original).hexdigest())
        self.assertEqual((self.repo/'code.py').read_bytes(),b'value = "fixed"\n')
        self.assertFalse(result['prediction_bytes_modified'])

    def test_legacy_default_still_rejects_plain_unified_and_keeps_its_existing_contract(self):
        result=checked_patch.apply(self.repo,self.patch,self.base)
        self.assertFalse(result['applied'])
        self.assertEqual((self.repo/'code.py').read_bytes(),b'value = "old"\n')

    def test_malformed_hunk_count_is_an_input_error_and_is_not_auto_recounted(self):
        self.patch.write_bytes(self.patch.read_bytes().replace(b'@@ -1 +1 @@',b'@@ -1,9 +1,9 @@'))
        result=apply(self.repo,self.patch,self.base)
        self.assertFalse(result['applied']);self.assertIn('syntax validation',result['error'])
        self.assertEqual((self.repo/'code.py').read_bytes(),b'value = "old"\n')

    def test_new_unicode_file_and_deleted_file_are_both_tracked(self):
        name='testim\u00e4ge.py'
        self.patch.write_bytes(('--- /dev/null\n+++ b/'+name+'\n@@ -0,0 +1 @@\n+new = True\n'
            '--- a/code.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-value = "old"\n').encode())
        result=apply(self.repo,self.patch,self.base)
        self.assertTrue(result['applied'],result)
        self.assertFalse((self.repo/'code.py').exists())
        self.assertEqual((self.repo/name).read_bytes(),b'new = True\n')
        self.assertEqual(set(result['paths']),{name,'code.py'})

    def test_hunk_content_that_looks_like_file_headers_is_not_a_second_file(self):
        (self.repo/'code.py').write_bytes(b'-- a/looks_like_header\n')
        self.git('add','code.py');self.commit();self.base=self.git('rev-parse','HEAD').strip()
        self.patch.write_bytes(b'--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n--- a/looks_like_header\n+++ b/looks_like_header\n')
        result=apply(self.repo,self.patch,self.base)
        self.assertTrue(result['applied'],result)
        self.assertEqual(set(result['paths']),{'code.py'})

    def test_traversal_cannot_create_files_outside_the_repo(self):
        self.patch.write_bytes(b'--- /dev/null\n+++ b/../escape\n@@ -0,0 +1 @@\n+bad\n')
        self.assertFalse(apply(self.repo,self.patch,self.base)['applied'])
        self.assertFalse((self.root/'escape').exists())

    def test_batch_archive_contains_adapter_and_unmodified_base_without_shared_tar(self):
        data={}
        class Container:
            def put_archive(self,path,payload):
                with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                    data.update({name:archive.extractfile(name).read() for name in archive.getnames()})
                return True
        base=Path(checked_patch.__file__)
        copy_applicator(Container(),Path(__file__),{'checked_patch_base.py':base})
        self.assertEqual(set(data),{'checked_patch.py','checked_patch_base.py'})
        self.assertEqual(data['checked_patch_base.py'],base.read_bytes())


if __name__=='__main__':unittest.main()
