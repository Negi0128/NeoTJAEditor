"""実行中の QThread を「取り逃がさない」ための共有ヘルパ。

Qt の QThread は、run() が動いている最中に Python 側の最後の参照が消えて
GC されると "QThread: Destroyed while thread is still running" で
アプリごと落ちる。ダイアログを閉じた・別のファイルを開いて古いワーカーの
参照を上書きした、といった何気ない操作でこれが起きる。

そこでプロセスレベルの「待機所」にワーカーを移し、自分で終わるまで参照を
保持する。終了したら finished シグナルで待機所から自動的に外れる。

もともと dialogs/new_project_dialog.py にあった実装を、同じ問題を抱える
他の場所(preview_dock の曲デコード等)からも使えるように切り出したもの。
"""

# プロセスレベルの待機所。ここに入っている間は GC されない。
LINGERING_WORKERS = []


def detach_worker(worker):
    """実行中かもしれないワーカーを呼び出し元から切り離し、単独で寿命を
    全うできるようにする。終了済み(または None)なら何もしない。"""
    if worker is None:
        return
    try:
        if not worker.isRunning():
            return
    except RuntimeError:
        # 既に C++ 側が破棄されている(= 走っていない)。
        return
    if hasattr(worker, "cancel"):
        worker.cancel()
    worker.setParent(None)
    LINGERING_WORKERS.append(worker)
    worker.finished.connect(lambda: LINGERING_WORKERS.remove(worker)
                            if worker in LINGERING_WORKERS else None)
