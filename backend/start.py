#!/usr/bin/env python3
"""
Production startup script for Detective Game backend
"""
import uvicorn
import os
from dotenv import load_dotenv

if __name__ == "__main__":
    load_dotenv()
    
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    
    uvicorn.run(
        "main:socket_app",
        host=host,
        port=port,
        reload=False,
        access_log=True
    )
