import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.environ.get("GRAPHOLOG_RUST_BIN"),"set GRAPHOLOG_RUST_BIN to test native binary")
class RustIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.binary=os.environ["GRAPHOLOG_RUST_BIN"]
    def tearDown(self):self.temp.cleanup()
    def run_cli(self,*args):
        return subprocess.run([self.binary,*map(str,args)],capture_output=True,text=True,cwd=self.root)
    def test_scan_controls_links_limits_and_no_execution(self):
        (self.root/"x.py").write_text("open('NEVER_EXECUTE','w').write('bad')\n")
        (self.root/"binary.bin").write_bytes(b"\x00ab")
        (self.root/"loop").symlink_to(self.root,target_is_directory=True)
        (self.root/"big.ts").write_text("const x=1;\n"*100)
        result=self.run_cli("scan",self.root,"--format","json","--max-bytes","100")
        self.assertEqual(result.returncode,0)
        files={f["path"]:f for f in json.loads(result.stdout)["files"]}
        self.assertEqual(files["loop"]["status"],"symlink")
        self.assertEqual(files["big.ts"]["status"],"size-limit")
        self.assertEqual(files["binary.bin"]["status"],"binary")
        self.assertFalse((self.root/"NEVER_EXECUTE").exists())
        self.assertEqual(self.run_cli("scan",self.root,"--max-bytes","100","--strict").returncode,2)
    def test_relative_root_import_and_call(self):
        (self.root/"main.js").write_text("import {work} from './util.js'; work(2);\n")
        (self.root/"util.js").write_text("export function work(x) {return x+1;}\n")
        data=json.loads(self.run_cli("scan",self.root,"--format","json").stdout)
        self.assertEqual(data["files"][0]["imports"][0]["targets"],["util.js"])
        self.assertEqual(data["files"][0]["calls"][0]["target"],"util.js::work@1")
    def test_diff_exit_codes_and_no_overwrite(self):
        a,b=self.root/"a",self.root/"b";a.mkdir();b.mkdir()
        (a/"f.py").write_text("x=1\n");(b/"f.py").write_text("x=2\n")
        self.assertEqual(self.run_cli("diff",a,b,"--check").returncode,1)
        self.assertEqual(self.run_cli("scan",a,"-o",a/"f.py").returncode,2)
        self.assertEqual((a/"f.py").read_text(),"x=1\n")
        self.assertEqual(self.run_cli("diff",a,a,"--check").returncode,0)
    def test_terminal_escaping_and_determinism(self):
        (self.root/"bad\x1b[2J.js").write_text("const x=1;\n")
        report=self.run_cli("scan",self.root).stdout
        self.assertNotIn("\x1b",report)
        one=self.run_cli("scan",self.root,"--format","json").stdout
        two=self.run_cli("scan",self.root,"--format","json").stdout
        self.assertEqual(one,two)

    def test_literals_indentation_and_fstrings_remain_distinct(self):
        a,b=self.root/'a',self.root/'b';a.mkdir();b.mkdir()
        cases=[('number.py','def f(x):\n    return x < 10\n','def f(x):\n    return x < 1000\n'),
               ('string.py','def f():\n    return "allow"\n','def f():\n    return "deny"\n'),
               ('indent.py','def f(x):\n    if x:\n        x += 1\n        return x\n',
                            'def f(x):\n    if x:\n        x += 1\n    return x\n'),
               ('fstring.py','def f(x):\n    return f"{x + 1}"\n','def f(x):\n    return f"{x - 1}"\n'),
               ('async.py','async def f(x):\n    return x + 1\n','async def f(x):\n    return x - 1\n')]
        for name,one,two in cases:(a/name).write_text(one);(b/name).write_text(two)
        data=json.loads(self.run_cli('diff',a,b,'--format','json').stdout)
        self.assertTrue(all(p['status']=='changed' for p in data['pairs']))

    def test_streaming_json_handles_closed_pipe(self):
        (self.root/'x.js').write_text('export function f(x) { return x + 1; }\n'*500)
        with subprocess.Popen([self.binary,'scan',str(self.root),'--format','json'],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE) as process:
            process.stdout.close()
            error=process.stderr.read()
            self.assertEqual(process.wait(),0,error)


if __name__=="__main__":unittest.main()
