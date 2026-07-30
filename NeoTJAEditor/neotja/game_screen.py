"""本家(太鼓の達人 / TNDE)の画面構成を再現する合成ウィジェット。

譜面レーンそのものは既存の ChartPreviewWidget が描く。ここはその周りを組み
立てる係で、背景・左パネル・フッターを敷き、レーンを実測位置へ置くだけ。
レーンの描画ロジックには一切触っていない(寸法だけ set_lane_geometry で
本家に合わせる)。

座標は TNDE のゲーム内キャプチャ(1280x720 ネイティブ)から実測した:

    y=  0..188   上部背景(どんちゃん・曲名・魂ゲージ)
    y=188..364   レーン一式   左パネル 333x176 / レーン x=333
    y=192..322   └ レーン本体 947x130   (333+947 = 1280 ちょうど)
    y=322..348   └ 打音表記帯 947x26
    y=360..720   下部背景(踊り子) 1280x360
    y=676..720   フッター 1280x44
    判定円の中心 x=414, y=257   (レーン左端から 81px)

FULL(録画用)は 1280x720 全部、COMPACT(再生モード)は上部背景とレーン一式
だけの 1280x360。どちらも同じ座標系なので、切り替えても位置がずれない。
"""

import os

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from neotja import settings as settings_mod
from neotja.chart_preview_widget import ChartPreviewWidget

SCREEN_W = 1280
SCREEN_H_FULL = 720
SCREEN_H_COMPACT = 360

# --- 実測した配置 --------------------------------------------------------
BG_TOP_H = 188            # 上部背景の高さ
PANEL_X, PANEL_Y = 0, 188      # 左パネル(スコア/コンボ/太鼓/銘板)
PANEL_W, PANEL_H = 333, 176
LANE_X, LANE_Y = 333, 192      # レーン本体の左上
LANE_W, LANE_H = 947, 130
SE_STRIP_H = 26                # 打音表記帯(レーン本体の直下)
JUDGE_X_IN_LANE = 81           # レーン左端から判定円の中心まで
BG_DOWN_Y, BG_DOWN_H = 360, 360
FOOTER_Y, FOOTER_H = 676, 44

# --- 左パネルの中身(本家スクショから採った位置) ----------------------
# 数字シートの1文字は 29.3x31.3。本家のスコアは高さ25px前後だったので
# 0.8 倍で置く(1.6 倍にしたら6桁がパネルからはみ出した)。
SCORE_RIGHT, SCORE_Y = 182, 192      # スコアは右詰め
SCORE_SCALE = 0.8
COURSE_SYM_POS = (26, 232)           # コース記号(おに 等)
DRUM_POS = (200, 196)                # 太鼓 120x133
NAMEPLATE_POS = (4, 296)             # 1P 銘板
COMBO_Y_OFF = 24                     # 太鼓の上端からコンボ数字までの距離
COMBO_TEXT_Y_OFF = 62                # 同 「コンボ」文字まで
COMBO_SCALE = 0.95
# コンボ数字の色: 0-49 白 / 50-99 銀 / 100- 金
COMBO_SILVER_AT, COMBO_GOLD_AT = 50, 100
# 太鼓が光る時間(叩いた瞬間からの秒数)。本家もごく短い。
DRUM_GLOW_SEC = 0.09

# --- 魂ゲージ ------------------------------------------------------------
GAUGE_POS = (490, 150)   # 700x68。本家スクショでゲージ帯が始まる位置


