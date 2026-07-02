"""
OpenAI-compatible API surface for agent proxy.
"""

from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

# Import directly to prevent circular imports
from app.config import get_settings

# Create router for the API endpoints
router = APIRouter(prefix="/v1")

# Health check endpoint
@router.get("/healthz")
async def health_check():
    return {"status": "ok"}

# Metrics endpoint  
@router.get("/metrics")
async def metrics():
    # This is a placeholder - in reality this would return actual prometheus metrics
    return {"message": "Prometheus metrics endpoint"}

# List models endpoint
@router.get("/models")
async def list_models():
    # Return list of available models 
    default_models = [
        {"id": "tune", "object": "model", "created": 1234567890},
        {"id": "fast-think", "object": "model", "created": 1234567890},
        {"id": "fast", "object": "model", "created": 1234567890},  
        {"id": "ctx-think", "object": "model", "created": 1234567890},
        {"id": "ctx", "object": "model", "created": 1234567890}
    ]
    
    return {"data": default_models, "object": "list"}

# Chat completion endpoint - simplified for now
@router.post("/chat/completions")
async def chat_completion(request: Dict[str, Any]):
    # Placeholder logic - in real implementation this would process the request using resilience
    return {
        "id": "test-id", 
        "object": "chat.completion",
        "created": 1234567890,
        "model": request.get("model", "fast"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Test response"},
                "finish_reason": "stop"
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    }

# Completions endpoint
@router.post("/completions")
async def completions(request: Dict[str, Any]):
    # Simple response for now  
    return {
        "id": "test-id",
        "object": "text_completion", 
        "created": 1234567890,
        "model": request.get("model", "fast"),
        "choices": [
            {"text": "Test completion response", "index": 0, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    }