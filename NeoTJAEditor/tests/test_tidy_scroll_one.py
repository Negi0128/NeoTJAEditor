import unittest

from neotja.tja_tidy import tidy_text, same_sound


class TidyScrollOneTests(unittest.TestCase):
    def test_explicit_scroll_one_is_preserved_even_when_redundant(self):
        for body in ('#SCROLL 1\n1111,\n#SCROLL 1\n1111,',
                     '#SCROLL 1\n#SCROLL 1.00\n1111,',
                     '#SCROLL 1\n#SCROLL 2\n1111,'):
            with self.subTest(body=body):
                text = 'BPM:120\nCOURSE:oni\n#START\n' + body + '\n#END\n'
                result, _ = tidy_text(text)
                self.assertEqual(result.count('#SCROLL 1'), text.count('#SCROLL 1'))
                self.assertTrue(same_sound(text, result)[0])

    def test_other_redundant_scroll_values_are_still_tidied(self):
        text = 'BPM:120\nCOURSE:oni\n#START\n#SCROLL 2\n1111,\n#SCROLL 2\n1111,\n#END\n'
        result, _ = tidy_text(text)
        self.assertEqual(result.count('#SCROLL 2'), 1)


if __name__ == '__main__':
    unittest.main()
