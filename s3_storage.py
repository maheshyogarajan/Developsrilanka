"""
S3 Storage module for handling image uploads and retrievals from AWS S3.
This module provides both functions and a class for uploading images to S3 and retrieving them.
"""

import os
import logging
import io
import traceback
import boto3
from botocore.exceptions import ClientError
from PIL import Image
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class S3Storage:
    """Class for managing S3 operations with an initialized client instance."""
    
    def __init__(self):
        """Initialize the S3Storage class with boto3 client."""
        self.s3_client = self._get_s3_client()
        self.bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
    
    def _get_s3_client(self):
        """
        Create and return an S3 client using environment variables for credentials.
        
        Returns:
            boto3.client: The S3 client object
        """
        try:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                region_name=os.environ.get('AWS_REGION')
            )
            return s3_client
        except Exception as e:
            logger.error(f"Failed to create S3 client: {str(e)}")
            raise
    
    def upload_image(self, image, organization_id=None, receipt_id=None):
        """
        Upload an image to S3 bucket.
        
        Args:
            image: PIL Image object or path to image file
            organization_id: Organization ID to include in the S3 key path
            receipt_id: Receipt ID to include in the S3 key path
            
        Returns:
            str: S3 key of the uploaded image
        """
        try:
            # Log S3 client details for debugging
            logger.info(f"S3 UPLOAD - S3 client initialized: {self.s3_client is not None}")
            logger.info(f"S3 UPLOAD - Using bucket name: {self.bucket_name}")
            
            # Log input parameters
            logger.info(f"S3 UPLOAD - organization_id: {organization_id}, receipt_id: {receipt_id}")
            if hasattr(image, 'size'):
                logger.info(f"S3 UPLOAD - Image size: {image.size}, format: {getattr(image, 'format', 'unknown')}")
            elif isinstance(image, str):
                logger.info(f"S3 UPLOAD - Image path: {image}")
            else:
                logger.info(f"S3 UPLOAD - Image type: {type(image)}")
            
            # Generate a unique key for the image
            image_id = str(uuid.uuid4())
            logger.info(f"S3 UPLOAD - Generated UUID: {image_id}")
            
            # Create a path based on organization and receipt if provided
            # Use test/ folder path to match the format that's known to work with AWS 
            if organization_id and receipt_id:
                s3_key = f"test/{organization_id}_{receipt_id}_{image_id}.jpg"
            elif organization_id:
                s3_key = f"test/{organization_id}_{image_id}.jpg"
            else:
                s3_key = f"test/{image_id}.jpg"
            
            logger.info(f"S3 UPLOAD - Generated S3 key: {s3_key}")
            
            # Print all AWS environment variables (masking the values)
            logger.info(f"S3 UPLOAD - AWS_S3_BUCKET_NAME present: {bool(os.environ.get('AWS_S3_BUCKET_NAME'))}")
            logger.info(f"S3 UPLOAD - AWS_ACCESS_KEY_ID present: {bool(os.environ.get('AWS_ACCESS_KEY_ID'))}")
            logger.info(f"S3 UPLOAD - AWS_SECRET_ACCESS_KEY present: {bool(os.environ.get('AWS_SECRET_ACCESS_KEY'))}")
            logger.info(f"S3 UPLOAD - AWS_REGION present: {bool(os.environ.get('AWS_REGION'))}")
            
            # Handle different image input types
            if isinstance(image, str):  # If image is a file path
                logger.info(f"S3 UPLOAD - Uploading file from path: {image}")
                try:
                    # Check if file exists
                    if not os.path.exists(image):
                        logger.error(f"S3 UPLOAD - File does not exist: {image}")
                        raise FileNotFoundError(f"File does not exist: {image}")
                    
                    # Log file size
                    file_size = os.path.getsize(image)
                    logger.info(f"S3 UPLOAD - File size: {file_size} bytes")
                    
                    # Upload file
                    self.s3_client.upload_file(image, self.bucket_name, s3_key)
                    logger.info(f"S3 UPLOAD - File upload successful")
                except Exception as file_error:
                    logger.error(f"S3 UPLOAD - Error uploading file: {str(file_error)}")
                    logger.error(traceback.format_exc())
                    raise
            else:  # If image is a PIL Image object
                try:
                    logger.info(f"S3 UPLOAD - Uploading PIL Image object with format: {getattr(image, 'format', 'unknown')}")
                    img_byte_arr = io.BytesIO()
                    image.save(img_byte_arr, format='JPEG')
                    img_byte_arr.seek(0)
                    buffer_size = img_byte_arr.getbuffer().nbytes
                    logger.info(f"S3 UPLOAD - Image byte array size: {buffer_size} bytes")
                    
                    # If buffer size is 0, something went wrong
                    if buffer_size == 0:
                        logger.error("S3 UPLOAD - Image buffer size is 0 bytes")
                        raise ValueError("Image buffer size is 0 bytes")
                    
                    # Upload byte array
                    logger.info(f"S3 UPLOAD - Starting upload_fileobj to {self.bucket_name}, key: {s3_key}")
                    self.s3_client.upload_fileobj(img_byte_arr, self.bucket_name, s3_key)
                    logger.info(f"S3 UPLOAD - upload_fileobj completed successfully")
                except Exception as img_error:
                    logger.error(f"S3 UPLOAD - Error preparing or uploading image: {str(img_error)}")
                    logger.error(traceback.format_exc())
                    raise
            
            logger.info(f"S3 UPLOAD - Successfully uploaded image to S3: {s3_key}")
            
            # Verify the upload was successful by checking if the object exists
            try:
                logger.info(f"S3 UPLOAD - Verifying upload with head_object for key: {s3_key}")
                response = self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
                logger.info(f"S3 UPLOAD - Verified image exists in S3 at key: {s3_key}")
                logger.info(f"S3 UPLOAD - Object metadata: {response}")
            except Exception as verify_error:
                logger.warning(f"S3 UPLOAD - Could not verify image upload: {str(verify_error)}")
                logger.warning(traceback.format_exc())
                # Continue anyway since the upload might still have succeeded
                
            return s3_key
            
        except ClientError as e:
            logger.error(f"S3 UPLOAD - Error uploading image to S3: {str(e)}")
            logger.error(traceback.format_exc())
            raise
        except Exception as e:
            logger.error(f"S3 UPLOAD - Unexpected error uploading image to S3: {str(e)}")
            logger.error(traceback.format_exc())
            raise
    
    def get_image(self, s3_key):
        """
        Retrieve an image from S3 bucket.
        
        Args:
            s3_key: S3 key of the image to retrieve
            
        Returns:
            PIL.Image: The retrieved image as a PIL Image object
        """
        try:
            # Create a byte stream to store the retrieved image
            img_data = io.BytesIO()
            
            # Download the image to the byte stream
            self.s3_client.download_fileobj(self.bucket_name, s3_key, img_data)
            img_data.seek(0)
            
            # Open the image with PIL
            image = Image.open(img_data)
            
            logger.info(f"Successfully retrieved image from S3: {s3_key}")
            return image
            
        except ClientError as e:
            logger.error(f"Error retrieving image from S3: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error retrieving image from S3: {str(e)}")
            raise
    
    def generate_presigned_url(self, s3_key, expiration=3600):
        """
        Generate a presigned URL for an S3 object.
        
        Args:
            s3_key: S3 key of the object
            expiration: Time in seconds for the URL to remain valid (default: 1 hour)
            
        Returns:
            str: Presigned URL
        """
        try:
            if not s3_key or s3_key == "":
                logger.warning(f"Empty S3 key provided, cannot generate presigned URL")
                return None
                
            logger.info(f"Generating presigned URL for S3 key: {s3_key} in bucket: {self.bucket_name}")
            
            # Check if the object exists before generating URL
            try:
                self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
                logger.info(f"Object exists in S3: {s3_key}")
            except ClientError as e:
                if e.response['Error']['Code'] == '404':
                    logger.error(f"Object does not exist in S3: {s3_key}")
                    return None
                else:
                    logger.error(f"Error checking object existence: {str(e)}")
                    # Continue anyway to try generating the URL
            
            # Generate the presigned URL
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': s3_key},
                ExpiresIn=expiration
            )
            
            logger.info(f"Successfully generated presigned URL for {s3_key}")
            return url
            
        except ClientError as e:
            logger.error(f"Error generating presigned URL for {s3_key}: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error generating presigned URL for {s3_key}: {str(e)}")
            return None
    
    def delete_image(self, s3_key):
        """
        Delete an image from S3 bucket.
        
        Args:
            s3_key: S3 key of the image to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            
            logger.info(f"Successfully deleted image from S3: {s3_key}")
            return True
            
        except ClientError as e:
            logger.error(f"Error deleting image from S3: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error deleting image from S3: {str(e)}")
            return False

def get_s3_client():
    """
    Create and return an S3 client using environment variables for credentials.
    
    Returns:
        boto3.client: The S3 client object
    """
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            region_name=os.environ.get('AWS_REGION')
        )
        return s3_client
    except Exception as e:
        logger.error(f"Failed to create S3 client: {str(e)}")
        raise

def upload_image_to_s3(image, organization_id=None, receipt_id=None):
    """
    Upload an image to S3 bucket.
    
    Args:
        image: PIL Image object or path to image file
        organization_id: Organization ID to include in the S3 key path
        receipt_id: Receipt ID to include in the S3 key path
        
    Returns:
        str: S3 key of the uploaded image
    """
    try:
        s3_client = get_s3_client()
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        
        # Generate a unique key for the image
        image_id = str(uuid.uuid4())
        
        # Create a path based on organization and receipt if provided
        # Use test/ folder path to match the format that's known to work with AWS
        if organization_id and receipt_id:
            s3_key = f"test/{organization_id}_{receipt_id}_{image_id}.jpg"
        elif organization_id:
            s3_key = f"test/{organization_id}_{image_id}.jpg"
        else:
            s3_key = f"test/{image_id}.jpg"
        
        # Handle different image input types
        if isinstance(image, str):  # If image is a file path
            s3_client.upload_file(image, bucket_name, s3_key)
        else:  # If image is a PIL Image object
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='JPEG')
            img_byte_arr.seek(0)
            
            s3_client.upload_fileobj(img_byte_arr, bucket_name, s3_key)
        
        logger.info(f"Successfully uploaded image to S3: {s3_key}")
        return s3_key
        
    except ClientError as e:
        logger.error(f"Error uploading image to S3: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error uploading image to S3: {str(e)}")
        raise

def get_image_from_s3(s3_key):
    """
    Retrieve an image from S3 bucket.
    
    Args:
        s3_key: S3 key of the image to retrieve
        
    Returns:
        PIL.Image: The retrieved image as a PIL Image object
    """
    try:
        s3_client = get_s3_client()
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        
        # Create a byte stream to store the retrieved image
        img_data = io.BytesIO()
        
        # Download the image to the byte stream
        s3_client.download_fileobj(bucket_name, s3_key, img_data)
        img_data.seek(0)
        
        # Open the image with PIL
        image = Image.open(img_data)
        
        logger.info(f"Successfully retrieved image from S3: {s3_key}")
        return image
        
    except ClientError as e:
        logger.error(f"Error retrieving image from S3: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error retrieving image from S3: {str(e)}")
        raise

def generate_presigned_url(s3_key, expiration=3600):
    """
    Generate a presigned URL for an S3 object.
    
    Args:
        s3_key: S3 key of the object
        expiration: Time in seconds for the URL to remain valid (default: 1 hour)
        
    Returns:
        str: Presigned URL
    """
    try:
        if not s3_key or s3_key == "":
            logger.warning(f"Empty S3 key provided, cannot generate presigned URL")
            return None
            
        s3_client = get_s3_client()
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        
        logger.info(f"Generating standalone presigned URL for S3 key: {s3_key} in bucket: {bucket_name}")
        
        # Check if the object exists before generating URL
        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            logger.info(f"Object exists in S3: {s3_key}")
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                logger.error(f"Object does not exist in S3: {s3_key}")
                return None
            else:
                logger.error(f"Error checking object existence: {str(e)}")
                # Continue anyway to try generating the URL
        
        # Generate the presigned URL
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': s3_key},
            ExpiresIn=expiration
        )
        
        logger.info(f"Successfully generated standalone presigned URL for {s3_key}")
        return url
        
    except ClientError as e:
        logger.error(f"Error generating standalone presigned URL for {s3_key}: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error generating standalone presigned URL for {s3_key}: {str(e)}")
        return None

def delete_image_from_s3(s3_key):
    """
    Delete an image from S3 bucket.
    
    Args:
        s3_key: S3 key of the image to delete
        
    Returns:
        bool: True if deletion was successful, False otherwise
    """
    try:
        s3_client = get_s3_client()
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        
        s3_client.delete_object(Bucket=bucket_name, Key=s3_key)
        
        logger.info(f"Successfully deleted image from S3: {s3_key}")
        return True
        
    except ClientError as e:
        logger.error(f"Error deleting image from S3: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error deleting image from S3: {str(e)}")
        return False