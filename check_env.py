import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Print all environment variables
print("All environment variables:")
for key, value in os.environ.items():
    print(f"{key}: {value}")

# Check for CSRF_HARDENING_ENABLED specifically
csrf_value = os.environ.get("CSRF_HARDENING_ENABLED")
print(f"\nCSRF_HARDENING_ENABLED: {csrf_value}")