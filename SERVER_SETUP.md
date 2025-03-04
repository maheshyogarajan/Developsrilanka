# Server Setup for developsrilanka.com

This document provides instructions for setting up a server to host the Receipt Scanner application at developsrilanka.com.

## 1. Server Requirements

Minimum recommended specifications:
- 2 vCPUs
- 4GB RAM
- 20GB SSD storage
- Ubuntu 22.04 LTS

Recommended cloud providers:
- DigitalOcean
- AWS Lightsail
- Linode
- Google Cloud Platform

## 2. Initial Server Setup

### Update System Packages

```bash
sudo apt update
sudo apt upgrade -y
```

### Create Non-Root User with Sudo Privileges

```bash
sudo adduser devopsuser
sudo usermod -aG sudo devopsuser
```

### Setup Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## 3. Install Required Software

### Install Python and Dependencies

```bash
sudo apt install -y python3 python3-pip python3-dev python3-venv
sudo apt install -y build-essential libssl-dev libffi-dev
sudo apt install -y python3-setuptools
```

### Install PostgreSQL

```bash
sudo apt install -y postgresql postgresql-contrib
```

### Install Redis (if using Celery for background tasks)

```bash
sudo apt install -y redis-server
```

### Install Nginx

```bash
sudo apt install -y nginx
```

## 4. Configure PostgreSQL

### Create Database and User

```bash
sudo -u postgres psql

CREATE DATABASE developsrilanka;
CREATE USER devopsuser WITH PASSWORD 'your_secure_password';
ALTER ROLE devopsuser SET client_encoding TO 'utf8';
ALTER ROLE devopsuser SET default_transaction_isolation TO 'read committed';
ALTER ROLE devopsuser SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE developsrilanka TO devopsuser;
\q
```

## 5. Set Up Application

### Clone Repository

```bash
git clone https://github.com/your-repository/develop-sri-lanka.git
cd develop-sri-lanka
```

### Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### Install Application Dependencies

```bash
pip install -r requirements.txt
pip install gunicorn
```

### Configure Application Environment Variables

Create a `.env` file:

```bash
nano .env
```

Add the following configuration (update with your values):

```
FLASK_ENV=production
DATABASE_URL=postgresql://devopsuser:your_secure_password@localhost/developsrilanka
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
FACEBOOK_CLIENT_ID=your_facebook_client_id
FACEBOOK_CLIENT_SECRET=your_facebook_client_secret
SESSION_SECRET=your_secure_session_key
ENABLE_ASYNC_PROCESSING=True
REDIS_URL=redis://localhost:6379/0
```

## 6. Configure Gunicorn

### Create a Systemd Service File

```bash
sudo nano /etc/systemd/system/developsrilanka.service
```

Add the following configuration:

```
[Unit]
Description=Develop Sri Lanka Receipt Scanner
After=network.target

[Service]
User=devopsuser
Group=www-data
WorkingDirectory=/home/devopsuser/develop-sri-lanka
Environment="PATH=/home/devopsuser/develop-sri-lanka/venv/bin"
EnvironmentFile=/home/devopsuser/develop-sri-lanka/.env
ExecStart=/home/devopsuser/develop-sri-lanka/venv/bin/gunicorn --workers 4 --bind 0.0.0.0:5000 wsgi:app
Restart=always

[Install]
WantedBy=multi-user.target
```

### Enable and Start the Service

```bash
sudo systemctl enable developsrilanka
sudo systemctl start developsrilanka
```

## 7. Configure Nginx as a Reverse Proxy

### Create Nginx Configuration

```bash
sudo nano /etc/nginx/sites-available/developsrilanka
```

Add the following configuration:

```nginx
server {
    listen 80;
    server_name developsrilanka.com www.developsrilanka.com;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static {
        alias /home/devopsuser/develop-sri-lanka/static;
        expires 30d;
    }
}
```

### Enable the Site

```bash
sudo ln -s /etc/nginx/sites-available/developsrilanka /etc/nginx/sites-enabled
sudo nginx -t
sudo systemctl restart nginx
```

