    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in email login/registration: {str(e)}")
        logging.error(traceback.format_exc())  # Log the full traceback
        flash('We encountered a problem processing your request. Please try again or contact support if the issue persists.', 'danger')
        return redirect(url_for('login'))

@app.route('/auth/google')
def google_auth():
    """Initiate Google OAuth authentication."""
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/google/callback')
def google_callback():
    """Handle the Google OAuth callback."""
    try:
        token = google.authorize_access_token()
        resp = google.get('userinfo')
        user_info = resp.json()
        
        # Extract user data
        social_id = user_info['id']
        email = user_info['email']
        name = user_info.get('name', '')
        picture = user_info.get('picture', '')
        
        # Check if user exists
        from models import User
        user = User.query.filter_by(social_id=social_id).first()
        
        if not user:
            # Create a new user
            user = User(
                social_id=social_id,
                social_provider='google',
                email=email,
                name=name,
                profile_pic=picture
            )
            db.session.add(user)
            db.session.commit()
            
            # Create Personal Finances organization for new Google OAuth users
            try:
                from user_organization_helper import create_personal_finances_organization
                create_personal_finances_organization(user)
                logging.info(f"Created Personal Finances organization for new Google OAuth user {user.id}")
            except Exception as e:
                logging.error(f"Error creating Personal Finances organization for Google OAuth user: {str(e)}")
        
        # Log the user in
        login_user(user)
        flash('Successfully logged in with Google!', 'success')
        
        # Check if there's a pending invitation token in the session
        invitation_token = session.get('invitation_token')
        if invitation_token:
            # Import required models
            from models import FriendInvitation, OrganizationInvitation
            
            # Process the invitation
            try:
                # Check for friend invitation first
                friend_invitation = FriendInvitation.query.filter_by(token=invitation_token).first()
                if friend_invitation and not friend_invitation.accepted and friend_invitation.expires_at > datetime.utcnow():
                    # Mark invitation as accepted
                    friend_invitation.accepted = True
                    friend_invitation.accepted_at = datetime.utcnow()
                    friend_invitation.accepted_by_user_id = user.id
                    db.session.commit()
                    
                    # Clear the token from session
                    session.pop('invitation_token', None)
                    
                    # Access invited_by relationship instead of sender (which doesn't exist)
                    flash(f"You've successfully accepted the invitation from {friend_invitation.invited_by.name}!", "success")
                    logging.info(f"User {user.id} accepted friend invitation with token {invitation_token} after Google login")
                    
                    # Redirect to profile page to show the accepted invitation
                    return redirect(url_for('profile'))
                
                # Check for organization invitation
                org_invitation = OrganizationInvitation.query.filter_by(token=invitation_token).first()
                if org_invitation and not org_invitation.accepted and not org_invitation.is_expired():
                    logging.info(f"Processing organization invitation for user {user.id} with token {invitation_token} after Google login")
                    
                    # Redirect to the organization invitation acceptance route
                    # Clear the token from session as it will be processed by the organization route
                    session.pop('invitation_token', None)
                    
                    # Redirect to the organization invitation acceptance route
                    return redirect(url_for('organizations.accept_invitation', token=invitation_token))
                
                # If we get here, neither invitation was valid
                logging.warning(f"Invalid or expired invitation token in session after Google login: {invitation_token}")
                session.pop('invitation_token', None)
                
            except Exception as e:
                logging.error(f"Error processing invitation after Google login: {str(e)}")
                logging.error(traceback.format_exc())
        
        # Redirect directly to scan page for better user experience
        return redirect(url_for('index'))
    
    except Exception as e:
        logging.error(f"Error in Google authentication: {str(e)}")
        flash('Failed to log in with Google.', 'danger')
        return redirect(url_for('login'))

@app.route('/auth/facebook')
def facebook_auth():
    """Initiate Facebook OAuth authentication."""
    redirect_uri = url_for('facebook_callback', _external=True)
    return facebook.authorize_redirect(redirect_uri)

