"""
A simple script to check existing organizations and users.
"""
from app import app, db
from models import Organization, User, OrganizationUser

with app.app_context():
    organizations = Organization.query.all()
    users = User.query.all()
    
    print("\n=== Current Organizations ===")
    for org in organizations:
        print(f"ID: {org.id}, Name: {org.name}")
        print(f"  Email: {org.email}, Website: {org.website}")
        print(f"  Created: {org.created_at}")
        print("  Members:")
        for org_user in OrganizationUser.query.filter_by(organization_id=org.id).all():
            user = User.query.get(org_user.user_id)
            print(f"    - {user.name} ({user.email}): {org_user.role} (Default: {org_user.is_default})")
        print()
    
    print("\n=== Current Users ===")
    for user in users:
        print(f"ID: {user.id}, Name: {user.name}, Email: {user.email}")
        print(f"  Role: {user.role}")
        default_org = None
        org_user = OrganizationUser.query.filter_by(user_id=user.id, is_default=True).first()
        if org_user:
            org = Organization.query.get(org_user.organization_id)
            default_org = org.name if org else None
        print(f"  Default Organization: {default_org}")
        print()