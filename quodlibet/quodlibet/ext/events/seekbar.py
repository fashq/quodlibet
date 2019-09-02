# -*- coding: utf-8 -*-
# Copyright 2016 Christoph Reiter
#           2019 fashqakabgd
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from gi.repository import GObject, Gtk, GLib

from quodlibet import _
from quodlibet import app
from quodlibet import config
from quodlibet.plugins.events import EventPlugin
from quodlibet.qltk import Icons, find_widgets
from quodlibet.qltk.seekbutton import TimeLabel, SeekButton
from quodlibet.util import connect_destroy
from configparser import NoOptionError


class SynchronizedTimeTracker(GObject.GObject):
    """Emits exactly one tick every second as long as the player is actively
       playing.

    Tries to synchronize with playback so that the tick is emitted exactly
    at full seconds of playback position. The maximum allowed disparity between
    tick and playback position is 30 ms (self.offset + self.max_delta) behind
    actual playback position.
    """

    __gsignals__ = {
        'tick': (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, player):
        GObject.GObject.__init__(self)
        self._player = player

        # offset (ms) is additional time added to full seconds just to be sure
        # we are waking up right after full seconds of player's position
        self.offset = 10

        # max_delta (ms) is used to decide if new interval should be set to
        # synchronize itself with playback
        self.max_delta = 20

        self.__source_id = None
        self.__tick_prev_sec = 0

        self.__sigs = [
            player.connect("paused", self.__disable),
            player.connect("unpaused", self.__enable),
            player.connect("seek", self.__seek),
            player.connect("song-started", self.__song_started),
        ]

        if not player.paused:
            self.__enable()

    def __song_started(self, player, song):
        if self.__tick_prev_sec == 1:
            self.__tick_prev_sec = 0

    def __seek(self, player, song, seek):
        self.__tick_prev_sec = seek // 1000
        if not player.paused:
            self.restart()

    def __run(self, last_interval):
        position = self._player.get_position()
        current_sec = position // 1000
        if current_sec != self.__tick_prev_sec and current_sec != 0:
            self.emit("tick")
            self.__tick_prev_sec = current_sec
        interval = 1000 + self.offset - (position % 1000)
        if abs(last_interval - interval) > self.max_delta:
            self.__source_id = GLib.timeout_add(interval, self.__run, interval)
        else:
            return True

    def __enable(self, *args):
        if self.__source_id is None:
            position = self._player.get_position()
            interval = 1000 + self.offset - (position % 1000)
            self.__source_id = GLib.timeout_add(interval, self.__run, interval)

    def __disable(self, *args):
        if self.__source_id is not None:
            GLib.source_remove(self.__source_id)
            self.__source_id = None

    def restart(self, *args):
        self.__disable()
        self.__enable()

    def destroy(self):
        self.__disable()
        for signal_id in self.__sigs:
            self._player.disconnect(signal_id)


class SeekBar(Gtk.Box):

    def __init__(self, player, library):
        Gtk.Box.__init__(self)
        self.elapsed_label = TimeLabel()
        self.elapsed_label.set_name('elapsed-label')
        self.remaining_label = TimeLabel()
        self.remaining_label.set_name('remaining-label')
        self.remaining_label.set_width_chars(6)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self.scale.set_name('seek-bar')
        self.scale.set_adjustment(Gtk.Adjustment.new(0, 0, 0, 30, 5, 0))
        self.scale.set_digits(3)
        self.scale.set_show_fill_level(False)
        self.scale.set_draw_value(False)
        self._timer_modes = ['both', 'elapsed', 'remaining']
        self.c_timer_mode = __name__ + '_timer_mode'

        self.__timer_mode = 'both'
        self.__pressed_lmb = False
        self.__source_id = None

        self.elapsed_button = Gtk.Button()

        self.box = Gtk.Box(spacing=3)
        self.elapsed_button.add(self.box)
        self.elapsed_button.set_relief(Gtk.ReliefStyle.NONE)

        self.pack_start(self.scale, True, True, 0)

        try:
            self.__timer_mode = config.get('plugins', self.c_timer_mode)
        except NoOptionError:
            config.set('plugins', self.c_timer_mode, 'both')
        else:
            if self.__timer_mode not in self._timer_modes:
                self.__timer_mode = 'both'

        self._set_timer_mode(self.__timer_mode)

        self.elapsed_label.show()
        self.remaining_label.show()
        self.box.show()
        self.elapsed_button.show()
        self.scale.show()

        self.elapsed_button.connect('clicked', self._on_timer_clicked)
        self.scale.connect('button-release-event',
                           self._on_button_release, player)
        self.__id_button_press_event = self.scale.connect(
            'button-press-event', self._on_button_press, player)
        self.__id_change_value = self.scale.connect(
            'change-value', self._on_scale_value_change_request, player)

        self.__id_value_changed = self.scale.connect(
            'value-changed', self._on_scale_value_changed, player)
        GObject.signal_handler_block(self.scale, self.__id_value_changed)

        self._tracker = SynchronizedTimeTracker(player)
        self._tracker.connect('tick', self._on_tick, player)

        connect_destroy(player, 'seek', self._on_seek)
        connect_destroy(player, 'song-started', self._on_song_start)
        connect_destroy(player, "notify::seekable", self._update)

        self.connect("destroy", self._on_destroy)

        self._update(player)

    def _set_timer_mode(self, mode='both'):

        widgets = find_widgets(self, TimeLabel)
        for elem in widgets:
            name = elem.get_name()
            if name == 'elapsed-label' or name == 'remaining-label':
                self.remove(elem)

        widgets = find_widgets(self.box, TimeLabel)
        for elem in widgets:
            name = elem.get_name()
            if name == 'elapsed-label' or name == 'remaining-label':
                self.box.remove(elem)

        if mode == 'both':
            self.box.pack_start(self.elapsed_label, True, True, 0)
            self.pack_start(self.remaining_label, False, True, 0)
        elif mode == 'remaining':
            self.box.pack_start(self.remaining_label, True, True, 0)
        elif mode == 'elapsed':
            self.box.pack_start(self.elapsed_label, True, True, 0)

        self.__timer_mode = mode
        config.set('plugins', self.c_timer_mode, self.__timer_mode)

    def _on_destroy(self, *args):
        self._tracker.destroy()

    def _on_timer_clicked(self, button):

        i = self._timer_modes.index(self.__timer_mode)
        self._set_timer_mode(self._timer_modes[i-1])

    def _on_seek(self, player, song, ms):
        self._update_labels(player, ms)
        self._update_scale(player, ms)

    def _on_button_press(self, scale, event, player):
        GObject.signal_handler_block(self.scale, self.__id_button_press_event)
        self.__pressed_lmb = True
        GObject.signal_handler_unblock(self.scale, self.__id_value_changed)
        GObject.signal_handler_block(self.scale, self.__id_change_value)
        self.__pressed_lmb_scale_value = scale.get_value()

    def _on_button_release(self, scale, event, player):
        GObject.signal_handler_block(self.scale, self.__id_value_changed)
        value = scale.get_value()
        if player.seekable and self.__pressed_lmb_scale_value != value:
            player.seek(value * 1000)
        GObject.signal_handler_unblock(self.scale, self.__id_change_value)
        self.__pressed_lmb = False
        GObject.signal_handler_unblock(
            self.scale, self.__id_button_press_event)

    def _on_scale_value_changed(self, scale, player):
        elapsed = scale.get_value()
        remaining = elapsed - player.info("~#length")
        self.elapsed_label.set_time(elapsed)
        self.remaining_label.set_time(remaining)

    def _on_scale_value_change_request(self, scale, scroll, value, player):
        self.__pressed_lmb = True
        if self.__source_id is not None:
            GLib.source_remove(self.__source_id)
        self.__source_id = GLib.timeout_add(
            200, self.__scroll_timeout, value, player)

    def __scroll_timeout(self, value, player):
        if player.seekable:
            player.seek(value * 1000)
        self.__pressed_lmb = False
        self.__source_id = None

    def _on_tick(self, tracker, player):
        if not self.__pressed_lmb:
            self._update_labels(player)
            self._update_scale(player)

    def _update_labels(self, player, ms=None):
        if ms is not None:
            elapsed = ms // 1000
        else:
            elapsed = player.get_position() // 1000
        remaining = elapsed - player.info("~#length")
        self.elapsed_label.set_time(elapsed)
        self.remaining_label.set_time(remaining)

    def _update_scale(self, player, ms=None):
        if ms is not None:
            pval = ms / 1000
        else:
            pval = player.get_position() / 1000
        sval = self.scale.get_value()
        if (abs(pval - sval) > 0.001):
            self.scale.set_value(pval)

    def _on_song_start(self, player, *args):
        self._update(player, song_start=True)

    def _update(self, player, *args, song_start=False):
        if player.info:
            self.scale.set_range(0, player.info("~#length"))
        else:
            self.scale.set_range(0, 1)

        if player.seekable:
            self.elapsed_label.set_disabled(False)
            self.remaining_label.set_disabled(False)
            self.set_sensitive(True)
            if song_start:
                self._update_labels(player, 0)
                self._update_scale(player, 0)
            else:
                self._update_labels(player)
                self._update_scale(player)
        else:
            self.scale.set_value(0)
            self.elapsed_label.set_disabled(True)
            self.remaining_label.set_disabled(True)
            self.set_sensitive(False)


class SeekBarPlugin(EventPlugin):
    PLUGIN_ID = "SeekBar"
    PLUGIN_NAME = _("Alternative Seek Bar")
    PLUGIN_DESC = _("Alternative seek bar which is always visible and spans "
                    "the whole window width.")
    PLUGIN_ICON = Icons.GO_JUMP

    def enabled(self):
        self.bar = SeekBar(app.player, app.librarian)
        self.buttons_table = find_widgets(app.window.top_bar, Gtk.Table)[1]
        self.seek_button = find_widgets(self.buttons_table, SeekButton)[0]
        self.buttons_table.remove(self.seek_button)
        self.buttons_table.attach(self.bar.elapsed_button, 1, 3, 0, 1)
        self.bar.show()
        app.window.set_seekbar_widget(self.bar)

    def disabled(self):
        self.buttons_table.remove(self.bar.elapsed_button)
        self.buttons_table.attach(self.seek_button, 1, 3, 0, 1)
        app.window.set_seekbar_widget(None)
        self.bar.destroy()
        del self.bar