@app.route('/auth/facebook/callback')
def facebook_callback():
    """Handle the Facebook OAuth callback."""
    try:
        token = facebook.authorize_access_token()
        resp = facebook.get('me', params={'fields': 'id,name,email,picture'})
        user_info = resp.json()
        
        # Extract user data
        social_id = user_info['id']
        email = user_info.get('email')
        
        # Facebook might not return email if user hasn't shared it
        if not email:
            flash('Email access is required to use this service.', 'warning')
            return redirect(url_for('login'))
        
        name = user_info.get('name', '')
        picture = user_info.get('picture', {}).get('data', {}).get('url', '')
        
        # Check if user exists
        from models import User
        user = User.query.filter_by(social_id=social_id).first()
        
        if not user:
            # Create a new user
            user = User(
                social_id=social_id,
                social_provider='facebook',
                email=email,
                name=name,
                profile_pic=picture
            )
            db.session.add(user)
            db.session.commit()
            
            # Create Personal Finances organization for new Facebook OAuth users
            try:
                from user_organization_helper import create_personal_finances_organization
                create_personal_finances_organization(user)
                logging.info(f"Created Personal Finances organization for new Facebook OAuth user {user.id}")
            except Exception as e:
                logging.error(f"Error creating Personal Finances organization for Facebook OAuth user: {str(e)}")
        
        # Log the user in
        login_user(user)
        flash('Successfully logged in with Facebook!', 'success')
        
        # Check if there's a pending invitation token in the session
        invitation_token = session.get('invitation_token')
        if invitation_token:
            # Import required models
            from models import FriendInvitation, OrganizationInvitation
            
            # Process the invitation
            try:
                # Check for friend invitation first
                friend_invitation = FriendInvitation.query.filter_by(token=invitation_token).first()
                if friend_invitation and not friend_invitation.accepted and friend_invitation.expires_at > datetime.utcnow():
                    # Mark invitation as accepted
                    friend_invitation.accepted = True
                    friend_invitation.accepted_at = datetime.utcnow()
                    friend_invitation.accepted_by_user_id = user.id
                    db.session.commit()
                    
                    # Clear the token from session
                    session.pop('invitation_token', None)
                    
                    # Access invited_by relationship instead of sender (which doesn't exist)
                    flash(f"You've successfully accepted the invitation from {friend_invitation.invited_by.name}!", "success")
                    logging.info(f"User {user.id} accepted friend invitation with token {invitation_token} after Facebook login")
                    
                    # Redirect to profile page to show the accepted invitation
                    return redirect(url_for('profile'))
                
                # Check for organization invitation
                org_invitation = OrganizationInvitation.query.filter_by(token=invitation_token).first()
                if org_invitation and not org_invitation.accepted and not org_invitation.is_expired():
                    logging.info(f"Processing organization invitation for user {user.id} with token {invitation_token} after Facebook login")
                    
                    # Redirect to the organization invitation acceptance route
                    # Clear the token from session as it will be processed by the organization route
                    session.pop('invitation_token', None)
                    
                    # Redirect to the organization invitation acceptance route
                    return redirect(url_for('organizations.accept_invitation', token=invitation_token))
                
                # If we get here, neither invitation was valid
                logging.warning(f"Invalid or expired invitation token in session after Facebook login: {invitation_token}")
                session.pop('invitation_token', None)
                
            except Exception as e:
                logging.error(f"Error processing invitation after Facebook login: {str(e)}")
                logging.error(traceback.format_exc())
        
        # Redirect directly to scan page for better user experience
        return redirect(url_for('index'))
    
    except Exception as e:
        logging.error(f"Error in Facebook authentication: {str(e)}")
        flash('Failed to log in with Facebook.', 'danger')
        return redirect(url_for('login'))

@app.route('/logout', methods=['GET', 'POST'] if not CSRF_HARDENING_ENABLED else ['POST'])
@login_required
def logout():
    """Log the user out."""
    print(f"DEBUG: Logout route accessed with CSRF_HARDENING_ENABLED={CSRF_HARDENING_ENABLED}")
    logout_user()
    # Let's not redirect to login page with a flash message
    # The user knows they logged out, and it clutters the login page
    return redirect(url_for('home'))

