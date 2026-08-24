# -*- coding: utf-8 -*-
"""
sat_panel_manager.py

Thin wrapper around SatPanel that keeps piMap.py clean.
This version MATCHES the current SatPanel API exactly.
"""

from __future__ import annotations

import pygame
import config
from sat_panel import SatPanel


class SatPanelManager:
    """
    Owns the SatPanel and provides a stable interface to piMap.
    """

    def __init__(self, screen_w: int, screen_h: int):
        self.panel = SatPanel(
            screen_w=screen_w,
            screen_h=screen_h,
            width=380,
            top=getattr(config, "SPACE_BAR_HEIGHT", 90),
            bottom_margin=0,
            idle_hide_s=getattr(config, "FLYOUT_IDLE_HIDE_SECONDS", 10),
        )

    # ------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------

    def update(self, dt: float, mouse_pos: tuple[int, int],
               idle_s: float = 0.0) -> None:
        self.panel.update(dt, idle_s)

    def draw(self, screen: pygame.Surface, *_fonts) -> None:
        # SatPanel manages its own fonts internally
        self.panel.draw(screen)

    # ------------------------------------------------------------
    # Event routing
    # ------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> bool:
        """
        Pass events to the panel.
        Always returns False so piMap continues handling events.
        """
        self.panel.handle_event(event)
        return False

    def wheel_is_for_panel(self, mouse_pos: tuple[int, int]) -> bool:
        mx, my = mouse_pos
        return self._point_in_panel(mx, my)

    def on_wheel(self, wheel_y: int) -> None:
        # SatPanel handles wheel via MOUSEBUTTONDOWN (4/5),
        # so nothing needed here.
        pass

    # ------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------

    def selected_sat_ids(self) -> list[str]:
        return self.panel.get_enabled_sat_ids()

    def selected_sats(self):
        return self.panel.get_enabled_sats()

    def set_catalog(self, defs) -> None:
        self.panel.set_catalog(defs)

    def set_rows(self, rows) -> None:
        self.panel.set_rows(rows)

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------

    def _point_in_panel(self, x: int, y: int) -> bool:
        return (
            x >= int(self.panel.x)
            and x <= int(self.panel.x) + self.panel.width
            and y >= self.panel.top
            and y <= self.panel.top + self.panel.height
        )
