# This is the "Root" entry point that launches your frozen package
import asyncio
from jukeplayer.app import JukeBoxApp

async def main():
    app = JukeBoxApp()
    await app.run()



# Run the async application
asyncio.run(main())
