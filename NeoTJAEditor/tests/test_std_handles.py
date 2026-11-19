"""標準の口が壊れていると subprocess が WinError 6 で落ちる。std_handles.fix() で直る。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_std_handles.py`
(実際のダウンロードはしない。子の Python で口を壊して確かめる)
"""
import os
import subprocess
import sys
import unittest

CHILD = r'''
import ctypes, subprocess, sys
from ctypes import wintypes
k = ctypes.windll.kernel32
k.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
for std in (-10, -11, -12):
    k.SetStdHandle(std, wintypes.HANDLE(0x1234))   # 壊れた口(窓だけの起動と同じ形)
if sys.argv[1] == "fix":
    from neotja import std_handles
    std_handles.fix()
try:
    r = subprocess.run([sys.executable, "-c", "print(1)"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=30)
    print("OK", r.returncode, file=open(sys.argv[2], "w"))
except OSError as e:
    print("ERR", getattr(e, "winerror", None), file=open(sys.argv[2], "w"))
'''


@unittest.skipUnless(sys.platform == 'win32', 'Windows only')
class StdHandleTests(unittest.TestCase):
    def run_child(self, mode):
        import tempfile
        out = os.path.join(tempfile.mkdtemp(), 'r.txt')
        env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        subprocess.run([sys.executable, '-c', CHILD, mode, out], env=env, timeout=60,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(out) as f:
            return f.read().split()

    def test_broken_handles_fail_then_fixed(self):
        self.assertEqual(self.run_child('nofix'), ['ERR', '6'])
        self.assertEqual(self.run_child('fix'), ['OK', '0'])


if __name__ == '__main__':
    unittest.main()
