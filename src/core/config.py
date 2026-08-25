import os
from dotenv import load_dotenv

load_dotenv()

GROQ_KEY = os.getenv("GROQ_KEY")
NVIDIA_KEY = os.getenv("NVIDIA_KEY")
OPEN_ROUTER_KEY = os.getenv("OPEN_ROUTER_KEY")
AIHUBMIX_KEY = os.getenv("AIHUBMIX_KEY")

if not GROQ_KEY:
    raise ValueError("GROQ_KEY is not set in the .env file")
