import time
import hashlib

def generate_rate_limit_key(client_id: str) -> str:
    current_minute = int(time.time() // 60)
    return f"ratelimit:{client_id}:{current_minute}"

def generate_usage_key(client_id: str) -> str:
    today = time.strftime("%Y-%m-%d")
    return f"usage:{client_id}:{today}"

def get_client_namespace(api_key: str) -> str:
    """
    Genera un namespace único para el cliente
    Usado para aislar datos entre clientes
    """
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]
