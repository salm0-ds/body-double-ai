from supabase import acreate_client, create_client
import os

async def create_db_conn():
    return await acreate_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_PUBLISHABLE_KEY"]
    )