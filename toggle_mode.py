import os

def toggle():
    try:
        # Check if the dev folder is currently active
        os.stat('jukeplayer')
        os.rename('jukeplayer', '_jukeplayer')
        print("✅ Switched to PROD mode (Running from frozen firmware).")
        print("   Renamed 'jukeplayer' -> '_jukeplayer'\n")
    except OSError:
        # If 'jukeplayer' isn't found, check if it's currently in prod mode
        try:
            os.stat('_jukeplayer')
            os.rename('_jukeplayer', 'jukeplayer')
            print("✅ Switched to DEV mode (Running from filesystem .py scripts).")
            print("   Renamed '_jukeplayer' -> 'jukeplayer'\n")
        except OSError:
            print("❌ Error: Could not find either 'jukeplayer' or '_jukeplayer' directories!")
            return
            
    print("Please hard-reset the board or press Ctrl+D to apply changes.")

# Auto-run when the module is imported
toggle()
