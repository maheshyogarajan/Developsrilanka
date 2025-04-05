"""
Comprehensive test script for S3 retrieval and security analysis.
This script tests:
1. Retrieval of stored test samples from S3
2. Verification of file properties and integrity
3. Analysis of potential vulnerabilities
4. Comparison of S3 and local storage mechanisms
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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add current directory to path
sys.path.append('.')

def test_image_retrieval_from_s3():
    """Test retrieving a recently uploaded test image from S3."""
    try:
        # Import required modules
        from main import app
        from models import db, Receipt
        from s3_storage import S3Storage, upload_image_to_s3, get_image_from_s3
        from PIL import Image
        
        # Create a test image with known properties
        test_width, test_height = 400, 300
        test_color = (255, 0, 0)  # Red
        test_img = Image.new('RGB', (test_width, test_height), color=test_color)
        
        # Add a pattern to the image for verification
        for i in range(0, test_width, 20):
            for j in range(0, test_height, 20):
                if (i + j) % 40 == 0:
                    for x in range(i, min(i+10, test_width)):
                        for y in range(j, min(j+10, test_height)):
                            test_img.putpixel((x, y), (0, 0, 255))  # Blue squares
        
        # Calculate original image hash for later comparison
        img_byte_arr = io.BytesIO()
        test_img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        original_hash = hashlib.md5(img_byte_arr.read()).hexdigest()
        img_byte_arr.seek(0)
        
        # Upload the image to S3
        print("Uploading test image to S3...")
        s3_key = upload_image_to_s3(test_img, organization_id=1)
        print(f"Image uploaded to S3 with key: {s3_key}")
        
        # Test direct S3 retrieval
        print("\nTesting direct S3 retrieval...")
        try:
            retrieved_img = get_image_from_s3(s3_key)
            
            # Verify image properties
            if retrieved_img.width == test_width and retrieved_img.height == test_height:
                print("✓ Image dimensions match")
            else:
                print(f"✗ Image dimensions mismatch: Expected {test_width}x{test_height}, got {retrieved_img.width}x{retrieved_img.height}")
            
            # Check a few pixels to verify content
            match_count = 0
            total_check = 5
            for _ in range(total_check):
                x, y = random.randint(0, test_width-1), random.randint(0, test_height-1)
                if test_img.getpixel((x, y)) == retrieved_img.getpixel((x, y)):
                    match_count += 1
                    
            print(f"✓ Pixel content verification: {match_count}/{total_check} pixels match")
            
            # Get hash of retrieved image
            retrieved_arr = io.BytesIO()
            retrieved_img.save(retrieved_arr, format='PNG')
            retrieved_arr.seek(0)
            retrieved_hash = hashlib.md5(retrieved_arr.read()).hexdigest()
            
            if original_hash == retrieved_hash:
                print(f"✓ Image hashes match: {original_hash}")
            else:
                print(f"✗ Image hashes don't match: Original {original_hash}, Retrieved {retrieved_hash}")
                
            direct_retrieval_success = True
        except Exception as e:
            print(f"Error retrieving image directly from S3: {str(e)}")
            direct_retrieval_success = False
        
        # Test database record and presigned URL
        print("\nTesting database record and presigned URL retrieval...")
        with app.app_context():
            # Create a receipt record with the S3 key
            receipt = Receipt(
                user_id=1,
                organization_id=1,
                vendor_name="Retrieval Test",
                total_amount=100.0,
                date=datetime.now().date(),
                s3_key=s3_key
            )
            
            db.session.add(receipt)
            db.session.commit()
            print(f"Created receipt with ID {receipt.id} and S3 key: {s3_key}")
            
            # Get presigned URL from receipt
            presigned_url = receipt.s3_url
            if presigned_url:
                print(f"✓ Generated presigned URL: {presigned_url[:50]}...")
                
                # Test retrieving image via presigned URL
                try:
                    response = requests.get(presigned_url, stream=True)
                    if response.status_code == 200:
                        url_img = Image.open(io.BytesIO(response.content))
                        
                        # Verify image properties
                        if url_img.width == test_width and url_img.height == test_height:
                            print("✓ URL-retrieved image dimensions match")
                        else:
                            print(f"✗ URL-retrieved image dimensions mismatch: Expected {test_width}x{test_height}, got {url_img.width}x{url_img.height}")
                        
                        # Get hash of URL-retrieved image
                        url_arr = io.BytesIO()
                        url_img.save(url_arr, format='PNG')
                        url_arr.seek(0)
                        url_hash = hashlib.md5(url_arr.read()).hexdigest()
                        
                        if original_hash == url_hash:
                            print(f"✓ URL-retrieved image hash matches original")
                        else:
                            print(f"✗ URL-retrieved image hash doesn't match: Original {original_hash}, Retrieved {url_hash}")
                        
                        presigned_url_success = True
                    else:
                        print(f"✗ Failed to retrieve image via presigned URL, status: {response.status_code}")
                        presigned_url_success = False
                except Exception as e:
                    print(f"Error retrieving image via presigned URL: {str(e)}")
                    presigned_url_success = False
            else:
                print("✗ Failed to generate presigned URL")
                presigned_url_success = False
            
            # Clean up test data
            print("\nCleaning up test data...")
            db.session.delete(receipt)
            db.session.commit()
            print(f"✓ Deleted test receipt with ID {receipt.id}")
        
        return {
            "direct_retrieval": direct_retrieval_success,
            "presigned_url": presigned_url_success,
            "original_hash": original_hash
        }
    
    except Exception as e:
        print(f"Error in image retrieval test: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "direct_retrieval": False,
            "presigned_url": False,
            "error": str(e)
        }

def test_url_expiration():
    """Test the expiration of presigned URLs."""
    try:
        # Import required modules
        from main import app
        from models import db, Receipt
        from s3_storage import generate_presigned_url
        from PIL import Image
        
        # Create and upload a test image
        test_img = Image.new('RGB', (100, 100), color='blue')
        from s3_storage import upload_image_to_s3
        s3_key = upload_image_to_s3(test_img)
        
        print("\nTesting URL expiration...")
        
        # Generate URLs with different expiration times
        url_5s = generate_presigned_url(s3_key, expiration=5)  # 5 seconds
        url_1h = generate_presigned_url(s3_key, expiration=3600)  # 1 hour
        
        print(f"✓ Generated URL with 5-second expiration: {url_5s[:50]}...")
        print(f"✓ Generated URL with 1-hour expiration: {url_1h[:50]}...")
        
        # Test immediate access
        print("Testing immediate access to both URLs...")
        response_5s = requests.head(url_5s)
        response_1h = requests.head(url_1h)
        
        if response_5s.status_code == 200:
            print("✓ 5-second URL is valid immediately")
        else:
            print(f"✗ 5-second URL failed immediate access, status: {response_5s.status_code}")
        
        if response_1h.status_code == 200:
            print("✓ 1-hour URL is valid immediately")
        else:
            print(f"✗ 1-hour URL failed immediate access, status: {response_1h.status_code}")
        
        # Wait for short URL to expire
        print("\nWaiting for 5-second URL to expire...")
        import time
        time.sleep(6)
        
        # Test access after expiration
        response_5s_after = requests.head(url_5s)
        response_1h_after = requests.head(url_1h)
        
        if response_5s_after.status_code != 200:
            print("✓ 5-second URL correctly expired")
        else:
            print("✗ 5-second URL did not expire as expected")
        
        if response_1h_after.status_code == 200:
            print("✓ 1-hour URL still valid after 5 seconds")
        else:
            print(f"✗ 1-hour URL unexpectedly invalid, status: {response_1h_after.status_code}")
        
        return {
            "url_expiration_works": response_5s_after.status_code != 200 and response_1h_after.status_code == 200
        }
    
    except Exception as e:
        print(f"Error in URL expiration test: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "url_expiration_works": False,
            "error": str(e)
        }

def test_s3_security():
    """Test security aspects of S3 storage and retrieval."""
    try:
        # Import required modules
        from s3_storage import S3Storage, generate_presigned_url
        
        print("\nTesting S3 security aspects...")
        
        # Test 1: Path traversal attempt
        print("Test 1: Path traversal vulnerability check")
        traversal_key = "../../../secret/file.jpg"
        url = generate_presigned_url(traversal_key)
        
        if url is None:
            print("✓ Path traversal attempt correctly rejected")
        else:
            print("✗ Path traversal vulnerability: URL generated for traversal path")
            
        # Test 2: Cross-organization access
        print("\nTest 2: Cross-organization access check")
        # Create test images for two different organizations
        test_img = Image.new('RGB', (100, 100), color='green')
        
        from s3_storage import upload_image_to_s3
        org1_key = upload_image_to_s3(test_img, organization_id=1)
        org2_key = upload_image_to_s3(test_img, organization_id=2)
        
        print(f"Created test object for Organization 1: {org1_key}")
        print(f"Created test object for Organization 2: {org2_key}")
        
        # Create a receipt in organization 1 with org2's key
        from main import app
        from models import db, Receipt
        
        with app.app_context():
            try:
                # Try to create a receipt for org1 with org2's S3 key
                receipt = Receipt(
                    user_id=1,
                    organization_id=1,
                    vendor_name="Cross-Org Test",
                    total_amount=100.0,
                    date=datetime.now().date(),
                    s3_key=org2_key  # Using org2's key with org1's receipt
                )
                
                db.session.add(receipt)
                db.session.commit()
                
                # Can we get a URL for cross-org access?
                cross_org_url = receipt.s3_url
                
                if cross_org_url:
                    print("ℹ️ S3 URLs are generated based on key only, not organization")
                    print("  ↳ Application must implement organization-based S3 key checks")
                    
                    # Test accessing the URL
                    response = requests.head(cross_org_url)
                    if response.status_code == 200:
                        print("✗ Cross-organization access possible - images not isolated by access controls")
                        print("  ↳ Relying on key path structure for isolation")
                    else:
                        print("✓ Cross-organization URL access denied")
                
                # Clean up
                db.session.delete(receipt)
                db.session.commit()
                
            except Exception as e:
                print(f"Error in cross-organization test: {str(e)}")
        
        # Test 3: URL tampering
        print("\nTest 3: URL tampering check")
        valid_key = upload_image_to_s3(test_img)
        valid_url = generate_presigned_url(valid_key)
        
        if valid_url:
            # Try to tamper with the URL
            tampered_url = valid_url.replace(valid_key, "tampered/key.jpg")
            
            response = requests.head(tampered_url)
            if response.status_code != 200:
                print("✓ Tampered URL correctly rejected")
            else:
                print("✗ Tampered URL accepted - potential security issue")
        
        # Return security test results
        return {
            "path_traversal_prevented": url is None,
            "url_tampering_prevented": response.status_code != 200
        }
    
    except Exception as e:
        print(f"Error in S3 security test: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "error": str(e)
        }

def test_s3_fallback():
    """Test fallback to local storage when S3 is unavailable."""
    try:
        # Import required modules
        import image_processor
        from PIL import Image
        
        print("\nTesting S3 fallback mechanism...")
        
        # Save original S3 setting
        original_s3_setting = image_processor.USE_S3_STORAGE
        
        # Test with S3 disabled
        print("Testing with S3 disabled...")
        try:
            # Disable S3
            image_processor.USE_S3_STORAGE = False
            
            # Create a test image
            test_img = Image.new('RGB', (100, 100), color='yellow')
            
            # Upload using image processor
            result = image_processor.save_uploaded_image(test_img, "fallback_test.jpg")
            
            # Check the result
            if result:
                if result.get('image_path') and not result.get('s3_key'):
                    print("✓ Successfully saved to local storage with S3 disabled")
                    print(f"  ↳ Local path: {result.get('image_path')}")
                    local_fallback_works = True
                else:
                    print("✗ Unexpected result with S3 disabled:")
                    print(f"  ↳ image_path: {result.get('image_path')}")
                    print(f"  ↳ s3_key: {result.get('s3_key')}")
                    local_fallback_works = False
            else:
                print("✗ Failed to save image with S3 disabled")
                local_fallback_works = False
        
        finally:
            # Restore original S3 setting
            image_processor.USE_S3_STORAGE = original_s3_setting
        
        # Test S3 error handling fallback
        print("\nTesting S3 error handling fallback...")
        
        # We'll simulate an S3 error by temporarily setting an invalid bucket name
        from s3_storage import S3Storage
        
        # Create a test S3 handler with a real but invalid bucket
        old_s3_handler = image_processor.s3_handler
        
        try:
            invalid_bucket_handler = S3Storage()
            # Patch the handler with an invalid bucket name
            invalid_bucket_handler.bucket_name = "definitely-not-a-valid-bucket-name-for-testing"
            
            # Replace the handler temporarily
            image_processor.s3_handler = invalid_bucket_handler
            
            # Try to upload
            result = image_processor.save_uploaded_image(test_img, "error_fallback_test.jpg")
            
            # Check the result
            if result:
                if result.get('image_path') and not result.get('s3_key'):
                    print("✓ Successfully fell back to local storage on S3 error")
                    print(f"  ↳ Local path: {result.get('image_path')}")
                    error_fallback_works = True
                else:
                    print("✗ Unexpected result on S3 error:")
                    print(f"  ↳ image_path: {result.get('image_path')}")
                    print(f"  ↳ s3_key: {result.get('s3_key')}")
                    error_fallback_works = False
            else:
                print("✗ Failed to save image on S3 error")
                error_fallback_works = False
        
        except Exception as e:
            print(f"Error testing S3 error fallback: {str(e)}")
            error_fallback_works = False
        
        finally:
            # Restore original S3 handler
            image_processor.s3_handler = old_s3_handler
        
        return {
            "local_fallback_works": local_fallback_works,
            "error_fallback_works": error_fallback_works
        }
    
    except Exception as e:
        print(f"Error in S3 fallback test: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "error": str(e)
        }

def run_all_tests():
    """Run all S3 retrieval and security tests."""
    results = {}
    
    # Test 1: Basic image retrieval
    print("=== TEST 1: IMAGE RETRIEVAL ===")
    results['retrieval'] = test_image_retrieval_from_s3()
    
    # Test 2: URL expiration
    print("\n=== TEST 2: URL EXPIRATION ===")
    results['url_expiration'] = test_url_expiration()
    
    # Test 3: S3 security
    print("\n=== TEST 3: S3 SECURITY ===")
    results['security'] = test_s3_security()
    
    # Test 4: S3 fallback
    print("\n=== TEST 4: S3 FALLBACK ===")
    results['fallback'] = test_s3_fallback()
    
    return results

if __name__ == "__main__":
    print("======================================")
    print("S3 RETRIEVAL AND SECURITY ANALYSIS")
    print("======================================")
    
    results = run_all_tests()
    
    print("\n======================================")
    print("TEST RESULTS SUMMARY")
    print("======================================")
    
    # Image retrieval results
    print("\n1. Image Retrieval Test:")
    if results['retrieval'].get('direct_retrieval'):
        print("  ✓ Direct S3 retrieval works")
    else:
        print("  ✗ Direct S3 retrieval failed")
    
    if results['retrieval'].get('presigned_url'):
        print("  ✓ Presigned URL retrieval works")
    else:
        print("  ✗ Presigned URL retrieval failed")
    
    # URL expiration results
    print("\n2. URL Expiration Test:")
    if results['url_expiration'].get('url_expiration_works'):
        print("  ✓ URL expiration works correctly")
    else:
        print("  ✗ URL expiration failed")
    
    # Security results
    print("\n3. Security Test:")
    if results['security'].get('path_traversal_prevented'):
        print("  ✓ Path traversal prevention works")
    else:
        print("  ✗ Path traversal prevention failed")
    
    if results['security'].get('url_tampering_prevented'):
        print("  ✓ URL tampering prevention works")
    else:
        print("  ✗ URL tampering prevention failed")
    
    # Fallback results
    print("\n4. S3 Fallback Test:")
    if results['fallback'].get('local_fallback_works'):
        print("  ✓ Local storage fallback works when S3 disabled")
    else:
        print("  ✗ Local storage fallback failed")
    
    if results['fallback'].get('error_fallback_works'):
        print("  ✓ Error handling fallback works")
    else:
        print("  ✗ Error handling fallback failed")
    
    print("\n======================================")
    print("SECURITY ASSESSMENT")
    print("======================================")
    security_issues = []
    if not results['security'].get('path_traversal_prevented', True):
        security_issues.append("Path traversal vulnerability")
    if not results['security'].get('url_tampering_prevented', True):
        security_issues.append("URL tampering vulnerability")
        
    if security_issues:
        print("\n⚠️ Security issues found:")
        for issue in security_issues:
            print(f"  - {issue}")
    else:
        print("\n✅ No major security issues detected in tested scenarios")
        
    print("\nNote: This is not a comprehensive security audit. Production systems should undergo professional security assessment.")