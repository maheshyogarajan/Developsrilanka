"""
Migration script to add trust level and invitation reward tracking.
This enhances the user invitation system with gamification elements
while ensuring data integrity for existing records.
"""
import logging
import os
from datetime import datetime, timedelta, date
from app import db, app
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)

def add_trust_tracking_fields_to_user():
    """Add trust tracking fields to User model and initialize with appropriate values."""
    with app.app_context():
        try:
            # Check if columns already exist
            columns = db.session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'user'")).fetchall()
            column_names = [col[0] for col in columns]
            
            columns_added = False
            
            # Add subscription_status if it doesn't exist
            if 'subscription_status' not in column_names:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN subscription_status VARCHAR(30) NOT NULL DEFAULT 'free_trial'"))
                columns_added = True
                logging.info("Added subscription_status column to user table")
            
            # Add access_expiration_date if it doesn't exist
            if 'access_expiration_date' not in column_names:
                db.session.execute(text("""
                    ALTER TABLE \"user\" ADD COLUMN access_expiration_date TIMESTAMP WITHOUT TIME ZONE;
                    UPDATE \"user\" SET access_expiration_date = created_at + INTERVAL '14 days';
                    ALTER TABLE \"user\" ALTER COLUMN access_expiration_date SET NOT NULL;
                """))
                columns_added = True
                logging.info("Added access_expiration_date column to user table")
            
            # Add successful_invites_count if it doesn't exist
            if 'successful_invites_count' not in column_names:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN successful_invites_count INTEGER NOT NULL DEFAULT 0"))
                columns_added = True
                logging.info("Added successful_invites_count column to user table")
            
            # Add earned_access_days if it doesn't exist
            if 'earned_access_days' not in column_names:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN earned_access_days INTEGER NOT NULL DEFAULT 0"))
                columns_added = True
                logging.info("Added earned_access_days column to user table")
            
            # Add invitations_sent_today if it doesn't exist
            if 'invitations_sent_today' not in column_names:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN invitations_sent_today INTEGER NOT NULL DEFAULT 0"))
                columns_added = True
                logging.info("Added invitations_sent_today column to user table")
            
            # Add last_invitation_date if it doesn't exist
            if 'last_invitation_date' not in column_names:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN last_invitation_date DATE"))
                columns_added = True
                logging.info("Added last_invitation_date column to user table")
            
            # Add trust_level if it doesn't exist
            if 'trust_level' not in column_names:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN trust_level INTEGER NOT NULL DEFAULT 0"))
                columns_added = True
                logging.info("Added trust_level column to user table")
            
            # Add show_in_trust_ranking if it doesn't exist
            if 'show_in_trust_ranking' not in column_names:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN show_in_trust_ranking BOOLEAN NOT NULL DEFAULT FALSE"))
                columns_added = True
                logging.info("Added show_in_trust_ranking column to user table")
            
            # Add trust_points if it doesn't exist
            if 'trust_points' not in column_names:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN trust_points INTEGER NOT NULL DEFAULT 0"))
                columns_added = True
                logging.info("Added trust_points column to user table")
            
            # Add trust_ranking_consent_at if it doesn't exist
            if 'trust_ranking_consent_at' not in column_names:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN trust_ranking_consent_at TIMESTAMP WITHOUT TIME ZONE"))
                columns_added = True
                logging.info("Added trust_ranking_consent_at column to user table")
            
            # If columns were added, initialize data for existing users
            if columns_added:
                # Initialize trust level based on account age and activity
                db.session.execute(text("""
                    WITH user_activities AS (
                        SELECT 
                            u.id as user_id,
                            COUNT(DISTINCT r.id) as receipt_count,
                            COUNT(DISTINCT i.id) as invoice_count,
                            COUNT(DISTINCT b.id) as bank_account_count,
                            COUNT(DISTINCT fi.id) as friend_invitation_count,
                            COUNT(DISTINCT CASE WHEN fi.accepted = TRUE THEN fi.id ELSE NULL END) as accepted_invitation_count,
                            EXTRACT(DAY FROM NOW() - u.created_at) as account_age_days
                        FROM "user" u
                        LEFT JOIN receipt r ON r.user_id = u.id
                        LEFT JOIN invoice i ON i.user_id = u.id
                        LEFT JOIN bank_account b ON b.user_id = u.id
                        LEFT JOIN friend_invitation fi ON fi.invited_by_user_id = u.id
                        GROUP BY u.id
                    )
                    UPDATE "user" u
                    SET 
                        trust_points = COALESCE((
                            ua.receipt_count * 10 + 
                            ua.invoice_count * 15 + 
                            ua.bank_account_count * 20 + 
                            ua.friend_invitation_count * 5 + 
                            ua.accepted_invitation_count * 25
                        ), 0),
                        successful_invites_count = COALESCE(ua.accepted_invitation_count, 0),
                        trust_level = 
                            CASE 
                                WHEN ua.account_age_days >= 90 AND ua.accepted_invitation_count >= 2 THEN 3
                                WHEN ua.account_age_days >= 30 AND (ua.receipt_count + ua.invoice_count + ua.bank_account_count) >= 10 THEN 2
                                WHEN ua.account_age_days >= 7 THEN 1
                                ELSE 0
                            END
                    FROM user_activities ua
                    WHERE u.id = ua.user_id;
                """))
                
                # Set subscription_status based on access_expiration_date
                db.session.execute(text("""
                    UPDATE "user"
                    SET subscription_status = 
                        CASE 
                            WHEN access_expiration_date > NOW() THEN 'free_trial'
                            ELSE 'expired'
                        END;
                """))
                
                logging.info("Initialized trust levels and points for existing users")
            
            db.session.commit()
            logging.info("Successfully added and initialized trust tracking fields to User model")
            return True
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error adding trust tracking fields to User model: {str(e)}")
            return False