@app.route('/profile')
@login_required
def profile():
    """Show the user's profile page."""
    try:
        logging.info(f"Accessing profile page for user: {current_user.id} - {current_user.name}")
        
        # Import the FriendInvitation model
        from models import FriendInvitation
        
        # Get friend invitations sent by the user
        friend_invitations = FriendInvitation.query.filter_by(
            invited_by_user_id=current_user.id
        ).order_by(FriendInvitation.created_at.desc()).all()
        
        # Process invitations to ensure proper display and status
        for invitation in friend_invitations:
            # Check if the invited person is an existing user
            from models import User
            existing_user = User.query.filter_by(email=invitation.email).first()
            invitation.is_existing_user = existing_user is not None
            
            # Fix the is_expired check by using a closure pattern
            def make_is_expired(inv): 
                exp_date = inv.expires_at
                return lambda: datetime.utcnow() > exp_date
                
            invitation.is_expired = make_is_expired(invitation)
            
            # Hide the token attribute to prevent it from showing in the template
            # but keep it accessible for backend operations
            invitation._token = invitation.token
            
            # Modify how the invitation is rendered in string context to prevent token leakage
            invitation.__str__ = lambda: f"Invitation to {invitation.email}"
            invitation.__repr__ = lambda: f"Invitation to {invitation.email}"
        
        # Get trust activity statistics
        from models import TrustActivity
        
        # Reset the invitation counter if needed (e.g., if it's a new day)
        current_user.reset_invitation_counter()
            
        # Count successful invitations
        successful_invites = FriendInvitation.query.filter_by(
            invited_by_user_id=current_user.id,
            accepted=True
        ).count()
        
        # Get invitation stats
        invitation_stats = {
            'sent_total': len(friend_invitations),
            'sent_today': current_user.invitations_sent_today,
            'daily_limit': current_user.get_daily_invitation_limit(),
            'successful': successful_invites,
            'available': current_user.get_available_invitations()
        }
        
        # Get recently earned trust points
        recent_activities = TrustActivity.query.filter_by(
            user_id=current_user.id
        ).order_by(TrustActivity.created_at.desc()).limit(5).all()
        
        # Get comprehensive trust activity summary
        trust_summary = current_user.get_trust_activity_summary()
        
        # Get subscription information
        subscription_info = {
            'status': current_user.get_subscription_status_display(),
            'days_remaining': current_user.get_days_remaining(),
            'is_expired': current_user.is_access_expired(),
            'expiration_date': current_user.access_expiration_date
        }
        
        # Prepare template context for error logging
        template_context = {
            'sent_friend_invitations': f"<List[{len(friend_invitations)}]>",
            'invitation_stats': invitation_stats,
            'recent_activities': f"<List[{len(recent_activities)}]>",
            'trust_summary': trust_summary,
            'subscription_info': subscription_info
        }
        
        try:
            return render_template(
                'profile.html', 
                sent_friend_invitations=friend_invitations,
                invitation_stats=invitation_stats,
                recent_activities=recent_activities,
                trust_summary=trust_summary,
                subscription_info=subscription_info,
                debug_timestamp=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            )
        except Exception as template_error:
            # Import here to avoid circular imports
            from error_logger import log_template_error
            log_template_error('profile.html', str(template_error), profile, template_context)
            flash(f"Error loading profile template: {str(template_error)}", "danger")
            return redirect(url_for('home'))
    except Exception as e:
        logging.error(f"Error rendering profile page: {str(e)}")
        logging.error(traceback.format_exc())
        flash(f"Error loading profile: {str(e)}", "danger")
        return redirect(url_for('home'))

@app.route('/invite-friends')
@login_required
def invite_friends():
    """Show the invite friends page with trust level and invitation quota information."""
    # Check if we need to reset the daily invitation counter
    current_user.reset_invitation_counter()
    
    # Get invitation stats
    invitation_stats = {
        'daily_limit': current_user.get_daily_invitation_limit(),
        'sent_today': current_user.invitations_sent_today,
        'available': current_user.get_available_invitations(),
        'trust_level': current_user.trust_level,
        'trust_level_name': current_user.get_trust_level_name()
    }
    
    return render_template('invite_friends.html', 
                          invitation_stats=invitation_stats)
    
