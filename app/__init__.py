"""
Agent Proxy - Reliability Proxy for Local Agent and LLM Fleet

This is the main module that creates the FastAPI app and handles running it.
"""

import asyncio
import logging
from fastapi import FastAPI
from typing import Optional

from app.resilience import dispatch, dispatch_stream
from app.config import get_settings
from app.models import LogicalModel, Backend
from app.queue import run_queue_worker
from app.obs import setup_observability


# Setup logging for the service
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure a FastAPI app for agent proxy."""
    
    # Setup observability (metrics, logs)
    setup_observability()
    
    app = FastAPI(
        title="Agent Proxy",
        description="Reliability proxy for the local agent and LLM fleet",
        version="0.1.0"
    )
    
    # Import routes after app is created to avoid circular imports
    from app import api
    
    # Include API routes
    app.include_router(api.router)
    
    return app


async def run_app():
    """Run the main proxy service."""
    logger.info("Starting Agent Proxy...")
    
    settings = get_settings()
    
    # Create and start the application
    app = create_app()
    
    # Start background tasks (queue workers, etc.)
    queue_task = asyncio.create_task(run_queue_worker())
    
    try:
        # Import hypercorn here to avoid import issues in some environments
        from hypercorn.asyncio.run import run
        logger.info(f"Starting proxy on {settings.proxy_host}:{settings.proxy_port}")
        
        await run(app, {
            "bind": f"{settings.proxy_host}:{settings.proxy_port}",
            "workers": 1,
            "loglevel": settings.log_level.lower()
        })
    except Exception as e:
        logger.error(f"Error running app: {e}")
        raise
    finally:
        # Cleanup tasks when done
        queue_task.cancel()
        try:
            await queue_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    # Run the app directly (not as module)
    # This version sets up logging and runs the service
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    logger.info("Starting agent proxy from main...")
    asyncio.run(run_app())