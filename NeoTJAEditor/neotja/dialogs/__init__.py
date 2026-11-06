


def set_default_button(dialog, button):
    """その窓で Enter を受けるボタンを1つに決める。

    Qt は QPushButton の autoDefault が既定で True なので、決めておかない
    と Enter が「最初に見つけた押せるボタン」へ行く(利用者の報告
    2026-11-07「エンター押すと別の所にフォーカスがある」)。ほかを全部
    下ろしてから、効かせたい1つだけ上げる。
    """
    from PySide6.QtWidgets import QPushButton
    for b in dialog.findChildren(QPushButton):
        on = b is button
        b.setAutoDefault(on)
        b.setDefault(on)