@app.route('/update-trust-ranking-visibility', methods=['POST'])
@login_required
def update_trust_ranking_visibility():
    """Update the user's visibility in trust rankings."""
    try:
        data = request.get_json()
        show_in_ranking = data.get('show_in_ranking', False)
        
        # Update the user's preference
        current_user.show_in_trust_ranking = show_in_ranking
        
        # If showing in ranking for the first time, set consent timestamp
        if show_in_ranking and not current_user.trust_ranking_consent_at:
            current_user.trust_ranking_consent_at = datetime.utcnow()
        
        db.session.commit()
        
        # Log the activity
        from models import TrustActivity
        activity_type = 'trust_ranking_opt_in' if show_in_ranking else 'trust_ranking_opt_out'
        description = 'Opted into trust ranking system' if show_in_ranking else 'Opted out of trust ranking system'
        
        trust_activity = TrustActivity(
            user_id=current_user.id,
            activity_type=activity_type,
            points=0,  # No points for this administrative action
            description=description
        )
        db.session.add(trust_activity)
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating trust ranking visibility: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/resend-invitation', methods=['POST'])
@login_required
def resend_invitation():
    """Resend an expired friend invitation."""
    invitation_id = request.form.get('invitation_id')
    if not invitation_id:
        flash('Invalid invitation ID.', 'danger')
        return redirect(url_for('profile'))
    
    try:
        # Import the FriendInvitation model
        from models import FriendInvitation
        
        # Find the invitation by ID and verify ownership
        invitation = FriendInvitation.query.filter_by(
            id=invitation_id, 
            invited_by_user_id=current_user.id
        ).first()
        
        if not invitation:
            flash('Invitation not found or you are not authorized to modify it.', 'danger')
            return redirect(url_for('profile'))
        
        # Update expiration date
        invitation.expires_at = datetime.utcnow() + timedelta(days=7)
        db.session.commit()
        
        # Resend the email
        if send_invitation_email(invitation.email, current_user, invitation.personal_message, invitation.token):
            flash(f'Invitation to {invitation.email} has been resent!', 'success')
        else:
            flash(f'Failed to resend invitation to {invitation.email}.', 'danger')
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error resending invitation: {str(e)}")
        flash('An error occurred while resending the invitation.', 'danger')
    
    return redirect(url_for('profile'))

@app.route('/cancel-invitation', methods=['POST'])
@login_required
def cancel_invitation():
    """Cancel a pending friend invitation."""
    invitation_id = request.form.get('invitation_id')
    if not invitation_id:
        flash('Invalid invitation ID.', 'danger')
        return redirect(url_for('profile'))
    
    try:
        # Import the FriendInvitation model
        from models import FriendInvitation
        
        # Find the invitation by ID and verify ownership
        invitation = FriendInvitation.query.filter_by(
            id=invitation_id, 
            invited_by_user_id=current_user.id
        ).first()
        
        if not invitation:
            flash('Invitation not found or you are not authorized to modify it.', 'danger')
            return redirect(url_for('profile'))
        
        # Delete the invitation
        db.session.delete(invitation)
        db.session.commit()
        
        flash(f'Invitation to {invitation.email} has been canceled.', 'success')
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error canceling invitation: {str(e)}")
        flash('An error occurred while canceling the invitation.', 'danger')
    
    return redirect(url_for('profile'))