def add_fields_to_friend_invitation():
    """Add tracking fields to FriendInvitation model and initialize with appropriate values."""
    with app.app_context():
        try:
            # Check if columns already exist
            columns = db.session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'friend_invitation'")).fetchall()
            column_names = [col[0] for col in columns]
            
            columns_added = False
            
            # Add converted_to_user_id if it doesn't exist
            if 'converted_to_user_id' not in column_names:
                db.session.execute(text("""
                    ALTER TABLE friend_invitation ADD COLUMN converted_to_user_id INTEGER;
                    ALTER TABLE friend_invitation ADD CONSTRAINT fk_friend_invitation_converted 
                    FOREIGN KEY (converted_to_user_id) REFERENCES "user" (id);
                """))
                columns_added = True
                logging.info("Added converted_to_user_id column to friend_invitation table")
            
            # Add reward_days_granted if it doesn't exist
            if 'reward_days_granted' not in column_names:
                db.session.execute(text("ALTER TABLE friend_invitation ADD COLUMN reward_days_granted INTEGER DEFAULT 0"))
                columns_added = True
                logging.info("Added reward_days_granted column to friend_invitation table")
            
            # Add reward_granted_at if it doesn't exist
            if 'reward_granted_at' not in column_names:
                db.session.execute(text("ALTER TABLE friend_invitation ADD COLUMN reward_granted_at TIMESTAMP WITHOUT TIME ZONE"))
                columns_added = True
                logging.info("Added reward_granted_at column to friend_invitation table")
            
            # If columns were added, initialize data
            if columns_added:
                # For accepted invitations, try to link them to users with matching email addresses
                db.session.execute(text("""
                    UPDATE friend_invitation fi
                    SET converted_to_user_id = u.id
                    FROM "user" u
                    WHERE fi.email = u.email 
                    AND fi.accepted = TRUE;
                """))
                
                # Fix any null accepted_at values for accepted invitations
                db.session.execute(text("""
                    UPDATE friend_invitation 
                    SET accepted_at = created_at + INTERVAL '1 day' 
                    WHERE accepted = TRUE AND accepted_at IS NULL;
                """))
                
                logging.info("Initialized friend invitation tracking fields for existing records")
            
            db.session.commit()
            logging.info("Successfully added and initialized tracking fields to FriendInvitation model")
            return True
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error adding tracking fields to FriendInvitation model: {str(e)}")
            return False

