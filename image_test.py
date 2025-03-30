"""
Test script to verify image upload, storage, and retrieval.
This will help diagnose issues with receipt image display.
"""
import os
import logging
import base64
from datetime import datetime
from PIL import Image
from io import BytesIO

from flask import Flask, url_for, render_template, request, jsonify
from app import app, db
from models import Receipt, User
from image_processor import save_uploaded_image, UPLOAD_FOLDER

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def test_image_upload():
    """
    Test image upload and storage to verify where and how images are stored.
    Then create a receipt record referencing this image.
    """
    try:
        # 1. Create a test image if it doesn't exist
        test_image_path = "test_image.jpg"
        if not os.path.exists(test_image_path):
            # Create a simple test image
            img = Image.new('RGB', (100, 100), color = (73, 109, 137))
            img.save(test_image_path)
            logger.info(f"Created test image at {test_image_path}")
        
        # 2. Read the image
        with open(test_image_path, "rb") as f:
            image_data = f.read()
            
        # 3. Use the save_uploaded_image function to store it
        result = save_uploaded_image(image_data, "test_image.jpg")
        
        # 4. Log details about where and how the image was stored
        logger.info(f"Image saved with result: {result}")
        
        if result.get('image_path'):
            # Check the actual file on disk
            full_path = os.path.join(os.getcwd(), result['image_path'])
            if os.path.exists(full_path):
                logger.info(f"File exists at expected path: {full_path}")
            else:
                alt_path = os.path.join(os.getcwd(), 'static', result['image_path'].replace('static/', ''))
                if os.path.exists(alt_path):
                    logger.info(f"File exists at alternative path: {alt_path}")
                else:
                    logger.error(f"File not found at either expected path ({full_path}) or alternative path ({alt_path})")
        
        # 5. Create a receipt record in the database
        with app.app_context():
            # Find a user to associate with the receipt
            user = User.query.first()
            
            if user:
                # Create a test receipt using the uploaded image
                receipt = Receipt(
                    user_id=user.id,
                    vendor_name="Test Vendor",
                    total_amount=100.0,
                    date=datetime.now().date(),
                    image_path=result.get('image_path', ''),
                    s3_key=result.get('s3_key', '')
                )
                
                db.session.add(receipt)
                db.session.commit()
                
                logger.info(f"Created test receipt with ID: {receipt.id}")
                logger.info(f"Receipt record: image_path={receipt.image_path}, s3_key={receipt.s3_key}")
                
                # Test the to_dict method to see what image_url is being generated
                receipt_dict = receipt.to_dict()
                logger.info(f"Receipt to_dict result: image_url={receipt_dict.get('image_url')}")
                
                # Construct what the image URL should be
                if receipt.image_path:
                    expected_url = f"/static/{receipt.image_path.replace('static/', '').lstrip('/')}"
                    logger.info(f"Expected image URL: {expected_url}")
                
                return {
                    "receipt_id": receipt.id,
                    "image_path": receipt.image_path,
                    "s3_key": receipt.s3_key,
                    "image_url_from_dict": receipt_dict.get('image_url'),
                    "stored_at": full_path if os.path.exists(full_path) else alt_path
                }
            else:
                logger.error("No user found in the database to associate with test receipt")
                return None
    
    except Exception as e:
        logger.error(f"Error in test_image_upload: {str(e)}", exc_info=True)
        return None

if __name__ == "__main__":
    with app.app_context():
        result = test_image_upload()
        print("\n--- TEST RESULTS ---")
        if result:
            print(f"Receipt ID: {result['receipt_id']}")
            print(f"Image path stored in DB: {result['image_path']}")
            print(f"S3 key stored in DB: {result['s3_key']}")
            print(f"Image URL from to_dict: {result['image_url_from_dict']}")
            print(f"File stored at: {result['stored_at']}")
            print("\nTo view this receipt, visit:")
            print(f"http://localhost:5000/receipts/{result['receipt_id']}")
        else:
            print("Test failed. See logs for details.")