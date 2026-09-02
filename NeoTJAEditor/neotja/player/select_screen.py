"""難易度選択画面。本家の「曲を選んだあと」の画面をそのまま出す。

TJA を1つ開いたら、まずここでコースを選ぶ。単発のファイルを開いて見る、
という使い方が中心なので、曲の一覧を経由するより素直で、鑑賞会でそのまま
見せられる絵にもなる。

**素材はすべて System にあるものを使う。** 特に Difficulty_Bar.png は
「戻る/設定のボタン2つ + コース5枚」が1枚に並んだもので、カードには
アイコンも「かんたん」等の文字も星の帯も**最初から入っている**。だから
こちらで描くのは、カードを並べることと、レベルの★・数字を重ねることだけ。

座標は 1280x720 基準。ゲーム画面と同じ土俵なので、そのまま並べても
見た目の縮尺が揃う。
"""

import os

from PySide6.QtCore import (QEasingCurve, QRect, Qt, Signal,
                            QVariantAnimation)
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from neotja import settings as settings_mod

#: 描くときの座標系。ゲーム画面と同じ 1280x720 で、そこへ倍率をかけて出す。
#: 座標を全部書き換えずに大きくできるので、実測して合わせた位置はそのまま。
SCREEN_W, SCREEN_H = 1280, 720
#: 実際に見せる倍率。1280x720 のままだと小さいという指摘。
SCREEN_SCALE = 1.5

#: Difficulty_Bar.png の中の位置(実測)。ボタン2つのあとにカードが5枚。
CARD_W, CARD_H = 131, 237
CARD_X0 = 176            # 1枚目(かんたん)の左端
CARD_PITCH = 143.25      # カードの間隔

#: 後ろのパネル(Difficulty_Back)の位置。
#:
#: 実機の画面を 1280x720 に直して測った。パネルの黒い縁は画面の
#: (236,39)-(1045,540)。素材の中で縁は (23,13)-(833,514) にあるので、
#: 素材の左上をここへ置くと1pxの狂いもなく重なる。
#: 目分量で (211, 96) にしていたときは y が 68px もずれていた。
PANEL_RECT = QRect(213, 26, 858, 528)

#: 曲名と副題を置く高さ(ベースライン)。実機の文字の位置。
TITLE_BASELINE = 145
SUBTITLE_BASELINE = 181

#: カードを置く場所。**本家の座標そのもの。**
#:
#: Change_OniUra/Oni_Panel.png は 1280x720 の画面の中に おに のカードだけを
#: 置いた絵で、そのカードは (861, 270) にある。ここから本家の並びが決まる:
#: おに が右端(861)で、間隔は Difficulty_Bar と同じ 143.25。逆算すると
#: かんたん=431.25 / ふつう=574.5 / むずかしい=717.75 / おに=861。
#: 目分量で中央に寄せていたときは、実機と縦も横もずれていた。
CARD_Y = 270
#: 全体を右へ寄せた分。実機は 1P の吹き出しと どんちゃんが左を埋めるが、
#: こちらはどちらも出さないので、そのぶん右へ寄せたほうが収まりがよい。
ROW_SHIFT = 20
CARD_SLOT_X = tuple(x + ROW_SHIFT for x in (431.25, 574.5, 717.75, 861.0))

#: スロットの並び。おに と うら は**同じ場所**を分け合い、めくって切り替える
#: (本家と同じ)。5枚並べると横幅が足りず、左のボタンをカードで潰してしまう。
SLOT_COURSES = (("Easy",), ("Normal",), ("Hard",), ("Oni", "Edit"))

#: Select_Number.png の中の各数字の左右(実測)。18px 等間隔に置かれているが
#: 字そのものは細いので、そのまま並べると字間が空いて「1 0」に見える。
NUM_BOUNDS = ((3, 14), (22, 30), (39, 51), (57, 69), (74, 87),
              (93, 104), (111, 122), (129, 141), (147, 159), (164, 176))
