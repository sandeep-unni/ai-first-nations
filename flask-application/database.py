"""
database.py — Postgres connection setup for the Flask app.
Reads DATABASE_URL from environment (.env, loaded via python-dotenv).
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://aifn:aifn_dev_password@localhost:5433/aifn_mangrove"
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
