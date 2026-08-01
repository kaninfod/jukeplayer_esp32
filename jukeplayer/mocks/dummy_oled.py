# jukeplayer/gui/dummy_oled.py
class DummyOLEDScroller:
    def __init__(self):
        pass
    def start(self): pass
    def stop(self): pass
    def set_net_status(self, status): pass
    def set_player_status(self, status): pass
    def set_repeat_status(self, repeat): pass
    def set_volume(self, volume): pass
    def set_artist(self, artist): pass
    def set_text(self, title): pass
    def update(self, state={}): pass