#: 数字どうしの間隔。
NUM_TRACK = 1

# --- カードの中の位置(カード左上からの座標、実測) -----------------------
# ★と点線の帯は**カードの絵に焼き込まれている**。こちらが描き足すのは
# 「レベルの数字」と「埋まっているぶんの★」だけなので、焼き込まれたものに
# 合わせないとずれる。目分量で置いていたときは実際にずれていた。
#: 焼き込まれた大きな★の中心。
CARD_STAR_CX, CARD_STAR_CY = 52, 176
#: その★の右端。数字はここから右へ置く。
CARD_STAR_RIGHT = 61
#: 数字を★の右へ置くときの間隔。
NUM_LEFT_GAP = 5
#: 点線の帯: 10個、中心 x=20 から 10px 間隔、y=195。
DOT_X0, DOT_STEP, DOT_COUNT, DOT_Y = 20, 10, 10, 195

#: 左に置く2つのボタン(Difficulty_Bar の先頭2つ)。
#:
#: 実機と同じ「カードの左隣、カードと上をそろえる」位置。おに と うら を
#: めくって切り替えることにしたので、カードは4枚に収まり、ここが空く。
BTN_W, BTN_H = 77, 76
BTN_BACK_SRC_X, BTN_CONF_SRC_X = 0, 88
#:
#: 実機はカードの左隣(232 と 324)だが、あちらは 1P の吹き出しと どんちゃんが
#: 左を埋めているのでそれで収まる。こちらはどちらも出さないぶん左が空いて
#: 見えるので、パネルの左端(236)と1枚目のカード(431)のあいだの中央へ寄せた。
BTN_BACK_POS = (249 + ROW_SHIFT, 270)
BTN_CONF_POS = (341 + ROW_SHIFT, 270)

#: Select_OniUra_Parts.png の中の部品(実測)。横に 顔 / 輪 / 巴、縦に 桃・紫。
PARTS_FACE = (10, 193)
PARTS_RING = (211, 400)
PARTS_TOMOE = (439, 580)
PARTS_ROW_Y = ((5, 194), (205, 394))     # 0=おに(桃) 1=うら(紫)

#: おに/うら を選ぶときに出す2枚の大きさ(ふつうのカードの何倍か)と間隔。
#: 少し大きくするのは、「別の場面が開いた」と分かるようにするため。
PICK_SCALE = 1.18
PICK_GAP = 40
#: 2枚が出てくるのにかける時間(ミリ秒)。押した手応えが出るぶんだけで、
#: 待たされたと感じない長さ。
PICK_ANIM_MS = 150

#: TJA のコースキー → Difficulty_Bar の何枚目か。
CARD_INDEX = {"Easy": 0, "Normal": 1, "Hard": 2, "Oni": 3, "Edit": 4}

#: 表示の並び順(やさしいものから)。
COURSE_ORDER = ("Easy", "Normal", "Hard", "Oni", "Edit")

#: 譜面を開いていないときの案内。空のパネルだけ出ていると何をすればいいのか
#: 分からない、と言われたので、いちばん目に入る曲名の場所へ置く。
EMPTY_TITLE = "TJAを選んでください"
EMPTY_SUBTITLE = "ドラッグ＆ドロップでも読み込めます"

#: 譜面に無いコースのカードをどれだけ暗くするか。
MISSING_DIM = 150


