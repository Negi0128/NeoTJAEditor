import importlib
import sys
import threading


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
    QApplication, QCheckBox, QMessageBox,
)

from neotja import settings as settings_mod   # noqa: E402
from neotja import skin_cache                 # noqa: E402
from neotja.main_window import MainWindow     # noqa: E402


def _missing_system_text(searched):
    """System が見つからないときの案内。

    書くべきことは3つ。**何が足りないのか**(TNDE の System フォルダ)、
    **どうすれば直るのか**(exe の隣に置く / 環境設定で指定する)、そして
    **どこを見たのか**。最後のものが無いと、利用者は「置いたつもりなのに
    出る」ときに手の打ちようがない — 実際に見に行ったパスをそのまま並べれば、
    一段深く置いてしまった等の食い違いが自分で分かる。

    前に別の System から展開した素材がキャッシュに残っていれば、それは
    そのまま使われる。「内蔵スキンで再生」と言いながら絵が出ることになるので、
    そのときは一言添える(黙っていると挙動の説明がつかない)。"""
    places = "\n".join("　・%s" % p for p in searched) or "　（なし）"
    if skin_cache.cached_file_count() > 0:
        tail = (
            "このまま起動すると、前回取り出した素材がキャッシュに残っている"
            "ぶんはそのまま使われ、足りないところは内蔵スキン（アプリが自前で"
            "描く絵と合成打音）になります。"
        )
    else:
        tail = (
            "このまま起動すると、内蔵スキン（アプリが自前で描く絵と合成打音）"
            "で動きます。編集・保存・譜面画像生成・動画の書き出しは"
            "そのまま使えますが、見た目と音は本家と違うものになります。"
        )
    return (
        "音符・背景・打音などの素材が入った System フォルダが"
        "見つかりませんでした。\n\n"
        "このアプリは、これらの素材を TNDE（太鼓さん次郎の派生）に付属する "
        "System フォルダから読み込みます。素材は再配布できないため"
        "アプリには同梱していません。\n\n"
        "■ 直しかた\n"
        "NeoTJAEditor.exe と同じ場所に、TNDE の System フォルダをそのまま"
        "置いてください。\n"
        "別の場所にあるものを使いたい場合は、「設定 → 環境設定 → "
        "エディタ・ツール」タブの「素材（System フォルダ）」で指定できます"
        "（どちらの場合も、反映されるのは次回の起動からです）。\n\n"
        "■ 探した場所\n" + places + "\n\n"
        + tail
    )


def _warn_missing_system(cfg, searched):
    """System が見つからないことを伝える。起動は止めない。

    以前はここで「フォルダを選ぶ / 終了」を出し、選ばれなければ**起動そのもの
    を諦めていた**。素材が無いと真っ黒な画面になる、という前提で書かれた作り
    だったが、描画側はもともと素材が無ければ自前の絵へ落ちるようにできている
    ので、起動を止める理由が無い。素材を持っていない人がまず触ってみることも
    できなくなっていた。

    フォルダ選択もここには置かない。起動のたびに出るダイアログでファイル
    ダイアログまで開かせるのは重く、同じことは環境設定の「素材（System
    フォルダ）」からいつでもできる(文面でその場所を案内している)。

    「次回から表示しない」を押されたら設定へ残す。素材を置くつもりが無い人に
    毎回同じ話をしても読まれなくなるだけで、そのぶん本当に読んでほしい他の
    警告まで流し読みされる。"""
    box = QMessageBox()
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("素材（System フォルダ）が見つかりません")
    box.setText(_missing_system_text(searched))
    box.addButton("内蔵スキンで再生", QMessageBox.AcceptRole)
    # QMessageBox のチェックボックスは「押したボタンに関わらず」読める。
    # ボタンが1つしか無いので、押されたかどうかではなく状態だけを見る。
    never = QCheckBox("次回から表示しない")
    box.setCheckBox(never)
    box.exec()
    if not never.isChecked():
        return
    cfg["warn_missing_system"] = False
    # 保存できたかを確かめる。書けない場所(Program Files 配下など)に
    # アプリを置いていると黙って残らず、「次回から表示しない」を押したのに
    # 毎回出る、という一番いらだつ壊れ方をする。save_settings は書けなければ
    # %LOCALAPPDATA% へ退避するので、False は本当にどこへも書けなかったとき。
    if not settings_mod.save_settings(cfg):
        QMessageBox.warning(
            None, "設定を保存できませんでした",
            "「次回から表示しない」を保存できませんでした。\n\n"
            "今回はこのまま起動しますが、次回の起動でもこの案内が"
            "表示されます。\n\n"
            "保存先:\n　%s" % settings_mod.settings_path())


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
    """キャッシュへ書けないときの案内。素材は1つも用意できないが、**起動は
    続ける** — 内蔵スキンでなら動くので、ここで終わらせる理由が無い。
    何が起きたかを伝えて、直せるようにする。"""
    QMessageBox.warning(
        None, "素材を展開できませんでした",
        "素材を展開するフォルダに書き込めませんでした。\n\n"
        "　%s\n\n"
        "ウイルス対策ソフトに止められていないか、ディスクの空き容量が"
        "残っているか、フォルダが読み取り専用になっていないかを"
        "確認してください。\n\n"
        "今回は内蔵スキン（アプリが自前で描く絵と合成打音）で起動します。"
        % res.get("error", ""))


def _prepare_skin(cfg):
    """素材の用意。System を見つけ、キャッシュへ展開するところまで。

    **どう転んでも起動は止めない。** 以前は素材が揃わないと起動そのものを
    諦めていたが、描画側はもともと「素材が無ければ自前の絵と合成打音へ落ちる」
    作りなので、止める理由が無かった(素材を持っていない人が試すことすら
    できなかった)。ここでやるのは案内を出すことだけ。"""
    system_dir, searched, unusable = skin_cache.find_system_dir(cfg)
    if system_dir is None:
        # 内蔵スキンで続ける。案内は「次回から表示しない」で黙らせられる。
        if cfg.get("warn_missing_system", True):
            _warn_missing_system(cfg, searched)
        return
    if unusable:
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
        return
    if not res.get("skipped") and res.get("failed"):
        # 「ほとんど成功しなかった」(指紋を書かなかった)ときは必ず、
        # そうでなければ1割以上落ちたときだけ知らせる。数枚の欠けは
        # TNDE の版違いでふつうに起きるので、毎回出すと読まれなくなる。
        if not res.get("trusted") or res["failed"] * 10 >= res.get("total", 1):
            _warn_partial_extract(res)


def main():
    app = QApplication(sys.argv)
    icon_path = settings_mod.icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    # 素材の用意は MainWindow より先。組み立ての途中で絵を読みに行く箇所が
    # あるので、順番を逆にすると案内の裏で空のウィンドウが見える。素材が
    # 無くても _prepare_skin は案内を出すだけで、起動はそのまま続く。
    cfg = settings_mod.load_settings()
    _prepare_skin(cfg)
    win = MainWindow(app)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