@app.route('/send-invitations', methods=['POST'])
@login_required
def send_invitations():
    """Process and send friend invitations via email with trust-based limits."""
    emails_input = request.form.get('emails', '')
    personal_message = request.form.get('message', '')
    
    # Process email list (split by commas and clean)
    email_list = [email.strip() for email in emails_input.split(',') if email.strip()]
    
    if not email_list:
        flash('Please enter at least one email address.', 'warning')
        return redirect(url_for('invite_friends'))
    
    # Check available invitations
    available_invitations = current_user.get_available_invitations()
    if available_invitations <= 0:
        flash('You have reached your daily invitation limit. Please try again tomorrow.', 'warning')
        return redirect(url_for('invite_friends'))
    
    # Limit the number of emails to process based on available invitations
    if len(email_list) > available_invitations:
        email_list = email_list[:available_invitations]
        flash(f'Only processing the first {available_invitations} email(s) due to your daily invitation limit.', 'info')
    
    # Initialize counters for success/failure reporting
    sent_count = 0
    failed_emails = []
    
    # Import the FriendInvitation model
    from models import FriendInvitation
    
    for email in email_list:
        try:
            # Use more comprehensive email validation
            from email_validator import validate_email, EmailNotValidError
            
            try:
                # Validate the email
                valid = validate_email(email)
                # Convert to normal form (lowercase, etc)
                normalized_email = valid.email
                
                # Check for existing invitation
                existing_invitation = FriendInvitation.query.filter_by(
                    invited_by_user_id=current_user.id,
                    email=normalized_email,
                    accepted=False
                ).first()
                
                if existing_invitation and not existing_invitation.is_expired():
                    # Update the invitation rather than creating a new one
                    expires_at = datetime.utcnow() + timedelta(days=7)
                    existing_invitation.expires_at = expires_at
                    existing_invitation.personal_message = personal_message
                    db.session.commit()
                    invitation = existing_invitation
                else:
                    # Create a new invitation with token
                    token = str(uuid.uuid4())
                    expires_at = datetime.utcnow() + timedelta(days=7)
                    
                    invitation = FriendInvitation(
                        invited_by_user_id=current_user.id,
                        email=normalized_email,
                        token=token,
                        expires_at=expires_at,
                        personal_message=personal_message
                    )
                    
                    db.session.add(invitation)
                    db.session.commit()
                
                # Send invitation email
                if send_invitation_email(normalized_email, current_user, personal_message, invitation.token):
                    sent_count += 1
                    # Track invitation in user's daily count
                    current_user.track_sent_invitation()
                    
                    # Track in trust activity system
                    from models import TrustActivity
                    trust_activity = TrustActivity(
                        user_id=current_user.id,
                        activity_type='invitation_sent',
                        points=5,
                        description=f'Sent invitation to: {normalized_email}'
                    )
                    db.session.add(trust_activity)
                    db.session.commit()
                    
                    logging.info(f"Successfully sent invitation to {normalized_email}")
                else:
                    failed_emails.append(normalized_email)
                    logging.warning(f"Failed to send invitation to {normalized_email}")
            except EmailNotValidError as e:
                failed_emails.append(f"{email} (invalid: {str(e)})")
                logging.warning(f"Invalid email format: {email} - {str(e)}")
                
        except Exception as e:
            error_details = traceback.format_exc()
            logging.error(f"Error sending invitation to {email}: {str(e)}")
            logging.error(f"Stack trace: {error_details}")
            failed_emails.append(email)
    
    # Provide feedback to the user
    if sent_count > 0:
        flash(f'Successfully sent {sent_count} invitation(s)!', 'success')
    
    if failed_emails:
        flash(f'Failed to send to: {", ".join(failed_emails)}', 'warning')
    
    return redirect(url_for('invite_friends'))

# Email sending function for invitations
@app.route('/accept-invitation/<token>', methods=['GET', 'POST'])
def accept_friend_invitation(token):
    """Handle friend invitation acceptance via unique token."""
    try:
        # Import the FriendInvitation model
        from models import FriendInvitation
        
        # Find the invitation by token
        invitation = FriendInvitation.query.filter_by(token=token).first()
        
        if not invitation:
            flash("Invalid invitation link. The invitation may have been removed or the link is incorrect.", "danger")
            return redirect(url_for('home'))
            
        # Check if invitation has expired
        if invitation.expires_at < datetime.utcnow():
            flash("This invitation has expired. Please ask your friend to send a new invitation.", "warning")
            return redirect(url_for('home'))
            
        # Check if invitation has already been accepted
        if invitation.accepted:
            flash("You have already accepted this invitation. Please log in to continue.", "info")
            return redirect(url_for('login'))
            
        # If it's a GET request, show the confirmation page
        if request.method == 'GET':
            return render_template('confirm_friend_invitation.html', invitation=invitation, token=token)
        
        # If it's a POST request, process the acceptance
        elif request.method == 'POST':
            # If user is logged in, mark as accepted
            if current_user.is_authenticated:
                invitation.accepted = True
                invitation.accepted_at = datetime.utcnow()
                invitation.accepted_by_user_id = current_user.id
                db.session.commit()
                
                # Access invited_by relationship instead of sender (which doesn't exist)
                flash(f"You've successfully accepted the invitation from {invitation.invited_by.name}!", "success")
                return redirect(url_for('profile'))
            else:
                # Store token in session for after registration/login
                session['invitation_token'] = token
                flash("Please sign up or log in to accept the invitation.", "info")
                return redirect(url_for('register', invitation=token))
    
    except Exception as e:
        logging.error(f"Error processing invitation acceptance: {str(e)}")
        flash("An error occurred while processing the invitation. Please try again later.", "danger")
        return redirect(url_for('home'))

