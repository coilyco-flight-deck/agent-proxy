#!/usr/bin/env python3

"""
Agent Proxy - Reliability Proxy for Local Agent and LLM Fleet
"""

import asyncio
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

async def main():
    """Main entrypoint."""
    logger.info("Starting agent proxy...")
    
    # Create app and start it
    from app.main import run_app
    
    try:
        await run_app()
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())