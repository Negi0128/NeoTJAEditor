import importlib
import sys
import threading
from pathlib import Path


def _prewarm(module_name):
    """重い依存モジュールの import を裏で先に済ませておく。

    **なぜこれで速くなるのか**
    起動時に必ず通る import のうち、`sounddevice` は実測 408ms、`numpy` は
    131ms かかる。sounddevice のぶんはミキサー音声(MixerAudioEngine)の
    __init__ に丸ごと乗っていて、その中身は PortAudio の DLL 読み込みと
    初期化だけ(デバイスを開く処理自体は 1ms もかかっていない)。

    かといって音声エンジンごと後回しにすると「最初の再生でそのぶん待たされる」
    だけで体感は良くならない。そこで **import だけ**を別スレッドへ出し、
    主スレッドが PySide6 と neotja を読んでウィンドウを組み立てているあいだ
    (実測 390ms ほど)に裏で終わらせる。本番の import 文へ来たときには
    sys.modules に載っているので、そこはほぼ素通りになる。

    2つを1本のスレッドに並べると、後ろに置いたほうの到着が遅れて
    MixerAudioEngine を待たせてしまい差し引き損をした(実測)。別々の
    スレッドにして主スレッドと三者同時に進めるのが一番速かった。
    起動全体で 1133ms -> 937ms(中央値)。

    まだ終わっていなければ import 機構がそこで待つだけなので、最悪でも
    従来と同じ。失敗しても握りつぶす: 本番の import は MixerAudioEngine 側に
    あり、そこで従来どおり例外を見てレガシー音声へ退避する(ここで先に
    転んでも、あちらの判断は何も変わらない)。

    importlib を使うのは `import x` だと未使用扱いで pyflakes に引っかかる
    ため。読み込ませること自体が目的で、名前は要らない。"""
    try:
        importlib.import_module(module_name)
    except Exception:  # noqa: BLE001
        pass


# 下の import 群(Qt + neotja)だけで 400ms ほどかかる。先読みはその前に
# 走らせないと重ならないので、モジュールの一番上で起動する。
#
# 素材の用意(設定の読み込み → System 探し → 指紋の照合、合わせて実測 55ms)も
# Qt を使わないので同じように裏へ出してみたが、**やめた**。主スレッドの
# import と GIL を取り合って import 側が同じだけ延び、起動全体では 1002ms ->
# 991ms(中央値)の 11ms しか変わらなかった。その 11ms のために、案内を出す
# 判断(見つからない/使えない/取り出せなかった)を主スレッドへ戻す配線を
# 増やす価値は無いと判断した。
for _mod in ("sounddevice", "numpy"):
    threading.Thread(target=_prewarm, args=(_mod,), daemon=True).start()

from PySide6.QtCore import Qt                 # noqa: E402
from PySide6.QtGui import QCursor, QIcon      # noqa: E402
from PySide6.QtWidgets import (               # noqa: E402
    QApplication, QFileDialog, QMessageBox,
)

from neotja import settings as settings_mod   # noqa: E402
from neotja import skin_cache                 # noqa: E402
from neotja.main_window import MainWindow     # noqa: E402


def _missing_system_text(searched):
    """System が見つからないときの案内。探した場所を実際に並べる —
    「置いてください」だけだと、どこへ置けば拾われるのかが分からない。"""
    places = "\n".join("　・%s" % p for p in searched) or "　（なし）"
    return (
        "NeoTJAEditor の起動に必要な素材フォルダが見つかりませんでした。\n\n"
        "このアプリは、音符・背景・打音などの素材を TNDE（太鼓さん次郎の派生）に"
        "付属する System フォルダから読み込みます。素材は再配布できないため"
        "アプリには同梱していません。\n\n"
        "NeoTJAEditor.exe と同じ場所に、TNDE の System フォルダをそのまま"
        "置いてください。\n\n"
        "探した場所:\n" + places + "\n\n"
        "別の場所にある場合は「フォルダを選ぶ」から指定してください。"
    )