def send_invitation_email(to_email, from_user, personal_message='', token=None):
    """
    Send an invitation email to a friend using SendGrid.
    
    Args:
        to_email: Recipient's email address
        from_user: User object of the sender
        personal_message: Optional personal message
        token: Optional invitation token for tracking acceptance
        
    Returns:
        Boolean indicating success or failure
    """
    import traceback  # For detailed error logging
    import os
    import json
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    
    # Import the SendGrid logger for detailed API logging
    from sendgrid_logger import (
        log_api_request, 
        log_api_response, 
        log_api_error, 
        log_email_content
    )
    
    try:
        # Get the app URL for links
        app_url = request.host_url.rstrip('/')
        
        # Create the subject for email
        subject = f"{from_user.name} invites you to join DevelopSriLanka.com"
        
        # Log the email attempt
        logging.info(f"Sending friend invitation email to: {to_email}")
        logging.info(f"From: {from_user.name} <{from_user.email}>")
        logging.info(f"Subject: {subject}")
        
        # Get the SendGrid API key
        sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
        
        # Check if SendGrid API key is configured
        if not sendgrid_api_key:
            logging.warning("SendGrid API key is not configured. Email will not be sent.")
            logging.info(f"Would have sent friend invitation email to: {to_email}")
            logging.info(f"Subject: {subject}")
            logging.info(f"With personal message: {personal_message}")
            flash("Email feature requires SendGrid API key configuration", "warning")
            return False
        
        # Create HTML email with branding
        # Note: keeping variable name as 'sender' in template for compatibility
        html_content = render_template('email/friend_invitation.html',
                                sender=from_user,  # Template uses 'sender', though model uses 'invited_by'
                                personal_message=personal_message,
                                app_url=app_url,
                                token=token,
                                current_year=datetime.utcnow().year)
        
        # Use the verified sender email address from SendGrid account
        sender_email = 'info@developsrilanka.com'
        
        # Log which sender email we're using
        logging.info(f"Using verified SendGrid sender email address: {sender_email}")
            
        # Validate the recipient email address 
        import re
        recipient_email = to_email.strip().lower()
        # More comprehensive email validation pattern
        email_pattern = r'^[a-zA-Z0-9][a-zA-Z0-9._%+-]*[a-zA-Z0-9]@[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}$'
        
        if not re.match(email_pattern, recipient_email):
            logging.error(f"Invalid recipient email format: {recipient_email}")
            return False
            
        # Create SendGrid mail message with proper sender formatting
        message = Mail(
            from_email=sender_email,  # Just use plain email address
            to_emails=recipient_email,  # Use the validated and normalized email
            subject=subject,
            html_content=html_content
        )
        
        # Set the friendly display name separately for better compatibility
        message.from_email.name = f"{from_user.name} via DevelopSriLanka"
        
        # Send the email using SendGrid
        try:
            # Log email sending details using both regular logger and SendGrid logger
            logging.info(f"Preparing to send friend invitation email with SendGrid:")
            logging.info(f"From: {from_user.name} via DevelopSriLanka <{sender_email}>")
            logging.info(f"To: {recipient_email}")
            logging.info(f"Subject: {subject}")
            
            # Use detailed SendGrid logger to log the API request
            log_api_request(
                recipient_email=recipient_email, 
                sender_email=sender_email,
                sender_name=f"{from_user.name} via DevelopSriLanka",
                subject=subject
            )
            
            # Log the email content for debugging
            log_email_content(html_content=html_content)
            
            # Create SendGrid client and send message
            sg = SendGridAPIClient(sendgrid_api_key)
            
            # Capture raw API request data for logging
            api_request = message.get()
            print(f"SENDGRID API REQUEST: {json.dumps(api_request, indent=2)}")
            
            # Send the message
            response = sg.send(message)
            
            # Log the response with both loggers
            logging.info(f"SendGrid response status code: {response.status_code}")
            logging.info(f"SendGrid response body: {response.body}")
            logging.info(f"SendGrid response headers: {response.headers}")
            
            # Use detailed SendGrid logger for response
            log_api_response(
                status_code=response.status_code,
                response_body=response.body,
                response_headers=response.headers
            )
            
            # Log successful sending
            logging.info(f"Successfully sent friend invitation via SendGrid to {recipient_email}")
            print(f"SENDGRID EMAIL SENT: Successfully sent friend invitation to {recipient_email}")
            
            # Create a success message with SPAM folder notice in red text
            from markupsafe import Markup
            success_message = Markup(f'Invitation sent successfully to {recipient_email}! <span style="color: red;">(Please ask your friend to check their SPAM folder if not visible in inbox)</span>')
            flash(success_message, 'success')
            
            return True
            
        except Exception as sendgrid_error:
            error_details = traceback.format_exc()
            
            # Standard logger
            logging.error(f"==== SENDGRID ERROR DETAILS ====")
            logging.error(f"Failed to send friend invitation via SendGrid to {recipient_email}")
            logging.error(f"Error type: {type(sendgrid_error).__name__}")
            logging.error(f"Error message: {str(sendgrid_error)}")
            logging.error(f"From email: {sender_email}")
            logging.error(f"From name: {from_user.name} via DevelopSriLanka")
            logging.error(f"Full error traceback: {error_details}")
            logging.error(f"==== END SENDGRID ERROR DETAILS ====")
            
            # Use detailed SendGrid logger for error
            log_api_error(
                error=sendgrid_error,
                recipient_email=recipient_email,
                error_type=type(sendgrid_error).__name__,
                detailed_traceback=True
            )
            
            # Also print to console for immediate debugging
            print(f"SENDGRID EMAIL ERROR: Failed to send friend invitation to {recipient_email}")
            print(f"Error type: {type(sendgrid_error).__name__}")
            print(f"Error message: {str(sendgrid_error)}")
            
            flash(f"Failed to send invitation: {str(sendgrid_error)}", "danger")
            return False
            
    except Exception as e:
        error_details = traceback.format_exc()
        logging.error(f"Failed to prepare friend invitation email: {str(e)}")
        logging.error(f"Error details: {error_details}")
        flash(f"Friend invitation system error: {str(e)}", "danger")
        return False