def create_trust_activity_table():
    """Create TrustActivity table for tracking user actions that affect trust level."""
    with app.app_context():
        try:
            # Check if table already exists
            result = db.session.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'trust_activity'
                );
            """))
            table_exists = result.scalar()
            
            if not table_exists:
                db.session.execute(text("""
                    CREATE TABLE trust_activity (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES "user" (id),
                        activity_type VARCHAR(50) NOT NULL,
                        points INTEGER NOT NULL DEFAULT 0,
                        description VARCHAR(255),
                        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX idx_trust_activity_user_id ON trust_activity(user_id);
                """))
                logging.info("Created trust_activity table")
                return True
            else:
                logging.info("trust_activity table already exists")
                return True
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error creating trust_activity table: {str(e)}")
            return False

def create_trust_ranking_table():
    """Create TrustRanking table for monthly trust leaderboard."""
    with app.app_context():
        try:
            # Check if table already exists
            result = db.session.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'trust_ranking'
                );
            """))
            table_exists = result.scalar()
            
            if not table_exists:
                db.session.execute(text("""
                    CREATE TABLE trust_ranking (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES "user" (id),
                        ranking_month DATE NOT NULL,
                        trust_points INTEGER NOT NULL DEFAULT 0,
                        rank_position INTEGER,
                        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                        UNIQUE(user_id, ranking_month)
                    );
                    CREATE INDEX idx_trust_ranking_month ON trust_ranking(ranking_month);
                """))
                logging.info("Created trust_ranking table")
                return True
            else:
                logging.info("trust_ranking table already exists")
                return True
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error creating trust_ranking table: {str(e)}")
            return False

