import unittest

from neotja import note_edit as edit


class CompactNotesTests(unittest.TestCase):
    def put(self, text, slot, char, grid=16, measure=0):
        start, end, new, _ = edit.set_slot(text, (0, len(text)), measure, slot, grid, char)
        return text[:start] + new + text[end:]

    def test_head_only_and_growing_then_shrinking(self):
        for grid in (8, 16):
            with self.subTest(grid=grid):
                self.assertEqual(self.put('0,', 0, '1', grid), '1,')
        text = self.put('1,\n00000000,', 1, '2')
        self.assertEqual(text, '12' + '0' * 14 + ',\n00000000,')
        self.assertEqual(self.put(text, 1, '0'), '1,\n00000000,')
        self.assertEqual(self.put('1,', 8, '2'), '12,')

    def test_command_comments_and_roll_end_hold_their_positions(self):
        text = '5000 // tip 123\n#SCROLL 2\n// 34\n000080000000,'
        text = self.put(text, 12, '1')
        self.assertEqual(text, '5 // tip 123\n#SCROLL 2\n// 34\n081,')
        text = self.put(text, 1, '2')
        self.assertEqual(text, '5200 // tip 123\n#SCROLL 2\n// 34\n000080001000,')

    def test_append_and_move_to_another_measure(self):
        self.assertEqual(self.put('0,\n', 0, '1', measure=2), '0,\n0,\n1,\n')
        text = 'COURSE:oni\n#START\n12000000,\n0,\n#END\n'
        result = edit.op_move_items(text, (2, 5),
                                    [{'kind': 'note', 'pos': (1, 8)}], 14, 16)
        self.assertEqual(result['text'], 'COURSE:oni\n#START\n1,\n2,\n#END\n')
