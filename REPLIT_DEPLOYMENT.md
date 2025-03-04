# Deploying to Replit

This guide covers deploying your Receipt Scanner application directly from Replit to a publicly accessible URL.

## 1. Using Replit Deployments

Replit offers built-in deployment functionality that makes it easy to deploy your Flask application with minimal configuration.

### Step 1: Prepare Your Application

Make sure your application:
- Has a proper requirement specification
- Uses port 5000 for the web server
- Runs correctly in the Replit environment

### Step 2: Configure Secrets

1. Go to the "Secrets" tab in your Replit project
2. Add all necessary environment variables:
   - SESSION_SECRET
   - GEMINI_API_KEY
   - GOOGLE_CLIENT_ID
   - GOOGLE_CLIENT_SECRET
   - FACEBOOK_CLIENT_ID
   - FACEBOOK_CLIENT_SECRET
   - DATABASE_URL
   - FLASK_ENV=production

### Step 3: Deploy Your Application

1. Click the "Deploy" button at the top of your Replit interface
2. Follow the prompts to create a new deployment
3. Choose the "HTTP Service" deployment type
4. Keep the default settings for your first deployment
5. Click "Deploy" to start the deployment process

### Step 4: Managing Your Deployment

Once deployed:
1. You'll get a unique .replit.app URL for your application
2. You can access the deployment logs from the deployments tab
3. You can configure automatic deployments based on git commits

## 2. Setting Up a Custom Domain

To use developsrilanka.com with your Replit deployment:

### Step 1: Add the Custom Domain in Replit

1. Go to the "Deployments" tab in your Replit project
2. Navigate to the domain settings section
3. Add "developsrilanka.com" as a custom domain
4. Replit will provide you with DNS records to configure

### Step 2: Configure DNS Settings

Update your DNS settings with your domain registrar:

1. Add a CNAME record:
   - Host: @
   - Value: [your-replit-deployment-url] (without https://)
   - TTL: 3600 (or as recommended)

2. Add a CNAME record for the www subdomain:
   - Host: www
   - Value: [your-replit-deployment-url] (without https://)
   - TTL: 3600 (or as recommended)

3. Wait for DNS propagation (typically 24-48 hours)

### Step 3: Verify Domain Ownership

1. Follow the verification steps in Replit's domain settings
2. This may involve adding TXT records to your DNS configuration

## 3. Database Configuration

Since your app uses PostgreSQL, ensure:

1. You're using the Replit Database URL environment variable
2. Your database connection code handles potential connection issues
3. You've migrated your schema to the production database

## 4. Monitoring and Maintenance

### Health Checks

Use the built-in health check endpoint to monitor your application:

```
https://developsrilanka.com/health
```

This endpoint will return a JSON response with the status of your application's components.

### Logs

Access deployment logs through the Replit interface to troubleshoot issues.

### Updates

To update your deployed application:

1. Make changes to your code in Replit
2. Test thoroughly in the development environment
3. Deploy the updated version using the Deploy button or automatic deployments

## 5. Best Practices for Replit Deployments

1. **Environment Variables**: Keep all sensitive information in Secrets
2. **Error Handling**: Implement robust error handling and logging
3. **Caching**: Implement caching where possible to improve performance
4. **Database Connections**: Properly manage connection pools
5. **Rate Limiting**: Consider implementing rate limiting for public endpoints
6. **CORS**: Configure CORS settings appropriately
7. **Security Headers**: Use appropriate security headers

## 6. Replit-Specific Considerations

### Always-On

For applications like yours that need to be accessible 24/7:

1. Enable "Always On" in your Replit project settings
2. This ensures your application doesn't go to sleep when inactive

### Resource Limitations

Be aware of Replit's resource limitations:

1. CPU and memory usage caps
2. Storage limitations
3. Bandwidth restrictions

### Backups

Regularly backup your database and important files:

1. Consider setting up automated database dumps
2. Store backups external to Replit for additional security