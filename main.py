# from asyncio import base_events
# import asyncio
import gc
from jukeplayer.core.logger import log
import sys

def start_app():
    gc.collect()
    try:
        log.info(f"Starting Jukebox app...")
        from jukeplayer.app import main
        import asyncio
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("App stopped by KeyboardInterrupt")
    except Exception as e:
        log.error("Application crashed:")
        sys.print_exception(e)
        
start_app()
