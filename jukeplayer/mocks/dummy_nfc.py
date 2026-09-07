# jukeplayer/lib/dummy_nfc.py
class DummyNFCReader:
    def __init__(self):
        pass
    def select_chip(self): pass
    def deselect_chip(self): pass
    def read_album_id(self, block=4):
        return None
    async def write_data(self, album_id, block=4, timeout_ms=30000):
        # Simulated success payload, matching the async NFCReader contract
        return {"status": "success", "uid": "0xDUMMY_NFC"}