def initialize_trust_activities():
    """Create initial trust activity records for existing users based on their actions."""
    with app.app_context():
        try:
            # Check if there are any existing activities
            table_exists = db.engine.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'trust_activity'
                );
            """).scalar()
            
            if not table_exists:
                logging.info("Trust activity table does not exist, skipping initialization")
                return False
                
            existing_activities = db.engine.execute("""
                SELECT COUNT(*) FROM trust_activity;
            """).scalar()
            
            # Only populate if the table is empty
            if existing_activities == 0:
                # Insert receipt-related activities (safely with COALESCE)
                db.engine.execute("""
                    INSERT INTO trust_activity (user_id, activity_type, points, description, created_at)
                    SELECT 
                        user_id, 
                        'receipt_scan', 
                        10, 
                        'Historical receipt scan: ' || COALESCE(vendor_name, 'Unknown'), 
                        created_at
                    FROM receipt
                    WHERE user_id IS NOT NULL;
                """)
                
                # Insert invoice-related activities (safely with COALESCE)
                db.engine.execute("""
                    INSERT INTO trust_activity (user_id, activity_type, points, description, created_at)
                    SELECT 
                        user_id, 
                        'invoice_create', 
                        15, 
                        'Historical invoice creation: ' || COALESCE(invoice_number, 'Unknown'), 
                        created_at
                    FROM invoice
                    WHERE user_id IS NOT NULL;
                """)
                
                # Insert bank account-related activities (safely with COALESCE)
                db.engine.execute("""
                    INSERT INTO trust_activity (user_id, activity_type, points, description, created_at)
                    SELECT 
                        user_id, 
                        'bank_account_add', 
                        20, 
                        'Historical bank account: ' || COALESCE(bank_name, 'Unknown'), 
                        created_at
                    FROM bank_account
                    WHERE user_id IS NOT NULL;
                """)
                
                # Insert invitation-related activities
                db.engine.execute("""
                    INSERT INTO trust_activity (user_id, activity_type, points, description, created_at)
                    SELECT 
                        invited_by_user_id, 
                        CASE 
                            WHEN accepted = TRUE THEN 'invitation_accepted'
                            ELSE 'invitation_sent'
                        END, 
                        CASE 
                            WHEN accepted = TRUE THEN 25
                            ELSE 5
                        END, 
                        'Historical invitation to: ' || email, 
                        created_at
                    FROM friend_invitation
                    WHERE invited_by_user_id IS NOT NULL;
                """)
                
                logging.info("Added historical trust activities for existing users")
            else:
                logging.info("Trust activities already exist, skipping historical data import")
            
            return True
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error initializing trust activities: {str(e)}")
            return False

def calculate_user_rewards():
    """Calculate and apply invitation rewards for existing users."""
    with app.app_context():
        try:
            # Calculate rewards for users with successful invitations
            db.engine.execute("""
                WITH user_rewards AS (
                    SELECT 
                        invited_by_user_id,
                        COUNT(*) FILTER (WHERE accepted = TRUE) as accepted_count,
                        FLOOR(COUNT(*) FILTER (WHERE accepted = TRUE) / 5) * 30 as days_to_grant
                    FROM friend_invitation
                    WHERE accepted = TRUE
                    GROUP BY invited_by_user_id
                    HAVING COUNT(*) FILTER (WHERE accepted = TRUE) >= 5
                )
                UPDATE "user" u
                SET 
                    earned_access_days = ur.days_to_grant,
                    access_expiration_date = 
                        CASE 
                            WHEN u.access_expiration_date < NOW() THEN 
                                NOW() + (ur.days_to_grant * INTERVAL '1 day')
                            ELSE 
                                u.access_expiration_date + (ur.days_to_grant * INTERVAL '1 day')
                            END,
                    subscription_status = 
                        CASE 
                            WHEN ur.days_to_grant > 0 THEN 'invitation_extended'
                            ELSE subscription_status
                        END
                FROM user_rewards ur
                WHERE u.id = ur.invited_by_user_id AND ur.days_to_grant > 0;
            """)
            
            # Mark appropriate invitations as having granted rewards
            db.engine.execute("""
                WITH reward_groups AS (
                    SELECT
                        invited_by_user_id,
                        id,
                        ROW_NUMBER() OVER(PARTITION BY invited_by_user_id ORDER BY created_at) as invitation_number
                    FROM friend_invitation
                    WHERE accepted = TRUE
                    ORDER BY invited_by_user_id, created_at
                )
                UPDATE friend_invitation fi
                SET 
                    reward_days_granted = 
                        CASE 
                            WHEN (rg.invitation_number % 5) = 0 THEN 30
                            ELSE 0
                        END,
                    reward_granted_at = 
                        CASE 
                            WHEN (rg.invitation_number % 5) = 0 THEN NOW()
                            ELSE NULL
                        END
                FROM reward_groups rg
                WHERE fi.id = rg.id AND (rg.invitation_number % 5) = 0;
            """)
            
            logging.info("Calculated and applied invitation rewards for existing users")
            return True
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error calculating user rewards: {str(e)}")
            return False

def verify_data_consistency():
    """Verify and fix any data consistency issues after migration."""
    with app.app_context():
        try:
            # Fix any NULL trust_level values
            null_trust_levels = db.engine.execute(
                "SELECT COUNT(*) FROM \"user\" WHERE trust_level IS NULL").scalar()
            if null_trust_levels > 0:
                db.engine.execute("UPDATE \"user\" SET trust_level = 0 WHERE trust_level IS NULL")
                logging.info(f"Fixed {null_trust_levels} users with NULL trust_level values")
            
            # Fix any NULL trust_points values
            null_trust_points = db.engine.execute(
                "SELECT COUNT(*) FROM \"user\" WHERE trust_points IS NULL").scalar()
            if null_trust_points > 0:
                db.engine.execute("UPDATE \"user\" SET trust_points = 0 WHERE trust_points IS NULL")
                logging.info(f"Fixed {null_trust_points} users with NULL trust_points values")
            
            # Fix any NULL successful_invites_count values
            null_invite_counts = db.engine.execute(
                "SELECT COUNT(*) FROM \"user\" WHERE successful_invites_count IS NULL").scalar()
            if null_invite_counts > 0:
                db.engine.execute("UPDATE \"user\" SET successful_invites_count = 0 WHERE successful_invites_count IS NULL")
                logging.info(f"Fixed {null_invite_counts} users with NULL successful_invites_count values")
            
            # Fix any NULL earned_access_days values
            null_earned_days = db.engine.execute(
                "SELECT COUNT(*) FROM \"user\" WHERE earned_access_days IS NULL").scalar()
            if null_earned_days > 0:
                db.engine.execute("UPDATE \"user\" SET earned_access_days = 0 WHERE earned_access_days IS NULL")
                logging.info(f"Fixed {null_earned_days} users with NULL earned_access_days values")
            
            # Fix any NULL subscription_status values
            null_subscription = db.engine.execute(
                "SELECT COUNT(*) FROM \"user\" WHERE subscription_status IS NULL").scalar()
            if null_subscription > 0:
                db.engine.execute("UPDATE \"user\" SET subscription_status = 'free_trial' WHERE subscription_status IS NULL")
                logging.info(f"Fixed {null_subscription} users with NULL subscription_status values")
            
            # Fix any NULL access_expiration_date values
            null_expiration = db.engine.execute(
                "SELECT COUNT(*) FROM \"user\" WHERE access_expiration_date IS NULL").scalar()
            if null_expiration > 0:
                db.engine.execute("UPDATE \"user\" SET access_expiration_date = created_at + INTERVAL '14 days' WHERE access_expiration_date IS NULL")
                logging.info(f"Fixed {null_expiration} users with NULL access_expiration_date values")
            
            # Verify FriendInvitation data - fix any inconsistencies
            accepted_without_date = db.engine.execute(
                "SELECT COUNT(*) FROM friend_invitation WHERE accepted = TRUE AND accepted_at IS NULL").scalar()
            if accepted_without_date > 0:
                db.engine.execute("UPDATE friend_invitation SET accepted_at = created_at + INTERVAL '1 day' WHERE accepted = TRUE AND accepted_at IS NULL")
                logging.info(f"Fixed {accepted_without_date} accepted invitations with missing accepted_at timestamp")
            
            db.session.commit()
            logging.info("Verified and fixed data consistency issues")
            return True
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error verifying data consistency: {str(e)}")
            return False

def run_migration():
    """Run all migration steps with comprehensive data integrity checks."""
    logging.info("Starting trust level and invitation reward tracking migration")
    
    # Step 1: Add necessary columns to User model
    if add_trust_tracking_fields_to_user():
        logging.info("Successfully added and initialized trust tracking fields to User model")
    else:
        logging.error("Failed to add trust tracking fields to User model")
        return False
    
    # Step 2: Add necessary columns to FriendInvitation model
    if add_fields_to_friend_invitation():
        logging.info("Successfully added and initialized tracking fields to FriendInvitation model")
    else:
        logging.error("Failed to add tracking fields to FriendInvitation model")
        return False
    
    # Step 3: Create new tables
    if create_trust_activity_table():
        logging.info("Successfully created TrustActivity table")
    else:
        logging.error("Failed to create TrustActivity table")
        return False
    
    if create_trust_ranking_table():
        logging.info("Successfully created TrustRanking table")
    else:
        logging.error("Failed to create TrustRanking table")
        return False
    
    # Step 4: Initialize historical data
    if initialize_trust_activities():
        logging.info("Successfully initialized historical trust activities")
    else:
        logging.warning("Failed to initialize historical trust activities - continuing with migration")
    
    # Step 5: Calculate rewards for existing users with 5+ successful invitations
    if calculate_user_rewards():
        logging.info("Successfully calculated and applied invitation rewards")
    else:
        logging.warning("Failed to calculate invitation rewards - continuing with migration")
    
    # Step 6: Final data consistency check
    if verify_data_consistency():
        logging.info("Successfully verified data consistency")
    else:
        logging.error("Failed to verify data consistency")
        return False
    
    logging.info("Trust level and invitation reward tracking migration completed successfully")
    return True

if __name__ == "__main__":
    if run_migration():
        print("Migration completed successfully")
    else:
        print("Migration failed, check logs for details")