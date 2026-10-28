"""Light theme for the editor: h3d.theme() overrides the editor's THEME colours."""


def register(h3d):
    h3d.theme(
        bg="#EEF0F3", panel="#F8F9FB", panel_dark="#E3E6EB", border="#CDD2DA",
        button="#E2E5EA", button_hover="#D5DAE2", selected="#C9DBFA",
        accent="#2F6FD6", accent_dim="#A9C6F7", play_green="#3E9B57", green_text="#2E7D46",
        danger="#D14343", danger_hover="#E05252",
        text="#1F2328", text_bright="#0B0D10", text_dim="#5E6673",
    )
