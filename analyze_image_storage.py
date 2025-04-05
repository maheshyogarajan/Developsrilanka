"""
Image Storage Analysis Script

This script performs a comprehensive analysis of the image storage system:
1. Vulnerability testing (path traversal, file extension validation, etc.)
2. Storage mechanism verification (local vs S3)
3. Retrieval verification (database retrieval, URL generation)
4. Error handling verification (non-existent files, invalid S3 keys, etc.)
"""

import os
import sys
import io
import logging
import base64
import random
import string
import traceback
from datetime import datetime
from PIL import Image
from pathlib import Path

# Configure logging - set to INFO to see important details
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Disable other loggers to reduce noise
logging.getLogger('boto3').setLevel(logging.ERROR)
logging.getLogger('botocore').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)
logging.getLogger('s3transfer').setLevel(logging.ERROR)
logging.getLogger('sqlalchemy').setLevel(logging.ERROR)
logging.getLogger('flask').setLevel(logging.ERROR)
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# Add current directory to path to allow imports
sys.path.append('.')

# Import required modules
from main import app
from models import db, Receipt, User
from s3_storage import S3Storage, generate_presigned_url
from image_processor import save_uploaded_image, UPLOAD_FOLDER

def generate_test_image(width=300, height=400, color='white'):
    """Generate a test image in memory."""
    return Image.new('RGB', (width, height), color=color)

