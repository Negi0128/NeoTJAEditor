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


if __name__ == "__main__":
    unittest.main()
