# Receipt Scanner Deployment Documentation

This folder contains all the documentation needed to deploy the Receipt Scanner application to developsrilanka.com. 

## Documentation Files

### Main Deployment Guide
- **DEPLOYMENT.md**: Comprehensive deployment instructions for production

### Server and Domain Setup
- **SERVER_SETUP.md**: Detailed server configuration and setup guide
- **DNS_SETUP.md**: DNS configuration instructions for developsrilanka.com

### Deployment Options
- **REPLIT_DEPLOYMENT.md**: Instructions for deploying directly from Replit

### Checklists and Verification
- **DEPLOYMENT_CHECKLIST.md**: Complete checklist for pre-deployment, deployment, and post-deployment tasks

## Deployment Overview

The Receipt Scanner application can be deployed in several ways:

1. **Traditional Server Deployment**: Follow instructions in DEPLOYMENT.md and SERVER_SETUP.md
2. **Replit Deployment**: Use Replit's built-in deployment tools following REPLIT_DEPLOYMENT.md

## Required Environment Variables

The application requires these environment variables in production:

```
FLASK_ENV=production
DATABASE_URL=postgresql://username:password@hostname:port/database
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
FACEBOOK_CLIENT_ID=your_facebook_client_id
FACEBOOK_CLIENT_SECRET=your_facebook_client_secret
SESSION_SECRET=your_secure_session_key
ENABLE_ASYNC_PROCESSING=True
```

## Quick Start

1. Choose your deployment method (server or Replit)
2. Configure environment variables
3. Set up the database
4. Deploy the application code
5. Configure the web server
6. Set up DNS records
7. Configure SSL certificates
8. Verify the deployment using the health check endpoint

## Support and Maintenance

For ongoing maintenance:
- Monitor the application using the /health endpoint
- Set up regular database backups
- Keep the server updated with security patches
- Regularly review logs for errors or performance issues