class GameScreenWidget(QWidget):
    """1280x720(または上半分だけの 1280x360)の画面を組み立てる。

    compact=True は「通常再生モード」用で、下部背景と踊り子を描かないぶん
    軽く、窓も小さい。録画は compact=False で全部描く。
    """

    def __init__(self, chart_preview: ChartPreviewWidget, compact=False, parent=None):
        super().__init__(parent)
        self.chart_preview = chart_preview
        self._compact = bool(compact)
        self._skin = {}
        self._score_timeline = None
        self._course_key = None
        self._course_sym = None
        self._load_skin()

        chart_preview.setParent(self)
        # レーンの寸法を本家に合わせる。上下の余白は 0 にして、レーン本体と
        # 打音表記帯だけの高さ(130+26)にする — 余白ぶんの情報(連打カウント等)
        # は画面側の余白に描くほうが本家に近い。
        chart_preview.set_lane_geometry(LANE_W, LANE_H, JUDGE_X_IN_LANE,
                                        top_margin=0, bottom_margin=0)
        # コンボはこちらが左パネルの太鼓の上に描くので、レーン内には出さない。
        chart_preview._hide_lane_combo = True
        chart_preview.move(LANE_X, LANE_Y)

        self.setFixedSize(SCREEN_W, SCREEN_H_COMPACT if compact else SCREEN_H_FULL)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    # ------------------------------------------------------------------
    def _load_skin(self):
        """skin/ から使う絵を読む。無ければ None のままで、描画側が黙って飛ばす
        (スキンは同梱しない外部パックなので、無くても動くのが前提)。"""
        base = str(settings_mod.skin_dir())
        for key, rel in (
            ("bg_top", "Background.png"),
            ("bg_down", "Bg_down.png"),
            ("footer", "Footer.png"),
            ("panel", "Taiko_Background.png"),
            ("lane_frame", "Taiko_Frame.png"),
            ("drum", os.path.join("Combo", "Base.png")),
            ("drum_don", os.path.join("Combo", "Don.png")),
            ("drum_ka", os.path.join("Combo", "Ka.png")),
            ("combo_white", os.path.join("Combo", "Digits.png")),
            ("combo_silver", os.path.join("Combo", "DigitsSilver.png")),
            ("combo_gold", os.path.join("Combo", "DigitsGold.png")),
            ("combo_text", os.path.join("Combo", "Text.png")),
            ("score_digits", "Score_Plate.png"),
            ("nameplate", "NamePlate.png"),
            ("gauge", "Gauge.png"),
            ("gauge_base", "Gauge_Base.png"),
        ):
            path = os.path.join(base, rel)
            if os.path.exists(path):
                pm = QPixmap(path)
                if not pm.isNull():
                    self._skin[key] = pm

    def set_chart(self, preview_data: dict, course_key=None):
        """譜面が変わったときに呼ぶ。スコアの配点をここで決めておく
        (毎フレーム計算しないで済むよう、時刻→点数の表を先に作る)。"""
        from neotja.score import ScoreTimeline

        self._score_timeline = ScoreTimeline(preview_data or {})
        self._course_key = course_key or (preview_data or {}).get("course_key")
        self._course_sym = None
        if self._course_key:
            # コース記号は Easy/Normal/Hard/Oni/Edit の5種。
            name = {"easy": "Easy", "normal": "Normal", "hard": "Hard",
                    "oni": "Oni", "edit": "Edit", "tower": "Oni",
                    "ura": "Oni"}.get(str(self._course_key).lower())
            if name:
                path = os.path.join(str(settings_mod.skin_dir()), "CourseSymbol", name + ".png")
                if os.path.exists(path):
                    pm = QPixmap(path)
                    self._course_sym = pm if not pm.isNull() else None
        self.update()

    # ------------------------------------------------------------------
    def _draw_digits(self, p, sheet, value, *, cols=10, rows=1, row=0,
                     right=None, left=None, y=0, scale=1.0):
        """0-9 が横に並んだシートから数字を描く。right 指定で右詰め。"""
        if sheet is None:
            return
        cw, ch = sheet.width() / cols, sheet.height() / rows
        w, h = cw * scale, ch * scale
        s = str(int(value))
        x = (right - w * len(s)) if right is not None else (left or 0)
        for c in s:
            i = int(c)
            p.drawPixmap(QRect(int(x), int(y), int(w) + 1, int(h) + 1), sheet,
                         QRect(int(i * cw), int(row * ch), int(cw), int(ch)))
            x += w

    def _draw_left_panel(self, p, combo, score, recent):
        """左パネル: スコア / コース記号 / 太鼓 + コンボ / 銘板。"""
        # --- スコア(右詰め) ---
        self._draw_digits(p, self._skin.get("score_digits"), score,
                          cols=10, rows=3, row=0,
                          right=SCORE_RIGHT, y=SCORE_Y, scale=SCORE_SCALE)

        # --- コース記号 ---
        if self._course_sym is not None:
            p.drawPixmap(COURSE_SYM_POS[0], COURSE_SYM_POS[1], self._course_sym)

        # --- 太鼓 ---
        drum = self._skin.get("drum")
        dx, dy = DRUM_POS
        if drum is not None:
            p.drawPixmap(dx, dy, drum)
            # 叩いた瞬間だけ、その音符の色で光らせる(面=赤 / 縁=水色)。
            if recent is not None:
                elapsed, char, _n = recent
                if 0.0 <= elapsed < DRUM_GLOW_SEC:
                    glow = self._skin.get("drum_don" if char in "13" else "drum_ka")
                    if glow is not None:
                        # 叩いた直後がいちばん明るく、すぐ消える。
                        p.setOpacity(max(0.0, 1.0 - elapsed / DRUM_GLOW_SEC))
                        p.drawPixmap(dx, dy, glow)
                        p.setOpacity(1.0)

        # --- コンボ(太鼓の上に重ねる) ---
        if combo > 0:
            key = ("combo_gold" if combo >= COMBO_GOLD_AT else
                   "combo_silver" if combo >= COMBO_SILVER_AT else "combo_white")
            sheet = self._skin.get(key)
            if sheet is not None:
                cw = sheet.width() / 10 * COMBO_SCALE
                dw = drum.width() if drum is not None else 120
                # 太鼓の中心に対して左右対称に置く。
                right = dx + dw // 2 + int(cw * len(str(combo))) // 2
                self._draw_digits(p, sheet, combo, right=right,
                                  y=dy + COMBO_Y_OFF, scale=COMBO_SCALE)
            ct = self._skin.get("combo_text")
            if ct is not None and drum is not None:
                p.drawPixmap(dx + drum.width() // 2 - ct.width() // 2,
                             dy + COMBO_TEXT_Y_OFF, ct)

        # --- 銘板 ---
        np_ = self._skin.get("nameplate")
        if np_ is not None:
            p.drawPixmap(NAMEPLATE_POS[0], NAMEPLATE_POS[1], np_)

    def _draw_gauge(self, p, ratio):
        """魂ゲージ。全良前提なので「叩いた数 / 総数」で満ちていく。"""
        base = self._skin.get("gauge_base")
        fill = self._skin.get("gauge")
        gx, gy = GAUGE_POS
        if base is not None:
            p.drawPixmap(gx, gy, base)
        if fill is not None:
            wpx = int(fill.width() * max(0.0, min(1.0, ratio)))
            if wpx > 0:
                p.drawPixmap(gx, gy, fill, 0, 0, wpx, fill.height())

    def set_compact(self, compact: bool):
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self.setFixedSize(SCREEN_W, SCREEN_H_COMPACT if compact else SCREEN_H_FULL)
        self.update()

    def is_compact(self) -> bool:
        return self._compact

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        h = self.height()
        # スキンが無い環境でも「黒い箱」にならないよう下地は必ず塗る。
        p.fillRect(self.rect(), QColor("#0d1117"))

        # --- 上部背景 (0..188 が見える範囲) ---
        bg = self._skin.get("bg_top")
        if bg is not None:
            # 素材(1280x316)を 188px で切ると絵が途中で断ち切られるので、
            # 丸ごと描いて下端(188 以降)はレーン一式で隠す。地面の線が
            # レーンの上に来るので、本家と同じ「奥行きのある」見え方になる。
            p.drawPixmap(0, 0, bg)

        if not self._compact:
            # --- 下部背景 (360..720) ---
            bd = self._skin.get("bg_down")
            if bd is not None:
                p.drawPixmap(0, BG_DOWN_Y, bd)
            # --- フッター (676..720) ---
            ft = self._skin.get("footer")
            if ft is not None:
                p.drawPixmap(0, FOOTER_Y, ft)

        # --- 左パネル (0,188 - 333x176) ---
        panel = self._skin.get("panel")
        if panel is not None:
            p.drawPixmap(PANEL_X, PANEL_Y, panel)
        else:
            p.fillRect(QRect(PANEL_X, PANEL_Y, PANEL_W, PANEL_H), QColor("#8c1d1d"))

        # レーン枠(Taiko_Frame 951x224)は、どの位置に合わせるのかを実測できて
        # いないので今は描かない。当て推量で置いたら画面の真ん中に黒い帯が
        # 浮いてしまったため、根拠が取れるまで出さない方針にする。
        # 代わりに、レーンの上下に細い境界線だけ引いて締める。
        p.fillRect(QRect(LANE_X, LANE_Y - 2, LANE_W, 2), QColor(0, 0, 0, 220))
        p.fillRect(QRect(LANE_X, LANE_Y + LANE_H + SE_STRIP_H, LANE_W, 2),
                   QColor(0, 0, 0, 220))

        # --- HUD(スコア・コンボ・太鼓・ゲージ) ---
        # 現在値はレーン側が持っているものをそのまま使う。HUD 用に別のカウントを
        # 持たないので、シークしても再生を止めてもズレようがない。
        try:
            now, combo, recent = self.chart_preview.game_state()
        except Exception:  # noqa: BLE001
            now, combo, recent = 0.0, 0, None
        score = self._score_timeline.at(now) if self._score_timeline else 0
        total = max(1, self.chart_preview.total_notes())
        self._draw_left_panel(p, combo, score, recent)
        self._draw_gauge(p, combo / total)

        p.end()
        # レーン本体は子ウィジェット(ChartPreviewWidget)が自分で描く。

    # ------------------------------------------------------------------
    def lane_rect(self) -> QRect:
        """レーン本体の矩形(照合・デバッグ用)。"""
        return QRect(LANE_X, LANE_Y, LANE_W, LANE_H)

    def judge_center(self):
        """判定円の中心(画面座標)。実測値と突き合わせるのに使う。"""
        return (LANE_X + JUDGE_X_IN_LANE, LANE_Y + LANE_H // 2)
