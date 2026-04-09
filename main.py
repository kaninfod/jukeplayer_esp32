import asyncio
import gc
from jukeplayer.lib.logger import log

def start_app():
    # Clean memory before loading the big app class
    gc.collect()
    try:
        from jukeplayer.app import main
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("App stopped by KeyboardInterrupt")
    except Exception as e:
        log.error(f"Application crashed: {e}")

if __name__ == "__main__":
    start_app()