class SelectScreen(QWidget):
    """コースを選ぶ画面。選ばれたら courseChosen を出す。"""

    courseChosen = Signal(str)      # コースキー
    cancelled = Signal()
    #: レンチのボタン。NeoTJAPlayer の設定を開く。
    settingsRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(int(SCREEN_W * SCREEN_SCALE),
                          int(SCREEN_H * SCREEN_SCALE))
        self.setFocusPolicy(Qt.StrongFocus)
        # 押せるものの上でカーソルを変えるために、押していなくても
        # マウスの動きを受け取る。
        self.setMouseTracking(True)
        #: 押したときに鳴らす音を出す係(PlayerCore が繋ぐ)。
        self.click_sound_cb = None
        self._title = ""
        self._subtitle = ""
        # スロットごとの候補。[[かんたん], [ふつう], [むずかしい], [おに, うら]]
        # 譜面を開く前も4つ分の空スロットを持たせて、カードを薄暗く出す。
        self._slots = [[] for _ in SLOT_COURSES]
        self._slot_side = 0         # おに のスロットで今どちらを見せているか
        # おに と うら を選んでいる最中か(押されたら2枚を出して選ばせる)。
        self._picking = False
        # 出てくる動きの進み具合 0.0(閉じている) 〜 1.0(出きった)。
        # 押した場所から広がるので、どのカードを押したのかが目で追える。
        self._pick_t = 0.0
        self._pick_anim = QVariantAnimation(self)
        self._pick_anim.setDuration(PICK_ANIM_MS)
        self._pick_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._pick_anim.valueChanged.connect(self._on_pick_anim)
        self._pick_anim.finished.connect(self._on_pick_anim_done)
        self._cursor = 0
        self._skin = {}
        self._skin_ready = False
        # 薄暗くしたカードの作り置き。毎コマ作ると重いので種類ごとに1枚。
        self._dim_cache = {}
        self._title_font = None

    # ------------------------------------------------------------------
    def set_song(self, title, subtitle, courses):
        """曲名とコースを流し込む。courses は
        [{"key": "Oni", "level": 10}, ...] の形(並びは問わない)。"""
        self._title = title or ""
        self._subtitle = subtitle or ""
        known = {c.get("key"): c for c in (courses or []) if c.get("key")}
        # スロットは**常に4つ**。譜面に無いコースも薄暗く出す。
        # 出したり引っ込めたりすると、譜面ごとに並びが動いて目が迷う。
        # おに のスロットには おに と うら の両方が入りうる。
        self._slots = []
        for keys in SLOT_COURSES:
            self._slots.append([known[k] for k in keys if k in known])
        # おに のスロットは、うらがあっても最初は おに を見せる。
        self._slot_side = 0
        self._picking = False
        self._pick_t = 0.0
        # いちばん難しい「実際に入っているもの」に合わせる。
        have = [i for i, c in enumerate(self._slots) if c]
        self._cursor = have[-1] if have else 0
        self.update()

    def current_course(self):
        c = self._course_in(self._cursor)
        return c.get("key") if c else None

    def _course_in(self, i):
        """スロット i で今見えているコース。"""
        if not (0 <= i < len(self._slots)):
            return None
        cand = self._slots[i]
        if not cand:
            return None                 # 譜面に無いコース(薄暗く出すだけ)
        if len(cand) > 1:
            return cand[self._slot_side % len(cand)]
        return cand[0]

    def _has_flip(self, i):
        return 0 <= i < len(self._slots) and len(self._slots[i]) > 1

    # ------------------------------------------------------------------
    def _ensure_skin(self):
        """素材はここで読む。起動時ではなく最初に描くときに揃えるのは、
        ゲーム画面(_ensure_skin)と同じ考え方 — 使わない画面のぶんまで
        起動を遅くしない。"""
        if self._skin_ready:
            return
        self._skin_ready = True
        d = settings_mod.skin_dir()
        for name in ("Select_Cards", "Select_Panel", "Select_Star",
                     "Select_Number", "Select_OniUra_Parts",
                     "Select_Swap"):
            p = os.path.join(str(d), name + ".png")
            pm = QPixmap(p) if os.path.exists(p) else QPixmap()
            self._skin[name] = None if pm.isNull() else pm
        # 曲名の書体はゲーム画面のタイトルと同じものを使う(そろえないと
        # 同じアプリの中で書体が混ざる)。
        f = os.path.join(str(d), "Kanteiryu.otf")
        if os.path.exists(f):
            fid = QFontDatabase.addApplicationFont(f)
            fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
            if fams:
                self._title_font = fams[0]

    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        k = event.key()
        if k in (Qt.Key_Left, Qt.Key_D, Qt.Key_F):
            self._move(-1)
        elif k in (Qt.Key_Right, Qt.Key_K, Qt.Key_J):
            self._move(1)
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            key = self.current_course()
            if key:
                self.courseChosen.emit(key)
        elif k == Qt.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)

    def _move(self, delta):
        if not self._slots:
            return
        self._cursor = max(0, min(self._cursor + delta, len(self._slots) - 1))
        self.update()

    def flip(self):
        """おに ⇄ うら をめくる。"""
        if self._has_flip(self._cursor):
            self._slot_side += 1
            self.update()

    def mousePressEvent(self, event):
        """クリック1回で決まる。

        「選ぶ」と「決める」を分けると、鑑賞会で見せている最中に2手かかる。
        見る道具なので、押したものがそのまま始まるほうが素直。
        """
        pt = self._to_screen(event.position())
        if self._clickable_at(pt):
            self._click_sound()

        # おに/うら を選んでいる最中は、その2枚と「外側」しか押せない。
        if self._picking:
            for j, rect in enumerate(self._pick_rects()):
                if rect.contains(pt):
                    self._picking = False
                    self._pick_t = 0.0
                    self._pick_anim.stop()
                    self._slot_side = j
                    self.update()
                    self.courseChosen.emit(self._slots[self._cursor][j]["key"])
                    return
            # 外を押したら閉じるだけ(選ばずに戻れる道を必ず残す)。
            self._start_pick(False)
            return

        if self._back_rect().contains(pt):
            self.cancelled.emit()
            return
        if self._conf_rect().contains(pt):
            self.settingsRequested.emit()
            return
        i = self._card_at(pt)
        if i is None:
            return
        if not self._slots[i]:
            return                      # 譜面に無いコースは押しても何もしない
        self._cursor = i
        # うらがある譜面の おに は、押すと「おに と うら の2枚」を出して
        # 選ばせる。小さな切り替えボタンを付けてみたが、絵が何を意味するのか
        # 伝わらなかった。押したら候補が出てくるほうが、説明が要らない。
        if self._has_flip(i):
            self._picking = True
            self._start_pick(True)
            return
        self.update()
        c = self._course_in(i)
        if c:
            self.courseChosen.emit(c["key"])

    def mouseMoveEvent(self, event):
        pt = self._to_screen(event.position())
        # 押せるものの上ではカーソルを指の形にする。見ただけで押せると
        # 分かるようにするため(絵だけでは押せるかどうか伝わらない)。
        self.setCursor(Qt.PointingHandCursor if self._clickable_at(pt)
                       else Qt.ArrowCursor)
        if self._picking:
            return
        # どれを押そうとしているかが分かるように、指したものを大きくする。
        i = self._card_at(pt)
        if i is not None and self._slots[i] and i != self._cursor:
            self._cursor = i
            self.update()

    def _click_sound(self):
        """押した合図にドンを鳴らす。押せないものの上では鳴らさない
        (鳴ったのに何も起きない、が起きないように)。"""
        if self.click_sound_cb is not None:
            try:
                self.click_sound_cb()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _to_screen(posf):
        """ウィジェット上の座標を、描画に使っている 1280x720 の座標へ戻す。"""
        return (posf / SCREEN_SCALE).toPoint()

    def _clickable_at(self, pt):
        """そこに押せるものがあるか。"""
        if self._picking:
            return any(r.contains(pt) for r in self._pick_rects())
        if self._back_rect().contains(pt) or self._conf_rect().contains(pt):
            return True
        i = self._card_at(pt)
        return i is not None and bool(self._slots[i])

    def _on_pick_anim(self, v):
        self._pick_t = float(v)
        self.update()

    def _on_pick_anim_done(self):
        # 閉じきったところで初めて「選んでいない」状態に戻す。途中で戻すと
        # 縮んでいる最中に後ろのカードが押せてしまう。
        if self._pick_t <= 0.001:
            self._picking = False
        self.update()

    def _start_pick(self, opening):
        self._pick_anim.stop()
        self._pick_anim.setStartValue(self._pick_t)
        self._pick_anim.setEndValue(1.0 if opening else 0.0)
        self._pick_anim.start()

    def _pick_rects(self):
        """おに / うら の2枚を出す場所。押したカードから中央へ広がる。

        いきなり中央に出すと、どこから出てきたのかが分からない。押した
        カードの場所から動かすと、目が自然についていく。"""
        if not self._picking or not self._has_flip(self._cursor):
            return []
        t = max(0.0, min(1.0, self._pick_t))
        w = int(CARD_W * PICK_SCALE)
        h = int(CARD_H * PICK_SCALE)
        total = w * 2 + PICK_GAP
        x0 = (SCREEN_W - total) // 2
        y0 = CARD_Y + (CARD_H - h) // 2
        finals = [QRect(x0, y0, w, h), QRect(x0 + w + PICK_GAP, y0, w, h)]
        srcs = self._card_rects()
        src = srcs[self._cursor] if self._cursor < len(srcs) else finals[0]
        out = []
        for f in finals:
            out.append(QRect(
                int(round(src.x() + (f.x() - src.x()) * t)),
                int(round(src.y() + (f.y() - src.y()) * t)),
                int(round(src.width() + (f.width() - src.width()) * t)),
                int(round(src.height() + (f.height() - src.height()) * t))))
        return out

    def _swap_rect(self, i):
        """めくるボタンの場所。カードの右上。"""
        rects = self._card_rects()
        if not (0 <= i < len(rects)):
            return QRect()
        r = rects[i]
        sx = r.width() / float(CARD_W)
        sy = r.height() / float(CARD_H)
        x, y, w, h = SWAP_RECT
        return QRect(r.x() + int(x * sx), r.y() + int(y * sy),
                     int(w * sx), int(h * sy))

    @staticmethod
    def _back_rect():
        return QRect(BTN_BACK_POS[0], BTN_BACK_POS[1], BTN_W, BTN_H)

    @staticmethod
    def _conf_rect():
        return QRect(BTN_CONF_POS[0], BTN_CONF_POS[1], BTN_W, BTN_H)

    def _card_at(self, pt):
        for i, rect in enumerate(self._card_rects()):
            if rect.contains(pt):
                return i
        return None

    def _card_rects(self):
        """カードの位置。本家と同じ4つの場所。譜面に何が入っていても動かない。"""
        return [QRect(int(round(x)), CARD_Y, CARD_W, CARD_H)
                for x in CARD_SLOT_X]

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        self._ensure_skin()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor("#0d1117"))
        # ここから先は 1280x720 の座標で描く。倍率は painter に持たせる。
        p.scale(SCREEN_SCALE, SCREEN_SCALE)

        panel = self._skin.get("Select_Panel")
        if panel is not None:
            p.drawPixmap(PANEL_RECT, panel)
        else:
            p.fillRect(PANEL_RECT, QColor("#5fd0bb"))

        self._draw_title(p)
        self._draw_buttons(p)
        self._draw_cards(p)
        if self._picking:
            self._draw_pick(p)

    def _draw_title(self, p):
        # 曲名の帯は Difficulty_Back の絵に含まれているので、文字だけ置く。
        name = self._title_font or "Meiryo"
        cx = PANEL_RECT.center().x()
        # まだ譜面を開いていないときは、曲名の場所に何をすればいいのかを出す。
        # 空のパネルだけが出ていると「?」となる、という指摘を受けての対応。
        title = self._title or EMPTY_TITLE
        subtitle = self._subtitle or (EMPTY_SUBTITLE if not self._title else "")
        self._draw_outlined(p, title, cx, TITLE_BASELINE, 40, name)
        if subtitle:
            self._draw_outlined(p, subtitle, cx, SUBTITLE_BASELINE, 22, name)

    @staticmethod
    def _draw_outlined(p, text, cx, baseline, size, family):
        """白抜き＋黒縁の文字。drawText に縁取りは無いので、いったん
        QPainterPath にしてから縁を先に描く(細い縁が潰れないよう
        strokePath -> fillPath の順)。"""
        f = QFont(family)
        f.setPixelSize(size)
        f.setBold(True)
        path = QPainterPath()
        from PySide6.QtGui import QFontMetricsF
        fm = QFontMetricsF(f)
        w = fm.horizontalAdvance(text)
        path.addText(cx - w / 2.0, baseline, f, text)
        pen = QPen(QColor(0, 0, 0, 235))
        # 縁は太め。実機の曲名はかなりしっかり縁取られていて、細いと
        # 明るいパネルの上で文字が沈む。
        pen.setWidthF(max(4.0, size * 0.24))
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.strokePath(path, pen)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.fillPath(path, QColor("#ffffff"))

    def _draw_buttons(self, p):
        """左の2つ(戻る / 設定)。Difficulty_Bar の先頭に入っているものを
        そのまま使う。実機と同じ位置に置いてある。"""
        cards = self._skin.get("Select_Cards")
        if cards is None:
            return
        p.drawPixmap(self._back_rect(), cards,
                     QRect(BTN_BACK_SRC_X, 0, BTN_W, BTN_H))
        p.drawPixmap(self._conf_rect(), cards,
                     QRect(BTN_CONF_SRC_X, 0, BTN_W, BTN_H))

    def _draw_cards(self, p):
        cards = self._skin.get("Select_Cards")
        rects = self._card_rects()
        for i, rect in enumerate(rects):
            course = self._course_in(i)
            # 譜面に無いコースも枠は出す。並びが譜面ごとに動くと目が迷うので、
            # 「無い」ことも同じ場所で見せる。
            missing = course is None
            if missing:
                idx = i if i < 3 else 3
            else:
                idx = CARD_INDEX.get(course.get("key"), 3)
            selected = (not missing) and (i == self._cursor)
            # 選んでいるカードは少し持ち上げて大きく見せる。
            r = QRect(rect)
            if selected:
                r.adjust(-6, -12, 6, 6)
            if missing:
                dim = self._dim_card(idx)
                if dim is not None:
                    p.drawPixmap(r, dim)
                self._draw_level(p, r, None)
            elif cards is not None:
                sx = int(round(CARD_X0 + idx * CARD_PITCH))
                p.drawPixmap(r, cards, QRect(sx, 0, CARD_W, CARD_H))
                self._draw_level(p, r, course.get("level"))
            else:
                p.fillRect(r, QColor("#c86"))
                self._draw_level(p, r, course.get("level"))

    def _draw_pick(self, p):
        """おに と うら の2枚を出す。

        後ろを暗くしてから出す。そうしないと、後ろのカード列と同じ重みで
        並んで見えて「いま何を聞かれているのか」が伝わらない。"""
        cards = self._skin.get("Select_Cards")
        rects = self._pick_rects()
        if not rects:
            return
        # しっかり暗くする。薄いと後ろのカードが2枚のあいだから覗いて、
        # 何枚あるのか分からない絵になる(実際にそうなった)。
        p.fillRect(QRect(0, 0, SCREEN_W, SCREEN_H),
                   QColor(0, 0, 0, int(210 * self._pick_t)))
        for j, r in enumerate(rects):
            course = self._slots[self._cursor][j]
            idx = CARD_INDEX.get(course.get("key"), 3)
            if cards is not None:
                sx = int(round(CARD_X0 + idx * CARD_PITCH))
                p.drawPixmap(r, cards, QRect(sx, 0, CARD_W, CARD_H))
            self._draw_level(p, r, int(course.get("level") or 0))

    def _dim_card(self, idx):
        """薄暗くしたカード(譜面に無いコース用)。種類ごとに1枚だけ作り置く。

        **矩形で上から黒を塗ってはいけない。** カードは角が丸いので、
        その塗りが角からはみ出して四角い影が付く(実際にそうなっていた)。
        カードの絵の上にだけ乗るよう SourceAtop で重ねる。"""
        pm = self._dim_cache.get(idx)
        if pm is not None:
            return pm
        cards = self._skin.get("Select_Cards")
        if cards is None:
            return None
        sx = int(round(CARD_X0 + idx * CARD_PITCH))
        pm = cards.copy(QRect(sx, 0, CARD_W, CARD_H))
        q = QPainter(pm)
        q.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        q.fillRect(pm.rect(), QColor(0, 0, 0, MISSING_DIM))
        q.end()
        self._dim_cache[idx] = pm
        return pm

    def _draw_level(self, p, rect, level):
        """レベルの数字と、埋まっているぶんの★。

        ★も点線の帯もカードの絵に焼き込まれているので、こちらはその**上に
        重ねるだけ**。位置はカードの絵から実測した定数(CARD_STAR_* / DOT_*)に
        合わせる。目分量で置いていたときは、数字も星も明らかにずれていた。

        カードは選択時に少し拡大するので、倍率をかけてから置く。
        """
        num = self._skin.get("Select_Number")
        star = self._skin.get("Select_Star")
        sx = rect.width() / float(CARD_W)
        sy = rect.height() / float(CARD_H)

        def px(cx, cy):
            """カード内の座標を、画面上の座標へ。"""
            return rect.x() + int(round(cx * sx)), rect.y() + int(round(cy * sy))

        if level is None or level <= 0:
            # レベルが分からない/コースが無い。素材の数字は 0-9 しか無いので、
            # 「-」は自前で引く(数字と同じ白＋黒縁で、太さも高さに合わせる)。
            _x, cy = px(0, CARD_STAR_CY)
            x, _y2 = px(CARD_STAR_RIGHT + NUM_LEFT_GAP, 0)
            w = int(round(12 * sx))
            h = max(2, int(round(4 * sy)))
            p.setPen(QPen(QColor(0, 0, 0, 220), max(1.0, 2.0 * sy)))
            p.setBrush(QColor("#ffffff"))
            p.drawRect(QRect(x, cy - h // 2, w, h))
            p.setPen(Qt.NoPen)
            p.setBrush(Qt.NoBrush)
            return
        if num is not None:
            digits = [int(c) for c in str(level)]
            widths = [NUM_BOUNDS[d][1] - NUM_BOUNDS[d][0] + 1 for d in digits]
            h = int(round(num.height() * sy))
            x, _y = px(CARD_STAR_RIGHT + NUM_LEFT_GAP, 0)
            # 数字の高さの中心を、焼き込まれた★の中心に合わせる。
            _x, cy = px(0, CARD_STAR_CY)
            y = cy - h // 2
            for d, w in zip(digits, widths):
                dw = int(round(w * sx))
                p.drawPixmap(QRect(x, y, dw, h), num,
                             QRect(NUM_BOUNDS[d][0], 0, w, num.height()))
                x += dw + int(round(NUM_TRACK * sx))

        if star is not None:
            # 帯は10個ぶんしか無い。★11 の譜面もあるので 10 で頭打ちにする。
            n = max(0, min(level, DOT_COUNT))
            sw = int(round(star.width() * sx))
            sh = int(round(star.height() * sy))
            for i in range(n):
                cx, cy = px(DOT_X0 + i * DOT_STEP, DOT_Y)
                p.drawPixmap(cx - sw // 2, cy - sh // 2, sw, sh, star)
