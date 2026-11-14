"""密な譜面の編集を速くした所が、前の(遅い)やり方と同じ答えを返すか。"""
import random
import unittest

from neotja import note_edit


def _slow_diff(text, new):
    """以前 main_window で1文字ずつ回していたやり方そのもの。"""
    p = 0
    limit = min(len(text), len(new))
    while p < limit and text[p] == new[p]:
        p += 1
    s = 0
    while s < limit - p and text[len(text) - 1 - s] == new[len(new) - 1 - s]:
        s += 1
    return p, len(text) - s, len(new) - s


class TextDiffRangeTests(unittest.TestCase):
    def test_matches_char_by_char(self):
        rnd = random.Random(1114)
        alpha = "01,\n#ab"
        for _ in range(3000):
            n = rnd.randrange(0, 300)
            old = "".join(rnd.choice(alpha) for _ in range(n))
            new = old
            for _k in range(rnd.randrange(0, 3)):
                a = rnd.randrange(0, len(new) + 1)
                b = min(len(new), a + rnd.randrange(0, 6))
                ins = "".join(rnd.choice(alpha) for _ in range(rnd.randrange(0, 6)))
                new = new[:a] + ins + new[b:]
            self.assertEqual(note_edit.text_diff_range(old, new),
                             _slow_diff(old, new), (old, new))

    def test_long_text(self):
        old = "1000,\n" * 50000
        new = old[:150001] + "2" + old[150002:]
        self.assertEqual(note_edit.text_diff_range(old, new), (150001, 150002, 150002))
        self.assertEqual(note_edit.text_diff_range(old, old), (len(old),) * 3)
        new2 = old + "0000,\n"
        self.assertEqual(note_edit.text_diff_range(old, new2),
                         _slow_diff(old, new2))


def _old_chart_items(text, body):
    """速くする前の chart_items(全小節を毎回読み直す)。"""
    from neotja.measure_math import parse_measure_lines
    items = []
    open_long = None
    ordinal = 0
    spans = note_edit._scan_measure_spans(
        text, body, note_edit._edit_branch, note_edit._edit_branch_path)
    for m, (a, b) in enumerate(spans):
        notes = ""
        for p in parse_measure_lines(text[a:b]):
            if p["type"] == "measure":
                notes = p["notes"]
                break
        L = len(notes)
        for i, c in enumerate(notes):
            if c == "0":
                continue
            k = note_edit._key(m, i, L)
            if c == "8":
                if open_long is not None:
                    open_long["tail"] = (m, i, L)
                    open_long["k1"] = k
                    open_long = None
                continue
            if c in note_edit.LONG_HEADS:
                it = {"char": c, "head": (m, i, L), "tail": None,
                      "k0": k, "k1": note_edit._INF_KEY, "ord": None}
                if c in note_edit.BALLOON_HEADS:
                    it["ord"] = ordinal
                    ordinal += 1
                items.append(it)
                open_long = it
            else:
                items.append({"char": c, "head": (m, i, L), "tail": None,
                              "k0": k, "k1": k, "ord": None})
    return items


def _forget_all(analyzer=None):
    note_edit._spans_memo = (None, None, None)
    note_edit._starts_memo = (None, None)
    note_edit._diff_memo = (None, None, None)
    note_edit._notes_cache.clear()
    if analyzer is not None:
        analyzer._line_range_memo = None


def _rand_measure(rnd):
    g = rnd.choice((1, 2, 4, 8, 16))
    s = "".join(rnd.choice("0000000011223456789") for _ in range(g))
    if rnd.random() < 0.1:
        cut = rnd.randrange(0, len(s) + 1)       # 1小節を2行に分ける
        s = s[:cut] + "\n" + s[cut:]
    if rnd.random() < 0.05:
        s += " // メモ, カンマ入り"
    return s + ","


def _rand_body(rnd, n):
    out = []
    i = 0
    while i < n:
        r = rnd.random()
        if r < 0.06:
            out.append("#BRANCHSTART p,1,2")
            for lv in rnd.sample(("N", "E", "M"), rnd.randrange(1, 4)):
                out.append("#" + lv)
                for _ in range(rnd.randrange(1, 4)):
                    out.append(_rand_measure(rnd))
                    i += 1
            out.append("#BRANCHEND")
        elif r < 0.1:
            out.append(rnd.choice(("#BPMCHANGE 155", "#SCROLL 1.5", "#GOGOSTART",
                                   "#MEASURE 3/4", "// コメント 5,6,", "")))
        else:
            out.append(_rand_measure(rnd))
            i += 1
    if rnd.random() < 0.3:
        out.append("1000")          # カンマの無い書きかけの末尾
    return out


