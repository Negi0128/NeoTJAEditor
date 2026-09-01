"""NeoTJAEditor 本体のパッケージ。

ここで唯一していることは、落ちた理由を残す仕掛けを掛けること
(neotja/crashlog.py)。**neotja をどう起動しても必ず通る**唯一の場所なので
ここに置いてある — `python -m neotja` を通らない起動(IDE から別のファイルを
実行する、検証用のスクリプトから MainWindow を組み立てる等)でも記録が
残るようにするため。掛けるのに 1ms もかからず、失敗しても握りつぶすので、
起動を邪魔することはない。
"""

from neotja import crashlog as _crashlog

_crashlog.install()
