"""
Migration script to add the s3_key column to the receipt table.
This allows storing receipt images in AWS S3 instead of just locally.
"""

import os
import sys
from datetime import datetime
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Column, String, text

# Create a minimal Flask application for the migration
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

def add_s3_key_column():
    """
    Add the s3_key column to the receipt table.
    """
    try:
        # Use raw SQL with text() to add the column
        print("Adding s3_key column to receipt table...")
        with app.app_context():
            db.session.execute(text('ALTER TABLE receipt ADD COLUMN IF NOT EXISTS s3_key VARCHAR(255)'))
            db.session.commit()
            print("Column added successfully!")
            return True
    except Exception as e:
        print(f"Error adding s3_key column: {str(e)}")
        try:
            with app.app_context():
                db.session.rollback()
        except:
            pass
        return False

if __name__ == "__main__":
    success = add_s3_key_column()
    sys.exit(0 if success else 1)