#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sat_panel.py - Slide-out satellite list with checkboxes.

The catalog (all available sats) is supplied via set_catalog(); the display
order and the per-row "time to next pass" labels are supplied via set_rows()
by the caller (piMap), which owns the pass-prediction. Enable state is keyed
by NORAD id. ASCII-only text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import pygame


ISS_NORAD = 25544


@dataclass(frozen=True)
class SatDef:
    sat_id: str
    label: str
    norad_id: int
    color: tuple = (0, 0, 0)


class SatPanel:
    def __init__(
        self,
        screen_w: int,
        screen_h: int,
        *,
        width: int = 360,
        top: int = 70,
        bottom_margin: int = 40,
        idle_hide_s: float = 10.0,
    ) -> None:
        self.screen_w = int(screen_w)
        self.screen_h = int(screen_h)
        self.width = int(width)
        self.top = int(top)
        self.bottom_margin = int(bottom_margin)
        self.height = int(self.screen_h - self.top - self.bottom_margin)

        # Slide-out geometry
        self.hidden_x = self.screen_w
        self.shown_x = self.screen_w - self.width
        self.x = float(self.hidden_x)
        self.target_x = float(self.hidden_x)

        # Hover/open behavior
        self.edge_hot_zone = 8
        self.auto_hide_delay_s = 0.8      # grace period after the cursor leaves
        # Also close after this long with no mouse motion, even while the cursor
        # is parked inside the panel or on the hot edge. 0 disables it.
        self.idle_hide_s = float(idle_hide_s)
        self._time_since_hover = 0.0

        # Scroll behavior
        self.scroll_y = 0.0
        self.scroll_v = 0.0
        self.max_scroll = 0.0
        self._dragging_scroll = False
        self._last_mouse_y = 0

        self.font = pygame.font.SysFont("consolas", 17)
        self.font_small = pygame.font.SysFont("consolas", 13)

        self.bg = (8, 8, 10)
        self.border = (120, 95, 40)
        self.text = (230, 210, 160)
        self.dim = (150, 140, 115)
        self.check = (230, 210, 160)
        self.time_col = (150, 220, 255)
        self.now_col = (120, 255, 120)

        self.row_h = 30
        self.pad = 14
        self.checkbox_size = 16
        self.chip_size = 12

        # Catalog: all available sats. enabled keyed by NORAD id.
        self.catalog: List[SatDef] = []
        self.enabled: Dict[int, bool] = {}
        # Display rows: ordered (SatDef, time_label).
        self.rows: List[Tuple[SatDef, str]] = []

        self._recalc_scroll_bounds()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_catalog(self, defs: List[SatDef]) -> None:
        """Set the full list of available sats. Preserves existing toggles;
        ISS defaults on the first time it appears."""
        self.catalog = list(defs)
        new_enabled: Dict[int, bool] = {}
        for d in self.catalog:
            if d.norad_id in self.enabled:
                new_enabled[d.norad_id] = self.enabled[d.norad_id]
            else:
                new_enabled[d.norad_id] = (d.norad_id == ISS_NORAD)
        self.enabled = new_enabled
        if not self.rows:
            self.rows = [(d, "") for d in self.catalog]
        self._recalc_scroll_bounds()

    def set_rows(self, rows: List[Tuple[SatDef, str]]) -> None:
        """Set the ordered display rows (SatDef, time_label)."""
        self.rows = rows

    def get_enabled_sats(self) -> List[SatDef]:
        return [d for d in self.catalog if self.enabled.get(d.norad_id)]

    def get_enabled_sat_ids(self) -> List[str]:
        return [d.sat_id for d in self.catalog if self.enabled.get(d.norad_id)]

    def resize(self, screen_w: int, screen_h: int) -> None:
        self.screen_w = int(screen_w)
        self.screen_h = int(screen_h)
        self.height = int(self.screen_h - self.top - self.bottom_margin)
        self.hidden_x = self.screen_w
        self.shown_x = self.screen_w - self.width
        self.x = min(self.x, float(self.hidden_x))
        self.target_x = min(self.target_x, float(self.hidden_x))
        self._recalc_scroll_bounds()

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.MOUSEMOTION:
            mx, my = event.pos
            if mx >= self.screen_w - self.edge_hot_zone:
                self.target_x = float(self.shown_x)
                self._time_since_hover = 0.0
            elif self._point_in_panel(mx, my):
                self._time_since_hover = 0.0

            if self._dragging_scroll:
                dy = my - self._last_mouse_y
                self._last_mouse_y = my
                self.scroll_y -= float(dy)
                self._clamp_scroll()

        elif event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos
            if self._point_in_panel(mx, my):
                if event.button == 4:
                    self.scroll_v -= 900.0
                    return
                if event.button == 5:
                    self.scroll_v += 900.0
                    return
                if event.button == 1:
                    if self._handle_click(mx, my):
                        return
                    self._dragging_scroll = True
                    self._last_mouse_y = my
                    return

        elif event.type == pygame.MOUSEBUTTONUP:
            if event.button == 1:
                self._dragging_scroll = False

    def update(self, dt: float, idle_s: float = 0.0) -> None:
        """idle_s: seconds since the last real mouse motion, from piMap. Opening
        stays event-driven (a MOUSEMOTION in the hot zone), so this only ever
        decides when to close."""
        mx, my = pygame.mouse.get_pos()
        hovering = (mx >= self.screen_w - self.edge_hot_zone) or self._point_in_panel(mx, my)
        if self._dragging_scroll:
            stay_open = True          # never yank the panel mid scroll-drag
        elif self.idle_hide_s > 0 and idle_s >= self.idle_hide_s:
            stay_open = False         # mouse has gone still -- close even if hovered
        else:
            stay_open = hovering
        if stay_open:
            self._time_since_hover = 0.0
        else:
            self._time_since_hover += dt
            if self._time_since_hover >= self.auto_hide_delay_s:
                self.target_x = float(self.hidden_x)

        speed = 18.0
        self.x += (self.target_x - self.x) * min(1.0, speed * dt)

        if not self._dragging_scroll:
            if abs(self.scroll_v) > 1.0:
                self.scroll_y += self.scroll_v * dt
                self.scroll_v *= (0.86 ** (dt * 60.0))
                self._clamp_scroll()
            else:
                self.scroll_v = 0.0

    def draw(self, screen: pygame.Surface) -> None:
        panel_rect = pygame.Rect(int(self.x), self.top, self.width, self.height)
        pygame.draw.rect(screen, self.bg, panel_rect, border_radius=10)
        pygame.draw.rect(screen, self.border, panel_rect, width=2, border_radius=10)

        title = f"SATELLITES  ({len(self.catalog)})" if self.catalog else "SATELLITES"
        screen.blit(self.font.render(title, True, self.text),
                    (panel_rect.x + self.pad, panel_rect.y + self.pad))

        y_sep = panel_rect.y + self.pad + 26
        pygame.draw.line(screen, self.border,
                         (panel_rect.x + self.pad, y_sep),
                         (panel_rect.right - self.pad, y_sep), 1)

        list_rect = pygame.Rect(panel_rect.x, y_sep + 8, self.width,
                                self.height - (y_sep - panel_rect.y) - 18)
        prev_clip = screen.get_clip()
        screen.set_clip(list_rect)

        start_y = list_rect.y - int(self.scroll_y)
        for i, (s, tlabel) in enumerate(self.rows):
            row_y = start_y + i * self.row_h
            if row_y + self.row_h < list_rect.top or row_y > list_rect.bottom:
                continue

            on = self.enabled.get(s.norad_id, False)
            x = list_rect.x + self.pad

            # Checkbox
            cb_y = row_y + (self.row_h - self.checkbox_size) // 2
            cb = pygame.Rect(x, cb_y, self.checkbox_size, self.checkbox_size)
            pygame.draw.rect(screen, self.border, cb, 2, border_radius=3)
            if on:
                pygame.draw.line(screen, self.check, (cb.x + 3, cb.y + 8), (cb.x + 6, cb.y + 12), 2)
                pygame.draw.line(screen, self.check, (cb.x + 6, cb.y + 12), (cb.x + 13, cb.y + 3), 2)

            # Color chip
            chip_x = cb.right + 8
            chip_y = row_y + (self.row_h - self.chip_size) // 2
            chip = pygame.Rect(chip_x, chip_y, self.chip_size, self.chip_size)
            pygame.draw.rect(screen, s.color, chip, border_radius=2)
            pygame.draw.rect(screen, self.dim, chip, 1, border_radius=2)

            # Time-to-pass (right aligned)
            tcolor = self.now_col if tlabel == "now" else self.time_col if tlabel not in ("", "--") else self.dim
            tsurf = self.font_small.render(tlabel, True, tcolor)
            trect = tsurf.get_rect()
            trect.right = list_rect.right - self.pad
            trect.centery = row_y + self.row_h // 2
            screen.blit(tsurf, trect)

            # Name (truncated to fit before the time column)
            name_x = chip.right + 8
            avail = trect.left - 6 - name_x
            label = self._fit_text(s.label, avail)
            ncolor = self.text if on else self.dim
            nsurf = self.font.render(label, True, ncolor)
            screen.blit(nsurf, (name_x, row_y + (self.row_h - nsurf.get_height()) // 2))

        screen.set_clip(prev_clip)

        hint = self.font_small.render("Sorted by next pass. Click to toggle.", True, self.dim)
        screen.blit(hint, (panel_rect.x + self.pad, panel_rect.bottom - 20))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fit_text(self, text: str, max_w: int) -> str:
        if max_w <= 0:
            return ""
        if self.font.size(text)[0] <= max_w:
            return text
        ell = "..."
        while text and self.font.size(text + ell)[0] > max_w:
            text = text[:-1]
        return text + ell if text else ""

    def _point_in_panel(self, x: int, y: int) -> bool:
        return (x >= int(self.x) and x <= int(self.x) + self.width
                and y >= self.top and y <= self.top + self.height)

    def _recalc_scroll_bounds(self) -> None:
        content_h = max(len(self.rows), len(self.catalog)) * self.row_h
        visible_list_h = max(1, self.height - 80)
        self.max_scroll = max(0.0, float(content_h - visible_list_h))
        self._clamp_scroll()

    def _clamp_scroll(self) -> None:
        if self.scroll_y < 0.0:
            self.scroll_y = 0.0
            self.scroll_v *= 0.4
        elif self.scroll_y > self.max_scroll:
            self.scroll_y = self.max_scroll
            self.scroll_v *= 0.4

    def _handle_click(self, mx: int, my: int) -> bool:
        panel_rect = pygame.Rect(int(self.x), self.top, self.width, self.height)
        y_sep = panel_rect.y + self.pad + 26
        list_rect = pygame.Rect(panel_rect.x, y_sep + 8, self.width,
                                self.height - (y_sep - panel_rect.y) - 18)
        if not list_rect.collidepoint(mx, my):
            return False

        local_y = (my - list_rect.y) + int(self.scroll_y)
        idx = local_y // self.row_h
        if idx < 0 or idx >= len(self.rows):
            return False

        norad = self.rows[idx][0].norad_id
        self.enabled[norad] = not self.enabled.get(norad, False)
        return True