def generate_random_string(length=10):
    """Generate a random string for testing."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def test_path_traversal_vulnerability():
    """Test for path traversal vulnerabilities in the filename handling."""
    logger.info("=== PATH TRAVERSAL VULNERABILITY TEST ===")
    try:
        # Create test image
        test_img = generate_test_image()
        
        # Malicious filenames to test
        traversal_filenames = [
            "../../../etc/passwd",
            "..%2f..%2f..%2fetc%2fpasswd",
            "test.jpg/../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "test.jpg;rm -rf /",
            "%00../../../etc/passwd"
        ]
        
        results = {}
        
        for filename in traversal_filenames:
            logger.info(f"Testing with malicious filename: {filename}")
            try:
                result = save_uploaded_image(test_img, filename)
                
                # Check if the result indicates successful storage
                if result:
                    # Check where the file was stored
                    if 'image_path' in result and result['image_path']:
                        path = result['image_path']
                        # Check if the path contains sensitive locations
                        if '..' in path or 'etc' in path or 'passwd' in path or 'windows' in path:
                            logger.error(f"VULNERABILITY FOUND: Path traversal successful: {path}")
                            results[filename] = {
                                'status': 'VULNERABLE',
                                'stored_at': path
                            }
                        else:
                            logger.info(f"Path traversal attempted but safe path used: {path}")
                            results[filename] = {
                                'status': 'SAFE',
                                'stored_at': path
                            }
                    else:
                        logger.info(f"No path returned for filename: {filename}")
                        results[filename] = {
                            'status': 'NO_PATH_RETURNED'
                        }
                else:
                    logger.info(f"Storage function returned no result for filename: {filename}")
                    results[filename] = {
                        'status': 'NO_RESULT'
                    }
            except Exception as e:
                logger.info(f"Error storing file with name {filename}: {str(e)}")
                results[filename] = {
                    'status': 'ERROR',
                    'error': str(e)
                }
        
        # Verify if any of the files were actually stored on the filesystem
        for filename, result in results.items():
            if 'stored_at' in result:
                file_path = os.path.join(os.getcwd(), 'static', result['stored_at'].replace('static/', ''))
                if os.path.exists(file_path):
                    # Check if the file is in an unexpected location
                    uploaded_folder = os.path.normpath(os.path.join(os.getcwd(), 'static', 'uploads'))
                    file_dir = os.path.normpath(os.path.dirname(file_path))
                    if not file_dir.startswith(uploaded_folder):
                        logger.error(f"VULNERABILITY FOUND: File stored outside uploads directory: {file_path}")
                        result['outside_uploads'] = True
                    else:
                        logger.info(f"File stored within uploads directory: {file_path}")
                        result['outside_uploads'] = False
                else:
                    logger.info(f"No file found at path: {file_path}")
                    result['file_exists'] = False
        
        return {
            'test_name': 'path_traversal_vulnerability',
            'results': results,
            'summary': 'Check results to see if any path traversal vulnerabilities were found'
        }
    
    except Exception as e:
        logger.error(f"Error in path traversal test: {str(e)}")
        traceback.print_exc()
        return {
            'test_name': 'path_traversal_vulnerability',
            'status': 'ERROR',
            'error': str(e)
        }

def test_file_extension_validation():
    """Test for file extension validation vulnerabilities."""
    logger.info("=== FILE EXTENSION VALIDATION TEST ===")
    try:
        # Create test image
        test_img = generate_test_image()
        
        # Test file extensions
        extensions = [
            '.jpg',
            '.jpeg',
            '.png',
            '.gif',
            '.php',  # Potential executable
            '.jsp',  # Potential executable
            '.exe',  # Definitely executable
            '.js',   # Potential client-side script
            '.html', # Potential client-side script
            '',      # No extension
            '.jpg.php', # Double extension
            '.php.jpg'  # Double extension reversed
        ]
        
        results = {}
        
        for ext in extensions:
            filename = f"test{ext}"
            logger.info(f"Testing with filename: {filename}")
            try:
                result = save_uploaded_image(test_img, filename)
                
                # Check if the result indicates successful storage
                if result:
                    results[filename] = {
                        'status': 'STORED',
                        'stored_at': result.get('image_path', 'Unknown'),
                        's3_key': result.get('s3_key', '')
                    }
                    
                    # Check if the file extension was sanitized/changed
                    original_ext = os.path.splitext(filename)[1].lower()
                    if result.get('image_path'):
                        final_ext = os.path.splitext(result['image_path'])[1].lower()
                        results[filename]['extension_sanitized'] = (original_ext != final_ext) and original_ext not in ('.jpg', '.jpeg', '.png', '.gif')
                    
                    logger.info(f"File {filename} stored successfully: {result}")
                else:
                    results[filename] = {
                        'status': 'NOT_STORED'
                    }
                    logger.info(f"File {filename} was not stored")
            except Exception as e:
                results[filename] = {
                    'status': 'ERROR',
                    'error': str(e)
                }
                logger.info(f"Error storing file {filename}: {str(e)}")
        
        # Analyze the results to identify potential vulnerabilities
        vulnerable_extensions = []
        for filename, result in results.items():
            ext = os.path.splitext(filename)[1].lower()
            if result['status'] == 'STORED' and ext in ('.php', '.jsp', '.exe', '.js', '.html'):
                if not result.get('extension_sanitized', False):
                    vulnerable_extensions.append(ext)
                    logger.error(f"VULNERABILITY FOUND: Potentially dangerous file extension '{ext}' was not sanitized")
        
        if vulnerable_extensions:
            return {
                'test_name': 'file_extension_validation',
                'results': results,
                'vulnerable_extensions': vulnerable_extensions,
                'summary': f"VULNERABLE - The following dangerous extensions were accepted: {', '.join(vulnerable_extensions)}"
            }
        else:
            return {
                'test_name': 'file_extension_validation',
                'results': results,
                'summary': "SECURE - File extension validation appears to be working correctly"
            }
    
    except Exception as e:
        logger.error(f"Error in file extension test: {str(e)}")
        traceback.print_exc()
        return {
            'test_name': 'file_extension_validation',
            'status': 'ERROR',
            'error': str(e)
        }

def test_s3_storage_mechanism():
    """Test S3 storage mechanism."""
    logger.info("=== S3 STORAGE MECHANISM TEST ===")
    try:
        # Check if S3 credentials are available
        s3_creds_available = all([
            os.environ.get('AWS_S3_BUCKET_NAME'),
            os.environ.get('AWS_ACCESS_KEY_ID'),
            os.environ.get('AWS_SECRET_ACCESS_KEY'),
            os.environ.get('AWS_REGION')
        ])
        
        if not s3_creds_available:
            logger.warning("Skipping S3 storage test as credentials are not available")
            return {
                'test_name': 's3_storage_mechanism',
                'status': 'SKIPPED',
                'reason': 'S3 credentials not available'
            }
        
        # Create test image
        test_img = generate_test_image()
        
        # Generate a unique filename
        unique_id = generate_random_string()
        filename = f"test_s3_{unique_id}.jpg"
        
        # Test various organization IDs
        org_ids = [None, 1, 999]  # None, existing, and likely non-existent org ID
        results = {}
        
        for org_id in org_ids:
            try:
                logger.info(f"Testing S3 upload with organization_id={org_id}")
                
                # Upload image
                result = save_uploaded_image(test_img, filename, org_id)
                
                if result:
                    s3_key = result.get('s3_key', '')
                    local_path = result.get('image_path', '')
                    
                    # Check if S3 key was generated
                    if s3_key:
                        # Generate URL and check if it's valid
                        presigned_url = generate_presigned_url(s3_key)
                        
                        # Store result
                        results[f"org_id_{org_id}"] = {
                            'status': 'SUCCESS',
                            's3_key': s3_key,
                            'local_path': local_path,
                            'presigned_url': presigned_url is not None,
                            'url_prefix': presigned_url[:100] if presigned_url else None
                        }
                        
                        # Check if organization ID is in the S3 key (for organization scoping)
                        if org_id is not None:
                            org_in_key = f"/{org_id}/" in s3_key
                            results[f"org_id_{org_id}"]['org_id_in_key'] = org_in_key
                            
                            if not org_in_key:
                                logger.warning(f"Organization ID {org_id} not found in S3 key: {s3_key}")
                        
                        logger.info(f"S3 upload successful for org_id={org_id}, key={s3_key}")
                    else:
                        results[f"org_id_{org_id}"] = {
                            'status': 'PARTIAL',
                            'local_path': local_path,
                            'reason': 'No S3 key generated'
                        }
                        logger.warning(f"No S3 key generated for org_id={org_id}")
                else:
                    results[f"org_id_{org_id}"] = {
                        'status': 'FAILED',
                        'reason': 'No result returned from save_uploaded_image'
                    }
                    logger.error(f"No result returned from save_uploaded_image for org_id={org_id}")
            except Exception as e:
                results[f"org_id_{org_id}"] = {
                    'status': 'ERROR',
                    'error': str(e)
                }
                logger.error(f"Error uploading to S3 for org_id={org_id}: {str(e)}")
        
        # Check fallback behavior when S3 fails
        try:
            # Create a mock S3Storage with intentionally wrong credentials
            s3_storage.USE_S3_STORAGE = False
            
            logger.info("Testing fallback to local storage when S3 is disabled")
            
            # Upload image with S3 disabled
            result = save_uploaded_image(test_img, f"fallback_{filename}")
            
            if result:
                local_path = result.get('image_path', '')
                s3_key = result.get('s3_key', '')
                
                if local_path and not s3_key:
                    results['fallback'] = {
                        'status': 'SUCCESS',
                        'local_path': local_path,
                        'summary': 'Successfully fell back to local storage when S3 is disabled'
                    }
                    logger.info(f"Successfully fell back to local storage: {local_path}")
                else:
                    results['fallback'] = {
                        'status': 'FAILED',
                        'local_path': local_path,
                        's3_key': s3_key,
                        'summary': 'Fallback behavior not working correctly'
                    }
                    logger.error(f"Fallback behavior not working correctly: {result}")
            else:
                results['fallback'] = {
                    'status': 'ERROR',
                    'reason': 'No result returned from save_uploaded_image'
                }
                logger.error("No result returned from save_uploaded_image during fallback test")
        except Exception as e:
            results['fallback'] = {
                'status': 'ERROR',
                'error': str(e)
            }
            logger.error(f"Error testing fallback behavior: {str(e)}")
        finally:
            # Restore S3 settings
            s3_storage.USE_S3_STORAGE = s3_creds_available
        
        return {
            'test_name': 's3_storage_mechanism',
            'results': results,
            'summary': 'Check results to see if S3 storage is working correctly'
        }
    
    except Exception as e:
        logger.error(f"Error in S3 storage test: {str(e)}")
        traceback.print_exc()
        return {
            'test_name': 's3_storage_mechanism',
            'status': 'ERROR',
            'error': str(e)
        }

def test_image_retrieval():
    """Test image retrieval from database and S3."""
    logger.info("=== IMAGE RETRIEVAL TEST ===")
    try:
        with app.app_context():
            # Find a receipt with an S3 key
            s3_receipt = db.session.query(Receipt).filter(Receipt.s3_key.isnot(None)).order_by(Receipt.id.desc()).first()
            
            # Find a receipt with a local image path
            local_receipt = db.session.query(Receipt).filter(
                Receipt.s3_key.is_(None), 
                Receipt.image_path.isnot(None)
            ).order_by(Receipt.id.desc()).first()
            
            # Find a receipt with neither
            no_image_receipt = db.session.query(Receipt).filter(
                Receipt.s3_key.is_(None), 
                Receipt.image_path.is_(None)
            ).order_by(Receipt.id.desc()).first()
            
            results = {}
            
            # Test S3 retrieval
            if s3_receipt:
                logger.info(f"Testing retrieval for S3 receipt ID: {s3_receipt.id}")
                
                # Get S3 URL
                s3_url = s3_receipt.s3_url
                
                # Convert receipt to dict
                receipt_dict = s3_receipt.to_dict()
                
                # Store results
                results['s3_receipt'] = {
                    'receipt_id': s3_receipt.id,
                    's3_key': s3_receipt.s3_key,
                    's3_url_generated': s3_url is not None,
                    's3_url_in_dict': receipt_dict.get('s3_url') is not None,
                    'summary': 'S3 URL retrieval successful' if s3_url else 'Failed to generate S3 URL'
                }
                
                if s3_url:
                    logger.info(f"Successfully generated S3 URL: {s3_url[:100]}...")
                else:
                    logger.warning(f"Failed to generate S3 URL for key: {s3_receipt.s3_key}")
            else:
                results['s3_receipt'] = {
                    'status': 'SKIPPED',
                    'reason': 'No receipt with S3 key found in database'
                }
                logger.warning("No receipt with S3 key found in database")
            
            # Test local image retrieval
            if local_receipt:
                logger.info(f"Testing retrieval for local receipt ID: {local_receipt.id}")
                
                # Convert receipt to dict
                receipt_dict = local_receipt.to_dict()
                
                # Check if image URL is generated
                image_url = receipt_dict.get('image_url')
                
                # Verify file exists on disk
                file_exists = False
                if local_receipt.image_path:
                    file_path = os.path.join(os.getcwd(), 'static', local_receipt.image_path.replace('static/', '').lstrip('/'))
                    file_exists = os.path.exists(file_path)
                
                # Store results
                results['local_receipt'] = {
                    'receipt_id': local_receipt.id,
                    'image_path': local_receipt.image_path,
                    'image_url_in_dict': image_url is not None,
                    'file_exists': file_exists,
                    'summary': 'Local image retrieval successful' if image_url and file_exists else 'Issues with local image retrieval'
                }
                
                if image_url and file_exists:
                    logger.info(f"Successfully retrieved local image: {image_url}, file exists on disk")
                else:
                    if not image_url:
                        logger.warning(f"No image URL generated for path: {local_receipt.image_path}")
                    if not file_exists:
                        logger.warning(f"File does not exist at path: {file_path}")
            else:
                results['local_receipt'] = {
                    'status': 'SKIPPED',
                    'reason': 'No receipt with local image path found in database'
                }
                logger.warning("No receipt with local image path found in database")
            
            # Test receipt with no image
            if no_image_receipt:
                logger.info(f"Testing retrieval for receipt with no image ID: {no_image_receipt.id}")
                
                # Convert receipt to dict
                receipt_dict = no_image_receipt.to_dict()
                
                # Check URLs
                s3_url = receipt_dict.get('s3_url')
                image_url = receipt_dict.get('image_url')
                
                # Store results
                results['no_image_receipt'] = {
                    'receipt_id': no_image_receipt.id,
                    's3_url_in_dict': s3_url is not None,
                    'image_url_in_dict': image_url is not None,
                    'summary': 'Receipt with no image handled correctly' if not s3_url and not image_url else 'Receipt with no image should not have URLs'
                }
                
                if not s3_url and not image_url:
                    logger.info("Receipt with no image correctly has no URLs")
                else:
                    logger.warning(f"Receipt with no image has unexpected URLs: s3_url={s3_url}, image_url={image_url}")
            else:
                results['no_image_receipt'] = {
                    'status': 'SKIPPED',
                    'reason': 'No receipt without images found in database'
                }
                logger.warning("No receipt without images found in database")
            
            return {
                'test_name': 'image_retrieval',
                'results': results,
                'summary': 'Check results to see if image retrieval is working correctly'
            }
    
    except Exception as e:
        logger.error(f"Error in image retrieval test: {str(e)}")
        traceback.print_exc()
        return {
            'test_name': 'image_retrieval',
            'status': 'ERROR',
            'error': str(e)
        }

def test_error_handling():
    """Test error handling for image storage and retrieval."""
    logger.info("=== ERROR HANDLING TEST ===")
    try:
        results = {}
        
        # Test 1: Invalid image data
        logger.info("Testing invalid image data handling")
        try:
            # Pass string instead of image data
            result = save_uploaded_image("This is not an image", "invalid_image.jpg")
            results['invalid_image'] = {
                'status': 'FAILED',
                'reason': 'Invalid image data did not raise exception',
                'result': result
            }
            logger.error("Invalid image data did not raise exception")
        except Exception as e:
            results['invalid_image'] = {
                'status': 'SUCCESS',
                'error': str(e),
                'summary': 'Invalid image data correctly raised exception'
            }
            logger.info(f"Invalid image data correctly raised exception: {str(e)}")
        
        # Test 2: Nonexistent S3 key retrieval
        logger.info("Testing nonexistent S3 key retrieval")
        try:
            # Generate a likely nonexistent S3 key
            nonexistent_key = f"receipts/nonexistent/key_{generate_random_string()}.jpg"
            
            # Try to generate a presigned URL
            url = generate_presigned_url(nonexistent_key)
            
            if url is None:
                results['nonexistent_s3_key'] = {
                    'status': 'SUCCESS',
                    'summary': 'Nonexistent S3 key correctly returned None'
                }
                logger.info("Nonexistent S3 key correctly returned None")
            else:
                results['nonexistent_s3_key'] = {
                    'status': 'FAILED',
                    'reason': 'Nonexistent S3 key returned a URL',
                    'url': url[:100] if url else None
                }
                logger.error(f"Nonexistent S3 key returned a URL: {url[:100] if url else None}")
        except Exception as e:
            # Some implementations might throw an exception for nonexistent keys
            results['nonexistent_s3_key'] = {
                'status': 'ALTERNATIVE_SUCCESS',
                'error': str(e),
                'summary': 'Nonexistent S3 key raised exception'
            }
            logger.info(f"Nonexistent S3 key raised exception: {str(e)}")
        
        # Test 3: Empty S3 key retrieval
        logger.info("Testing empty S3 key retrieval")
        try:
            # Try to generate a presigned URL with empty key
            url = generate_presigned_url("")
            
            if url is None:
                results['empty_s3_key'] = {
                    'status': 'SUCCESS',
                    'summary': 'Empty S3 key correctly returned None'
                }
                logger.info("Empty S3 key correctly returned None")
            else:
                results['empty_s3_key'] = {
                    'status': 'FAILED',
                    'reason': 'Empty S3 key returned a URL',
                    'url': url[:100] if url else None
                }
                logger.error(f"Empty S3 key returned a URL: {url[:100] if url else None}")
        except Exception as e:
            results['empty_s3_key'] = {
                'status': 'ALTERNATIVE_SUCCESS',
                'error': str(e),
                'summary': 'Empty S3 key raised exception'
            }
            logger.info(f"Empty S3 key raised exception: {str(e)}")
        
        # Test 4: Receipt.s3_url property error handling
        with app.app_context():
            logger.info("Testing Receipt.s3_url property error handling")
            try:
                # Create a receipt with an invalid S3 key
                test_receipt = Receipt(
                    user_id=1,
                    vendor_name="Test Vendor",
                    total_amount=10.0,
                    s3_key="invalid/s3/key.jpg"
                )
                
                # Try to access the s3_url property
                s3_url = test_receipt.s3_url
                
                if s3_url is None:
                    results['receipt_s3_url_property'] = {
                        'status': 'SUCCESS',
                        'summary': 'Receipt.s3_url property correctly returned None for invalid key'
                    }
                    logger.info("Receipt.s3_url property correctly returned None for invalid key")
                else:
                    results['receipt_s3_url_property'] = {
                        'status': 'FAILED',
                        'reason': 'Receipt.s3_url property returned a URL for invalid key',
                        'url': s3_url[:100] if s3_url else None
                    }
                    logger.error(f"Receipt.s3_url property returned a URL for invalid key: {s3_url[:100] if s3_url else None}")
            except Exception as e:
                results['receipt_s3_url_property'] = {
                    'status': 'FAILED',
                    'error': str(e),
                    'summary': 'Receipt.s3_url property raised unhandled exception'
                }
                logger.error(f"Receipt.s3_url property raised unhandled exception: {str(e)}")
        
        return {
            'test_name': 'error_handling',
            'results': results,
            'summary': 'Check results to see if error handling is working correctly'
        }
    
    except Exception as e:
        logger.error(f"Error in error handling test: {str(e)}")
        traceback.print_exc()
        return {
            'test_name': 'error_handling',
            'status': 'ERROR',
            'error': str(e)
        }

def run_all_tests():
    """Run all tests and compile results."""
    all_results = {}
    
    try:
        # Test 1: Path traversal vulnerability
        all_results['path_traversal'] = test_path_traversal_vulnerability()
        
        # Test 2: File extension validation
        all_results['file_extension'] = test_file_extension_validation()
        
        # Test 3: S3 storage mechanism
        all_results['s3_storage'] = test_s3_storage_mechanism()
        
        # Test 4: Image retrieval
        all_results['image_retrieval'] = test_image_retrieval()
        
        # Test 5: Error handling
        all_results['error_handling'] = test_error_handling()
        
        return all_results
    except Exception as e:
        logger.error(f"Error running all tests: {str(e)}")
        traceback.print_exc()
        return {
            'status': 'ERROR',
            'error': str(e)
        }

if __name__ == "__main__":
    print("=== RUNNING IMAGE STORAGE ANALYSIS ===")
    
    # Run all tests
    test_results = run_all_tests()
    
    # Print summary
    print("\n=== TEST RESULTS SUMMARY ===")
    
    for test_name, result in test_results.items():
        if 'summary' in result:
            print(f"{test_name}: {result['summary']}")
        elif 'status' in result and result['status'] == 'ERROR':
            print(f"{test_name}: ERROR - {result.get('error', 'Unknown error')}")
        else:
            print(f"{test_name}: See detailed results")
    
    print("\nDetailed test results are available in the logger output.")
    print("Analysis complete.")