"""
Focused test script for S3 image retrieval analysis.
This script:
1. Uploads a test image with known properties to S3
2. Retrieves the image directly from S3
3. Creates a database record with the S3 key
4. Generates and tests a presigned URL
5. Analyzes security and file integrity
"""

import os
import sys
import io
import urllib.request
import hashlib
import logging
import base64
import requests
from datetime import datetime
from PIL import Image
import random
import string
import uuid

# Disable logging to focus on test results
logging.basicConfig(level=logging.ERROR)
for log_name in ['root', 'werkzeug', 'sqlalchemy', 'boto3', 'botocore', 's3transfer', 'urllib3', 's3_storage', 'main', 'template_filters', 'blueprints']:
    logging.getLogger(log_name).setLevel(logging.ERROR)

# Add current directory to path
sys.path.append('.')

def test_s3_image_retrieval():
    try:
        # Import required modules
        from main import app
        from models import db, Receipt
        from s3_storage import generate_presigned_url, upload_image_to_s3, get_image_from_s3
        
        # Create a test image with verifiable properties
        print("Creating test image with known properties...")
        img_width, img_height = 200, 150
        img_color = (255, 0, 0)  # Red
        test_img = Image.new('RGB', (img_width, img_height), color=img_color)
        
        # Add unique pattern for verification
        for i in range(0, img_width, 40):
            for j in range(0, img_height, 40):
                for x in range(i, min(i+20, img_width)):
                    for y in range(j, min(j+20, img_height)):
                        test_img.putpixel((x, y), (0, 0, 255))  # Blue squares
                        
        # Calculate original image hash
        img_byte_arr = io.BytesIO()
        test_img.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        original_hash = hashlib.md5(img_byte_arr.read()).hexdigest()
        print(f"Original image hash: {original_hash}")
        
        # Upload image to S3
        print("\nUploading image to S3...")
        img_byte_arr.seek(0)
        organization_id = 1
        s3_key = upload_image_to_s3(test_img, organization_id=organization_id)
        print(f"Image uploaded with S3 key: {s3_key}")
        
        # Test direct retrieval from S3
        print("\nRetrieving image directly from S3...")
        try:
            retrieved_img = get_image_from_s3(s3_key)
            
            # Verify dimensions
            print(f"Retrieved image dimensions: {retrieved_img.width}x{retrieved_img.height}")
            if retrieved_img.width == img_width and retrieved_img.height == img_height:
                print("✓ Image dimensions match expected values")
            else:
                print(f"✗ Image dimensions mismatch: Expected {img_width}x{img_height}")
            
            # Calculate retrieved image hash
            retrieved_arr = io.BytesIO()
            retrieved_img.save(retrieved_arr, format='JPEG')
            retrieved_arr.seek(0)
            retrieved_hash = hashlib.md5(retrieved_arr.read()).hexdigest()
            print(f"Retrieved image hash: {retrieved_hash}")
            
            if original_hash == retrieved_hash:
                print("✓ Image hashes match (perfect retrieval)")
            else:
                print("ℹ️ Image hashes differ (possible format/compression changes)")
                
                # Check pixel sample instead
                pixel_matches = 0
                samples = 10
                for _ in range(samples):
                    x, y = random.randint(0, img_width-1), random.randint(0, img_height-1)
                    original_pixel = test_img.getpixel((x, y))
                    retrieved_pixel = retrieved_img.getpixel((x, y))
                    
                    # Allow for minor color variations due to JPEG compression
                    color_diff = sum(abs(a-b) for a, b in zip(original_pixel, retrieved_pixel))
                    if color_diff < 30:  # Threshold for acceptable color difference
                        pixel_matches += 1
                
                match_percentage = (pixel_matches / samples) * 100
                print(f"Pixel sample match rate: {match_percentage}% ({pixel_matches}/{samples})")
                if match_percentage >= 70:
                    print("✓ Pixel samples match at acceptable rate")
                else:
                    print("✗ Pixel samples match rate below threshold")
                
            direct_retrieval_success = True
            
        except Exception as e:
            print(f"Error retrieving image from S3: {str(e)}")
            direct_retrieval_success = False
        
        # Test creating a database record and generating a presigned URL
        print("\nTesting database integration and presigned URL...")
        with app.app_context():
            try:
                # Create a receipt with the S3 key
                receipt = Receipt(
                    user_id=1,
                    organization_id=organization_id,
                    vendor_name="S3 Retrieval Test",
                    total_amount=100.0,
                    date=datetime.now().date(),
                    s3_key=s3_key
                )
                
                db.session.add(receipt)
                db.session.commit()
                print(f"Created receipt record with ID: {receipt.id}, S3 key: {s3_key}")
                
                # Test the s3_url property (presigned URL generation)
                presigned_url = receipt.s3_url
                if presigned_url:
                    print(f"✓ Generated presigned URL: {presigned_url[:50]}...")
                    
                    # Test accessing the URL
                    try:
                        response = requests.get(presigned_url, timeout=10)
                        
                        if response.status_code == 200:
                            print("✓ Successfully accessed image via presigned URL")
                            
                            # Check content type
                            content_type = response.headers.get('Content-Type')
                            print(f"Content type: {content_type}")
                            if 'image/' in content_type.lower():
                                print("✓ Content type confirms this is an image")
                            
                            # Check file size
                            content_length = int(response.headers.get('Content-Length', 0))
                            print(f"File size: {content_length} bytes")
                            
                            # Verify content is actually an image
                            try:
                                url_img = Image.open(io.BytesIO(response.content))
                                print(f"URL-retrieved image dimensions: {url_img.width}x{url_img.height}")
                                
                                if url_img.width == img_width and url_img.height == img_height:
                                    print("✓ URL-retrieved image dimensions match")
                                else:
                                    print(f"✗ URL-retrieved image dimensions mismatch")
                                
                                presigned_url_success = True
                            except Exception as img_e:
                                print(f"Error processing URL-retrieved image: {str(img_e)}")
                                presigned_url_success = False
                        else:
                            print(f"✗ Failed to access presigned URL, status code: {response.status_code}")
                            presigned_url_success = False
                    
                    except Exception as url_e:
                        print(f"Error accessing presigned URL: {str(url_e)}")
                        presigned_url_success = False
                else:
                    print("✗ Failed to generate presigned URL")
                    presigned_url_success = False
                
                # Clean up
                db.session.delete(receipt)
                db.session.commit()
                print(f"Cleaned up test receipt with ID: {receipt.id}")
                
            except Exception as db_e:
                print(f"Database error: {str(db_e)}")
                presigned_url_success = False
        
        # Security test - try to access with modified URL
        if presigned_url_success and presigned_url:
            print("\nTesting URL security...")
            
            # Test 1: Path traversal
            traversal_s3_key = "../../../some_other_object.jpg"
            traversal_url = generate_presigned_url(traversal_s3_key)
            
            if traversal_url is None:
                print("✓ Path traversal attempt blocked (no URL generated)")
            else:
                print("✗ Generated URL for path traversal attempt")
            
            # Test 2: URL manipulation
            if presigned_url:
                # Try to manipulate the URL
                modified_url = presigned_url.replace(s3_key, "manipulated/path.jpg")
                
                try:
                    response = requests.head(modified_url, timeout=5)
                    if response.status_code != 200:
                        print("✓ Manipulated URL rejected")
                    else:
                        print("✗ Manipulated URL accepted - potential security issue")
                except Exception:
                    print("✓ Manipulated URL request failed (expected)")
        
        return {
            "direct_retrieval": direct_retrieval_success,
            "presigned_url": presigned_url_success,
            "s3_key": s3_key
        }
        
    except Exception as e:
        print(f"Test error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}

if __name__ == "__main__":
    print("======================================")
    print("S3 IMAGE RETRIEVAL ANALYSIS")
    print("======================================")
    
    results = test_s3_image_retrieval()
    
    print("\n======================================")
    print("SUMMARY OF FINDINGS")
    print("======================================")
    
    if results.get("direct_retrieval"):
        print("✅ Direct S3 retrieval working properly")
    else:
        print("❌ Issues with direct S3 retrieval")
    
    if results.get("presigned_url"):
        print("✅ Presigned URL generation and access working properly")
    else:
        print("❌ Issues with presigned URL generation/access")
    
    print("\nTest S3 key:", results.get("s3_key", "Not available"))
    
    if "error" in results:
        print("\n❌ Test encountered errors:", results["error"])
    
    print("\nAnalysis complete!")