def _ask_for_system_dir(cfg, searched):
    """System が無いまま起動できないので、選び直してもらう。

    戻り値は使える System の Path、または None(= 起動を諦める)。妥当で
    ないフォルダを選ばれたときは、何が足りないのかを言ってもう一度選ばせる
    — 黙って弾くと、利用者は同じフォルダを何度も選ぶことになる。"""
    text = _missing_system_text(searched)
    while True:
        box = QMessageBox()
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("System フォルダが見つかりません")
        box.setText(text)
        btn_pick = box.addButton("フォルダを選ぶ", QMessageBox.AcceptRole)
        box.addButton("終了", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not btn_pick:
            return None
        picked = QFileDialog.getExistingDirectory(
            None, "TNDE の System フォルダを選択")
        if not picked:
            # 選択をやめただけなので、案内へ戻す(そのまま終了しない)。
            continue
        if skin_cache.is_valid_system_dir(picked):
            cfg["system_dir"] = picked
            # 保存できたかを必ず確かめる。以前は例外を握りつぶしていたので、
            # 書けない場所(Program Files 配下など)にアプリを置いていると
            # 選び直しが残らず、毎回この同じダイアログからやり直しになった。
            # save_settings は書けない場合 %LOCALAPPDATA% へ退避するので、
            # ここまで来て False なら本当にどこへも書けていない。
            if not settings_mod.save_settings(cfg):
                QMessageBox.warning(
                    None, "設定を保存できませんでした",
                    "選んでいただいた System フォルダの場所を保存できませんでした。"
                    "\n\n今回はこのまま起動しますが、次回の起動でもう一度"
                    "選び直していただくことになります。\n\n"
                    "保存先:\n　%s" % settings_mod.settings_path())
            return Path(picked)
        text = (
            "選ばれたフォルダは TNDE の System ではないようです。\n\n"
            "　%s\n\n"
            "System フォルダの中には TNDE-R\\Graphics と TNDE-R\\Sounds が"
            "入っています。TNDE-R フォルダそのものや、その親フォルダではなく、"
            "「System」という階層を選んでください。" % picked
        )


def _warn_unusable_configured(configured, system_dir):
    """環境設定で指定された System が使えず、別のもので代用したときの断り。

    黙って別の System へ切り替わると、外付けや NAS を繋ぎ忘れた起動で、
    絵や音が変わった理由が利用者にまったく分からない(しかも本人は
    「指定してあるはず」と思っている)。"""
    QMessageBox.warning(
        None, "指定された System フォルダが使えません",
        "環境設定で指定されている System フォルダが見つからないか、"
        "中身が TNDE の System ではありませんでした。\n\n"
        "　指定:\n　%s\n\n"
        "代わりに次のフォルダの素材で起動します。\n\n"
        "　%s\n\n"
        "外付けドライブやネットワークドライブを指定している場合は、"
        "接続されているか確認してください。"
        % (configured, system_dir))


def _warn_partial_extract(res):
    """展開が部分的にしか成功しなかったときの断り。

    以前は ensure_cache の戻り値を誰も見ていなかったので、420件のうち何百件
    落ちていても利用者には何も伝わらず、「絵が出ない」とだけ言われることに
    なっていた。数枚欠けた程度でいちいち出しても邪魔なので、目に見えて
    足りないときだけ出す。"""
    QMessageBox.warning(
        None, "素材の一部を取り出せませんでした",
        "TNDE の System フォルダから取り出せなかった素材が %d 件あります"
        "（全 %d 件中）。\n\n"
        "絵や音の一部が本来と違う見た目・音になります。System フォルダが"
        "壊れているか、対応していない版の TNDE の可能性があります。\n\n"
        "別の System フォルダを使う場合は、環境設定の「エディタ・ツール」タブ"
        "から指定し直してください。" % (res.get("failed", 0), res.get("total", 0)))


def _warn_cache_unwritable(res):
    """キャッシュへ書けないときの案内。ここまで来ると素材が1つも用意できない
    ので起動は諦めるが、**例外で落ちる**のとは違って理由が利用者に残る。"""
    QMessageBox.critical(
        None, "素材を展開できませんでした",
        "素材を展開するフォルダに書き込めませんでした。\n\n"
        "　%s\n\n"
        "ウイルス対策ソフトに止められていないか、ディスクの空き容量が"
        "残っているか、フォルダが読み取り専用になっていないかを"
        "確認してください。" % res.get("error", ""))


def _prepare_skin(cfg):
    """素材の用意。System を見つけ、キャッシュへ展開するところまで。

    戻り値 False なら起動を続けられない。**ここで False になったときは
    ウィンドウを一切出さずに終わる** — 素材が無いままメインウィンドウを
    出すと、絵の無い真っ黒なプレビューを見せることになる。"""
    system_dir, searched, unusable = skin_cache.find_system_dir(cfg)
    if system_dir is None:
        system_dir = _ask_for_system_dir(cfg, searched)
        if system_dir is None:
            return False
    elif unusable:
        _warn_unusable_configured(unusable, system_dir)

    # 展開は初回で 2 秒程度、2 回目以降は指紋が一致するので 10ms 弱で終わる。
    # スプラッシュを出すほどではないので、砂時計カーソルだけ見せる。
    QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
    try:
        res = skin_cache.ensure_cache(system_dir)
    except Exception as exc:  # noqa: BLE001
        # ensure_cache は書き込みの失敗も戻り値で返す約束だが、それでも例外が
        # 抜けてきたときに**ウィンドウも案内も無いまま落ちる**のだけは避ける。
        # 利用者に残るのが「起動しない」だけになってしまうため。
        res = {"error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        QApplication.restoreOverrideCursor()

    # 展開の結果はここで初めて人の目に触れる。ensure_cache は例外を投げず、
    # 書き込みの失敗も戻り値に載せてくるので、ここで場合分けする。
    if res.get("error"):
        _warn_cache_unwritable(res)
        return False
    if not res.get("skipped") and res.get("failed"):
        # 「ほとんど成功しなかった」(指紋を書かなかった)ときは必ず、
        # そうでなければ1割以上落ちたときだけ知らせる。数枚の欠けは
        # TNDE の版違いでふつうに起きるので、毎回出すと読まれなくなる。
        if not res.get("trusted") or res["failed"] * 10 >= res.get("total", 1):
            _warn_partial_extract(res)
    return True


def main():
    app = QApplication(sys.argv)
    icon_path = settings_mod.icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    # 素材が揃わないうちは MainWindow を作らない。組み立ての途中で絵を読みに
    # 行く箇所があるので、順番を逆にすると案内の裏で空のウィンドウが見える。
    cfg = settings_mod.load_settings()
    if not _prepare_skin(cfg):
        sys.exit(0)
    win = MainWindow(app)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
