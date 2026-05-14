"""Built-in tool/function implementations called by the model."""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def execute_function(name: str, arguments: str) -> dict:
    """Execute a built-in function and return result."""
    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        args = {}

    if name == "get_time":
        from datetime import datetime
        return {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    elif name == "get_weather":
        location = args.get("location", "unknown")
        return {"location": location, "temperature": "72°F", "condition": "Sunny"}
    elif name == "calculate":
        expression = args.get("expression", "")
        try:
            result = eval(expression, {"__builtins__": {}})
            return {"expression": expression, "result": str(result)}
        except Exception:
            return {"expression": expression, "error": "Could not evaluate"}
    else:
        return {"error": f"Unknown function: {name}"}

_audio_chunk_count = 0

