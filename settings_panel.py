#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""settings_panel.py - Slide-out user-settings panel on the LEFT edge.

Mirrors the satellite panel's hover-to-open / auto-hide behavior, and adds an
idle timeout the sat panel does not have: the panel also closes after the mouse
has not moved for idle_hide_s, even while the cursor is parked inside it or on
the hot edge (which otherwise held it open indefinitely). Hosts
setting widgets. First widgets: TV (HDMI-2) output volume and WSPR alert-chime
volume, each a click/drag slider, plus Mute-TV and Test-chime buttons.

Sliders are generic: each is a dict {key, label, get, set, fmt} so more
settings can be added later without touching this file's mechanics.
"""

from __future__ import annotations

import pygame


class SettingsPanel:
    def __init__(self, screen_w, screen_h, *, top=70, width=300, bottom_margin=0,
                 idle_hide_s=10.0):
        self.screen_w = int(screen_w)
        self.screen_h = int(screen_h)
        self.width = int(width)
        self.top = int(top)
        self.bottom_margin = int(bottom_margin)
        self.height = int(self.screen_h - self.top - self.bottom_margin)

        # Slide-out geometry (LEFT edge): hidden off-screen at -width.
        self.hidden_x = -self.width
        self.shown_x = 0
        self.x = float(self.hidden_x)
        self.target_x = float(self.hidden_x)

        self.edge_hot_zone = 8
        self.auto_hide_delay_s = 0.8      # grace period after the cursor leaves
        # Close after this long with no mouse motion anywhere, regardless of
        # where the cursor is resting. 0 disables the idle timeout.
        self.idle_hide_s = float(idle_hide_s)
        self._time_since_hover = 0.0

        self.font = pygame.font.SysFont("consolas", 18)
        self.font_small = pygame.font.SysFont("consolas", 14)

        self.bg = (8, 9, 12)
        self.border = (60, 95, 130)
        self.text = (205, 218, 232)
        self.dim = (135, 150, 165)
        self.track = (40, 46, 56)
        self.knob = (120, 190, 245)
        self.btn = (30, 40, 52)
        self.btn_hot = (46, 62, 80)

        self.pad = 16
        self.slider_h = 8
        self.knob_r = 9
        self.row_h = 62

        self.sliders = []          # list of dicts: key,label,get,set,fmt
        self.buttons = []          # list of dicts: key,label,on_click
        self.choosers = []         # list of dicts: label, options(), get(), on_select(name)
        self._drag = None          # index of slider being dragged

    # ------------------------------------------------------------------
    def set_sliders(self, sliders):
        self.sliders = list(sliders)

    def set_buttons(self, buttons):
        self.buttons = list(buttons)

    def set_choosers(self, choosers):
        self.choosers = list(choosers)

    def visible(self):
        return self.x > self.hidden_x + 1

    def _point_in_panel(self, x, y):
        return (x >= int(self.x) and x <= int(self.x) + self.width
                and y >= self.top and y <= self.top + self.height)

    # ------------------------------------------------------------------
    # Layout (shared by draw + hit-testing so they never drift)
    # ------------------------------------------------------------------
    def _layout(self):
        px = int(self.x)
        inner_l = px + self.pad
        inner_r = px + self.width - self.pad
        y = self.top + self.pad + 30  # below title + separator
        tracks = []
        for _ in self.sliders:
            ty = y + 26
            tracks.append(pygame.Rect(inner_l, ty, inner_r - inner_l, self.slider_h))
            y += self.row_h
        # Choosers: each is a label then one clickable row per option.
        chooser_groups = []
        for ch in self.choosers:
            y += 24  # room for the chooser label
            rects = []
            for _ in ch["options"]():
                rects.append(pygame.Rect(inner_l, y, inner_r - inner_l, 26))
                y += 28
            chooser_groups.append(rects)
            y += 8
        # Buttons row near the bottom.
        btn_rects = []
        if self.buttons:
            bw = (inner_r - inner_l - 10 * (len(self.buttons) - 1)) // max(1, len(self.buttons))
            by = self.top + self.height - self.pad - 30
            bx = inner_l
            for _ in self.buttons:
                btn_rects.append(pygame.Rect(bx, by, bw, 30))
                bx += bw + 10
        return tracks, chooser_groups, btn_rects

    # ------------------------------------------------------------------
    def handle_event(self, event) -> bool:
        """Returns True if the event was consumed by the panel."""
        if event.type == pygame.MOUSEMOTION:
            if self._drag is not None:
                self._set_from_x(self._drag, event.pos[0])
                return True
            return False  # let plain motion reach the map (keeps last_motion fresh)

        if event.type == pygame.MOUSEWHEEL:
            return self._point_in_panel(*pygame.mouse.get_pos())  # swallow over panel

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            was = self._drag is not None
            self._drag = None
            return was or self._point_in_panel(*event.pos)

        if event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos
            if not self._point_in_panel(mx, my):
                return False
            if event.button in (4, 5):
                return True  # swallow wheel so it doesn't zoom the map
            if event.button == 1:
                tracks, chooser_groups, btn_rects = self._layout()
                for i, tr in enumerate(tracks):
                    if tr.inflate(16, 22).collidepoint(mx, my):
                        self._drag = i
                        self._set_from_x(i, mx)
                        return True
                for ch, rects in zip(self.choosers, chooser_groups):
                    for name, r in zip(ch["options"](), rects):
                        if r.collidepoint(mx, my):
                            ch["on_select"](name)
                            return True
                for b, br in zip(self.buttons, btn_rects):
                    if br.collidepoint(mx, my):
                        b["on_click"]()
                        return True
            return True  # click anywhere else on the panel is still "ours"
        return False

    def _set_from_x(self, i, mx):
        tr = self._layout()[0][i]  # tracks are the first element
        v = (mx - tr.x) / float(max(1, tr.w))
        v = 0.0 if v < 0 else 1.0 if v > 1 else v
        self.sliders[i]["set"](v)

    # ------------------------------------------------------------------
    def update(self, dt, idle_s=0.0):
        """idle_s: seconds since the last real mouse motion (piMap owns that
        clock). Hovering the edge or the panel body holds it open, but only
        while the mouse is actually being used -- otherwise a cursor left
        parked on the left edge pins the panel open forever."""
        mx, my = pygame.mouse.get_pos()
        hovering = mx <= self.edge_hot_zone or self._point_in_panel(mx, my)
        if self._drag is not None:
            stay_open = True          # never yank the panel mid-drag
        elif self.idle_hide_s > 0 and idle_s >= self.idle_hide_s:
            stay_open = False         # mouse has gone still -- close even if hovered
        else:
            stay_open = hovering
        if stay_open:
            self.target_x = float(self.shown_x)
            self._time_since_hover = 0.0
        else:
            self._time_since_hover += dt
            if self._time_since_hover >= self.auto_hide_delay_s:
                self.target_x = float(self.hidden_x)
        self.x += (self.target_x - self.x) * min(1.0, 18.0 * dt)

    # ------------------------------------------------------------------
    def draw(self, screen):
        if not self.visible():
            return
        px = int(self.x)
        panel_rect = pygame.Rect(px, self.top, self.width, self.height)
        pygame.draw.rect(screen, self.bg, panel_rect, border_radius=10)
        pygame.draw.rect(screen, self.border, panel_rect, width=2, border_radius=10)

        screen.blit(self.font.render("SETTINGS", True, self.text),
                    (px + self.pad, self.top + self.pad))
        y_sep = self.top + self.pad + 26
        pygame.draw.line(screen, self.border, (px + self.pad, y_sep),
                         (panel_rect.right - self.pad, y_sep), 1)

        mouse = pygame.mouse.get_pos()
        tracks, chooser_groups, btn_rects = self._layout()

        for i, s in enumerate(self.sliders):
            tr = tracks[i]
            val = max(0.0, min(1.0, float(s["get"]())))
            label = s["label"]
            valtxt = s["fmt"](val) if s.get("fmt") else f"{int(round(val * 100))}%"
            screen.blit(self.font_small.render(label, True, self.dim), (tr.x, tr.y - 22))
            vt = self.font_small.render(valtxt, True, self.text)
            screen.blit(vt, (tr.right - vt.get_width(), tr.y - 22))
            # track + filled portion + knob
            pygame.draw.rect(screen, self.track, tr, border_radius=4)
            fill = pygame.Rect(tr.x, tr.y, int(tr.w * val), tr.h)
            pygame.draw.rect(screen, self.knob, fill, border_radius=4)
            kx = tr.x + int(tr.w * val)
            ky = tr.centery
            pygame.draw.circle(screen, self.knob, (kx, ky), self.knob_r)
            pygame.draw.circle(screen, (10, 12, 16), (kx, ky), self.knob_r, 2)

        for ch, rects in zip(self.choosers, chooser_groups):
            if not rects:
                continue
            screen.blit(self.font_small.render(ch["label"], True, self.dim),
                        (rects[0].x, rects[0].y - 22))
            current = ch["get"]()
            for name, r in zip(ch["options"](), rects):
                sel = (name == current)
                hot = r.collidepoint(mouse)
                if sel or hot:
                    pygame.draw.rect(screen, self.btn_hot if sel else self.btn, r,
                                     border_radius=5)
                if sel:
                    pygame.draw.rect(screen, self.knob, r, 1, border_radius=5)
                mark = ">" if sel else " "
                col = self.text if (sel or hot) else self.dim
                screen.blit(self.font_small.render(f"{mark} {name}", True, col),
                            (r.x + 8, r.centery - 8))

        for b, br in zip(self.buttons, btn_rects):
            hot = br.collidepoint(mouse)
            pygame.draw.rect(screen, self.btn_hot if hot else self.btn, br, border_radius=6)
            pygame.draw.rect(screen, self.border, br, 1, border_radius=6)
            lab = self.font_small.render(b["label"](), True, self.text) \
                if callable(b["label"]) else self.font_small.render(b["label"], True, self.text)
            screen.blit(lab, (br.centerx - lab.get_width() // 2,
                              br.centery - lab.get_height() // 2))

        hint = self.font_small.render("Hover left edge to open", True, self.dim)
        screen.blit(hint, (px + self.pad, panel_rect.bottom - 20))
