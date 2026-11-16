from supabase import create_async_client, create_client
import os

def create_db_conn():
    return create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_PUBLISHABLE_KEY"]
    )