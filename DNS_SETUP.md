# DNS Configuration Guide for developsrilanka.com

This document provides instructions for configuring DNS settings to host the Receipt Scanner application at developsrilanka.com.

## 1. Domain Registration

Ensure that the developsrilanka.com domain is registered with a domain registrar. Common registrars include:
- Namecheap
- GoDaddy
- Google Domains
- Cloudflare Registrar

## 2. DNS Record Configuration

After deployment, you'll need to configure the following DNS records with your domain registrar:

### A Records

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | @ | [Server IP Address] | 3600 (1 hour) |
| A | www | [Server IP Address] | 3600 (1 hour) |

Replace `[Server IP Address]` with the actual IP address of your production server.

### CNAME Records (Alternative to A records if using a cloud service)

If you're using a cloud provider that offers its own domain (like Heroku or Vercel):

| Type | Name | Value | TTL |
|------|------|-------|-----|
| CNAME | @ | [Cloud Provider Domain] | 3600 (1 hour) |
| CNAME | www | [Cloud Provider Domain] | 3600 (1 hour) |

### MX Records (For Email)

If you plan to use email services with the domain:

| Type | Name | Priority | Value | TTL |
|------|------|----------|-------|-----|
| MX | @ | 10 | [Mail Server 1] | 3600 |
| MX | @ | 20 | [Mail Server 2] | 3600 |

### TXT Records

For domain verification and email security:

| Type | Name | Value | TTL |
|------|------|-------|-----|
| TXT | @ | "v=spf1 include:[Mail Provider] ~all" | 3600 |

## 3. SSL Certificate Configuration

For secure HTTPS connections:

1. After DNS propagation (which can take up to 24-48 hours), install an SSL certificate on your server.
2. We recommend using Let's Encrypt for free SSL certificates:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d developsrilanka.com -d www.developsrilanka.com
```

## 4. DNS Propagation

After making DNS changes:

- DNS propagation typically takes 24-48 hours to complete globally
- You can check propagation status at https://dnschecker.org/
- During this time, the site may be accessible in some locations but not others

## 5. Cloudflare Setup (Optional but Recommended)

For enhanced security and performance:

1. Create a Cloudflare account
2. Add your domain to Cloudflare
3. Update your domain's nameservers to Cloudflare's nameservers (provided during setup)
4. Configure Cloudflare settings:
   - Enable HTTPS (Flexible/Full/Full Strict)
   - Enable Always Use HTTPS
   - Configure cache settings (recommend Standard caching for most assets)
   - Enable minification for HTML, CSS, and JavaScript

## 6. Testing DNS Configuration

After DNS propagation, verify your setup with:

```bash
dig developsrilanka.com
dig www.developsrilanka.com
```

Check that SSL is properly configured:
```bash
curl -I https://developsrilanka.com
```

## 7. Domain Monitoring

Set up monitoring for your domain:
- Enable domain auto-renewal
- Monitor SSL certificate expiration
- Set up uptime monitoring with a service like UptimeRobot or Pingdom

## Special Considerations for developsrilanka.com

1. Consider registering related domains to protect your brand:
   - developsrilanka.org
   - developsrilanka.net
   - develop-srilanka.com

2. Consider implementing DNSSEC for enhanced DNS security

3. Set up a subdomain for development/staging:
   - staging.developsrilanka.com
   - test.developsrilanka.com

## Additional Resources

- [Digital Ocean DNS Guide](https://www.digitalocean.com/community/tutorials/an-introduction-to-digitalocean-dns)
- [Cloudflare DNS Documentation](https://developers.cloudflare.com/dns/)
- [Let's Encrypt Documentation](https://letsencrypt.org/docs/)