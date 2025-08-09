def tool_def(name, description):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "input": {"type": "string"}
                },
                "required": ["input"]
            }
        }
    }

def run_tool(name, input_text):
    if name == "lie_check":
        return "That doesn't seem consistent with earlier testimony."
    elif name == "act_confused":
        return "I really can't recall... was it raining?"
    elif name == "gossip_about":
        return f"{input_text} has always been a bit... shady."
    else:
        return "Tool not implemented."