def _rand_chart(rnd):
    lines = ["TITLE:test", "BPM:150"]
    for course in ("Oni", "Hard")[: rnd.randrange(1, 3)]:
        lines += ["COURSE:" + course, "BALLOON:5,6,7,8,9", "#START"]
        lines += _rand_body(rnd, rnd.randrange(5, 40))
        lines += ["#END", ""]
    return "\n".join(lines)


class IncrementalEditTests(unittest.TestCase):
    """覚えておいた結果を使い回しても、頭から数え直したのと同じになるか。"""

    def setUp(self):
        from neotja.tja_analyzer import TJACourseAnalyzer
        self.an = TJACourseAnalyzer({})
        _forget_all(self.an)

    def _check_same(self, text, course):
        an = self.an
        rng = an.course_line_range(text, course)
        self.assertEqual(rng, an._scan_course_line_range(text, course))
        if rng is None:
            return None
        body = note_edit.course_body_span(text, rng)
        starts = note_edit._line_starts(text)
        self.assertEqual(note_edit._line_starts_cached(text), starts)
        for lv, path in ((None, ()), ("N", ()), ("E", ()), ("M", ()),
                         ("auto", ("E", "M", "N", "M"))):
            with note_edit.editing_branch(lv, path):
                got = note_edit.measure_spans(text, body)
                want = note_edit._scan_measure_spans(
                    text, body, note_edit._edit_branch, note_edit._edit_branch_path)
                self.assertEqual(got, want, (lv, text))
                self.assertEqual(note_edit.chart_items(text, body),
                                 _old_chart_items(text, body))
        return rng

    def _rand_op(self, rnd):
        kind = rnd.choice(("key", "key", "place", "place", "long", "delete"))
        grid = rnd.choice((4, 8, 16, 12))
        m = rnd.randrange(0, 45)
        a = (m, rnd.randrange(0, grid))
        op = {"kind": kind, "a": a, "grid": grid}
        if kind == "key":
            op["char"] = rnd.choice("1234")
        elif kind == "place":
            op["char"] = rnd.choice("01234")
        elif kind == "long":
            op["char"] = rnd.choice("5679")
            op["b"] = (m + rnd.randrange(0, 6), rnd.randrange(0, grid))
        else:
            op["b"] = None
        return op

    def test_random_ops_match_full_scan(self):
        rnd = random.Random(20261114)
        for _chart in range(40):
            text = _rand_chart(rnd)
            course = rnd.choice(("Oni", "Hard"))
            for _step in range(40):
                rng = self._check_same(text, course)
                if rng is None:
                    break
                op = self._rand_op(rnd)
                lv = rnd.choice((None, "N", "E", "M", "auto"))
                path = ("M", "E", "N") if lv == "auto" else ()
                with note_edit.editing_branch(lv, path):
                    # 覚えた状態のまま(本文は中身が同じ別の文字列)
                    warm = note_edit.run_op((text + " ")[:-1], rng, dict(op))
                    tail_w = (note_edit.ensure_trailing_measure(
                        warm["text"], (rng[0], rng[1] + warm["text"].count("\n")
                                       - text.count("\n")))
                        if warm else None)
                    _forget_all(self.an)
                    cold = note_edit.run_op((text + " ")[:-1], rng, dict(op))
                    tail_c = (note_edit.ensure_trailing_measure(
                        cold["text"], (rng[0], rng[1] + cold["text"].count("\n")
                                       - text.count("\n")))
                        if cold else None)
                self.assertEqual(warm, cold, op)
                self.assertEqual(tail_w, tail_c, op)
                if warm is not None:
                    text = tail_w if tail_w is not None else warm["text"]
                # ときどき、作譜の操作ではない手での書き換えも混ぜる
                if rnd.random() < 0.3:
                    pos = rnd.randrange(0, len(text) + 1)
                    cut = min(len(text), pos + rnd.randrange(0, 3))
                    ins = "".join(rnd.choice("0123,\n#/ 8") for _ in range(rnd.randrange(0, 3)))
                    text = text[:pos] + ins + text[cut:]

    def test_long_note_spanning_many_measures(self):
        body = ["5000,"] + ["0000,"] * 30 + ["0008,", "0000,"]
        text = "COURSE:Oni\nBALLOON:4\n#START\n" + "\n".join(body) + "\n#END\n"
        rng = self.an.course_line_range(text, "Oni")
        for m, slot, ok in ((0, 0, True), (0, 1, False), (5, 1, False),
                            (31, 1, False), (32, 1, True)):
            self._check_same(text, "Oni")
            res = note_edit.run_op(text, rng, {"kind": "key", "a": (m, slot),
                                               "grid": 4, "char": "1"})
            # 連打の途中には置けない(頭の上なら連打ごと消える)
            self.assertEqual(res is not None, ok, (m, slot))


if __name__ == "__main__":
    unittest.main()