## 8. Set Up SSL with Let's Encrypt

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d developsrilanka.com -d www.developsrilanka.com
```

## 9. Set Up Celery Worker (if using)

### Create a Systemd Service File for Celery

```bash
sudo nano /etc/systemd/system/developsrilanka-celery.service
```

Add the following configuration:

```
[Unit]
Description=Develop Sri Lanka Celery Worker
After=network.target

[Service]
User=devopsuser
Group=www-data
WorkingDirectory=/home/devopsuser/develop-sri-lanka
Environment="PATH=/home/devopsuser/develop-sri-lanka/venv/bin"
EnvironmentFile=/home/devopsuser/develop-sri-lanka/.env
ExecStart=/home/devopsuser/develop-sri-lanka/venv/bin/celery -A worker.celery worker --loglevel=info
Restart=always

[Install]
WantedBy=multi-user.target
```

### Enable and Start the Celery Service

```bash
sudo systemctl enable developsrilanka-celery
sudo systemctl start developsrilanka-celery
```

## 10. Monitoring and Maintenance

### Set Up System Monitoring

```bash
sudo apt install -y htop fail2ban
```

### Configure Automatic Updates

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

### Set Up Log Rotation

```bash
sudo nano /etc/logrotate.d/developsrilanka
```

Add the following configuration:

```
/home/devopsuser/develop-sri-lanka/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 devopsuser www-data
    sharedscripts
    postrotate
        systemctl reload developsrilanka.service
    endscript
}
```

## 11. Backup Configuration

### Set Up Automated PostgreSQL Backups

```bash
sudo apt install -y postgresql-client
```

Create a backup script:

```bash
nano /home/devopsuser/backup-db.sh
```

Add the following:

```bash
#!/bin/bash
BACKUP_DIR="/home/devopsuser/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DB_NAME="developsrilanka"
DB_USER="devopsuser"
BACKUP_FILE="$BACKUP_DIR/db_backup_$TIMESTAMP.sql"

mkdir -p $BACKUP_DIR
pg_dump -U $DB_USER $DB_NAME > $BACKUP_FILE
gzip $BACKUP_FILE

# Rotate backups - keep only the last 7 days
find $BACKUP_DIR -name "db_backup_*.sql.gz" -type f -mtime +7 -delete
```

Make it executable:

```bash
chmod +x /home/devopsuser/backup-db.sh
```

Add to crontab:

```bash
crontab -e
```

Add the following line:

```
0 2 * * * /home/devopsuser/backup-db.sh
```

## 12. Security Enhancements

### Configure fail2ban

```bash
sudo cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local
sudo nano /etc/fail2ban/jail.local
```

Modify settings as needed, then restart fail2ban:

```bash
sudo systemctl restart fail2ban
```

### Configure Security Headers in Nginx

Update your Nginx configuration:

```bash
sudo nano /etc/nginx/sites-available/developsrilanka
```

Add these headers:

```nginx
server {
    # ... existing configuration ...
    
    # Security headers
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; font-src 'self' https://cdn.jsdelivr.net;" always;
}
```

Restart Nginx:

```bash
sudo nginx -t
sudo systemctl restart nginx
```

## 13. Health Check Setup

Create a monitoring script to check the application's /health endpoint:

```bash
nano /home/devopsuser/health-check.sh
```

Add the following:

```bash
#!/bin/bash
HEALTH_ENDPOINT="https://developsrilanka.com/health"
EMAIL="admin@developsrilanka.com"

RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" $HEALTH_ENDPOINT)

if [ $RESPONSE -ne 200 ]; then
    echo "Health check failed with status code: $RESPONSE" | mail -s "ALERT: developsrilanka.com is DOWN" $EMAIL
    systemctl restart developsrilanka
fi
```

Make it executable:

```bash
chmod +x /home/devopsuser/health-check.sh
```

Add to crontab:

```bash
crontab -e
```

Add the following line:

```
*/5 * * * * /home/devopsuser/health-check.sh
```

This will check the health endpoint every 5 minutes.