# Update the middleware to handle authentication required routes
@app.before_request
def check_authentication():
    """Check if user is authenticated for protected routes and set default organization."""
    # Set up default organization for authenticated users
    if current_user.is_authenticated:
        from models import Organization, OrganizationUser
        # Get the user's default organization - usually the first one they're a member of
        user_org = OrganizationUser.query.filter_by(user_id=current_user.id).first()
        if user_org:
            g.default_organization = Organization.query.get(user_org.organization_id)
        else:
            g.default_organization = None
    else:
        g.default_organization = None
            
    # Paths that require authentication
    protected_paths = [
        '/scan', 
        '/history', 
        '/analytics', 
        '/profile',
        '/receipts',
        '/export',
        '/export/excel',
        '/save',
        '/invite-friends',
        '/send-invitations'
    ]
    
    # Check for paths that start with these prefixes
    protected_prefixes = [
        '/receipts/',
        '/view_receipt/',
        '/reports/'
    ]
    
    # Check if the current path is protected and user is not authenticated
    is_protected = (
        request.path in protected_paths or
        any(request.path.startswith(prefix) for prefix in protected_prefixes)
    )
    
    if is_protected and not current_user.is_authenticated:
        # Store the requested URL for redirecting after login
        session['next'] = request.url
        flash('Please log in to access this page.', 'info')
        return redirect(url_for('login'))

@app.route('/api/log_client_error', methods=['POST'])
@csrf.exempt
def log_client_error():
    """
    API endpoint to receive and log client-side errors.
    This creates a bridge between frontend and backend error logging systems.
    """
    from error_logger import log_error, ErrorTypes
    
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No error data provided"}), 400
            
        # Extract required fields
        error_message = data.get('error_message', 'Unknown client error')
        component = data.get('component', 'client')
        error_type = data.get('error_type', ErrorTypes.CLIENT)
        additional_info = data.get('additional_info', {})
        
        # Add client metadata
        client_meta = {
            'user_agent': data.get('user_agent'),
            'url': data.get('url'),
            'client_error_id': data.get('error_id'),
            'client_timestamp': data.get('timestamp')
        }
        
        # Merge with any existing additional info
        if isinstance(additional_info, dict):
            additional_info.update(client_meta)
        else:
            additional_info = client_meta
            
        # Log the error using our backend system
        error_id = log_error(
            error=error_message,
            error_type=error_type,
            component=component,
            additional_info=additional_info,
            log_level="ERROR",
            capture_request=True
        )
        
        return jsonify({
            "success": True, 
            "error_id": error_id,
            "message": "Error logged successfully"
        })
        
    except Exception as e:
        # Log the exception in logging the client error
        logging.error(f"Error logging client error: {str(e)}")
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": "Server error processing client error log"
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
