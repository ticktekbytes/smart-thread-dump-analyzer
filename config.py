from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Database settings
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT")
POSTGRES_DB = os.getenv("POSTGRES_DB")

# Construct database URL
DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# Vector store settings
VECTOR_STORE_PATH = "thread_dump_db"

# OpenAI settings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")