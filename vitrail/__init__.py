# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Vitrail -- a fast, GPU-rendered widget toolkit for pygame + ModernGL.

Like a stained-glass window, every pane of the interface is a lit piece of
glass on the GPU: one instanced draw call per frame.
"""

from . import animation, assets, material, nodes
from .animation import AnimationPlayer, Clip, CurveEditor, Keyframe, Timeline, Track, TransportBar
from .app import App
from .core import UI, Container, MouseEvent, Popup, Signal, UndoStack, Widget
from .dialogs import Dialog, FileDialog, InputDialog, MessageBox
from .expr import ExprError, evaluate
from .fuzzy import fuzzy_filter, fuzzy_score
from .assets import Asset, AssetBrowser, AssetGrid, MaterialBall, MaterialEditor, MaterialSlot
from .icons import ICONS, icon_names, register_icon
from .images import Image
from .layout import Canvas, Column, Row, ScrollArea, Separator, Spacer
from .menus import Menu, MenuBar, MenuItem, open_context_menu
from .pie import PieMenu
from .renderer import DrawList, Renderer
from .theme import Theme, rgb, shade, to_hex, with_alpha
from .tree import TreeItem, TreeView
from .widgets import (Button, Checkbox, ColorPicker, ColorSwatch, Dropdown, EnumRow, Label, LineEdit,
                      NumberField, Panel, ProgressBar, Tabs, TextField, Toggle, ValueWidget, VectorField)

__version__ = "0.3.0"

__all__ = [
    "animation", "assets", "material", "nodes",
    "App", "UI", "Widget", "Container", "Popup", "Signal", "UndoStack", "MouseEvent",
    "Column", "Row", "Canvas", "ScrollArea", "Spacer", "Separator", "Tabs",
    "Label", "Button", "Checkbox", "Toggle", "NumberField", "VectorField", "TextField", "LineEdit",
    "Dropdown", "EnumRow", "ProgressBar", "Panel", "ColorSwatch", "ColorPicker", "ValueWidget",
    "Menu", "MenuItem", "MenuBar", "open_context_menu", "PieMenu",
    "TreeItem", "TreeView", "Image",
    "Clip", "Track", "Keyframe", "AnimationPlayer", "Timeline", "CurveEditor", "TransportBar",
    "Asset", "AssetBrowser", "AssetGrid", "MaterialBall", "MaterialEditor", "MaterialSlot",
    "Dialog", "MessageBox", "InputDialog", "FileDialog",
    "ICONS", "icon_names", "register_icon",
    "Theme", "rgb", "shade", "to_hex", "with_alpha",
    "DrawList", "Renderer", "evaluate", "ExprError", "fuzzy_filter", "fuzzy_score",
]
