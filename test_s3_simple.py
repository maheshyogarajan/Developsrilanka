"""
Minimal test script for S3 file properties and retrieval.
"""

import os
import io
import sys
import logging
from PIL import Image
import hashlib
import requests

# Disable all but critical logging
logging.basicConfig(level=logging.ERROR)

# Add current directory to path
sys.path.append('.')

def main():
    try:
        # Import required modules
        from s3_storage import upload_image_to_s3, get_image_from_s3, generate_presigned_url
        
        # Create a simple test image (100x100 blue square)
        test_width, test_height = 100, 100
        test_color = (0, 0, 255)  # Blue
        test_img = Image.new('RGB', (test_width, test_height), color=test_color)
        
        # Get original image hash
        img_buffer = io.BytesIO()
        test_img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        original_hash = hashlib.md5(img_buffer.read()).hexdigest()
        print(f"Original image hash: {original_hash}")
        print(f"Original image size: {test_width}x{test_height}")
        print(f"Original image format: PNG")
        
        # Upload to S3
        print("\n1. Uploading test image to S3...")
        s3_key = upload_image_to_s3(test_img)
        print(f"Image uploaded with S3 key: {s3_key}")
        
        # Retrieve directly from S3
        print("\n2. Retrieving image directly from S3...")
        retrieved_img = get_image_from_s3(s3_key)
        
        # Check properties
        print(f"Retrieved image size: {retrieved_img.width}x{retrieved_img.height}")
        print(f"Retrieved image format: {retrieved_img.format if hasattr(retrieved_img, 'format') else 'N/A'}")
        
        # Get retrieved image hash
        retrieved_buffer = io.BytesIO()
        retrieved_img.save(retrieved_buffer, format='PNG')
        retrieved_buffer.seek(0)
        retrieved_hash = hashlib.md5(retrieved_buffer.read()).hexdigest()
        print(f"Retrieved image hash: {retrieved_hash}")
        
        if original_hash == retrieved_hash:
            print("✅ HASH MATCH: Image retrieved with perfect fidelity")
        else:
            # Check color of center pixel
            center_x, center_y = test_width // 2, test_height // 2
            original_center = test_img.getpixel((center_x, center_y))
            retrieved_center = retrieved_img.getpixel((center_x, center_y))
            
            print(f"Original center pixel color: {original_center}")
            print(f"Retrieved center pixel color: {retrieved_center}")
            
            if original_center == retrieved_center:
                print("✓ Center pixel colors match")
            else:
                print("✗ Center pixel colors don't match")
        
        # Get and test presigned URL
        print("\n3. Testing presigned URL generation...")
        presigned_url = generate_presigned_url(s3_key)
        if presigned_url:
            print(f"Generated URL: {presigned_url[:50]}...")
            
            # Download via URL
            print("\n4. Retrieving image via presigned URL...")
            response = requests.get(presigned_url)
            
            if response.status_code == 200:
                print(f"✓ Successfully retrieved image via URL")
                
                # Check content type and size
                content_type = response.headers.get('Content-Type', 'unknown')
                content_length = response.headers.get('Content-Length', '0')
                
                print(f"Content-Type: {content_type}")
                print(f"Content-Length: {content_length} bytes")
                
                # Load image from response content
                url_img = Image.open(io.BytesIO(response.content))
                print(f"URL-retrieved image size: {url_img.width}x{url_img.height}")
                
                # Get URL-retrieved image hash
                url_buffer = io.BytesIO()
                url_img.save(url_buffer, format='PNG')
                url_buffer.seek(0)
                url_hash = hashlib.md5(url_buffer.read()).hexdigest()
                print(f"URL-retrieved image hash: {url_hash}")
                
                if url_hash == original_hash:
                    print("✅ PERFECT MATCH: URL retrieval maintains perfect fidelity")
                else:
                    # Check center pixel
                    url_center = url_img.getpixel((center_x, center_y))
                    print(f"URL-retrieved center pixel color: {url_center}")
                    
                    if url_center == original_center:
                        print("✓ URL-retrieved center pixel color matches original")
                    else:
                        print("✗ URL-retrieved center pixel color differs from original")
            else:
                print(f"✗ Failed to retrieve image via URL, status code: {response.status_code}")
        else:
            print("✗ Failed to generate presigned URL")
            
        print("\nS3 retrieval test completed.")
        return True
    
    except Exception as e:
        print(f"ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=== S3 IMAGE PROPERTIES AND RETRIEVAL TEST ===")
    main()