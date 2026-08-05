from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import secrets
import string
import requests
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'jubayer-like-shop-secret-key-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///jubayer_like_shop.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ======================== DATABASE MODELS ========================

class APIKey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    like_limit = db.Column(db.Integer, default=0)
    daily_limit = db.Column(db.Integer, default=10)
    validity_days = db.Column(db.Integer, default=30)
    min_like_usage = db.Column(db.Integer, default=1)
    
    credits_used = db.Column(db.Integer, default=0)
    daily_used = db.Column(db.Integer, default=0)
    last_reset = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    is_auto_deactivated = db.Column(db.Boolean, default=False)
    deactivated_at = db.Column(db.DateTime)
    
    logs = db.relationship('APILog', backref='api_key_obj', lazy=True)
    
    def generate_key(self):
        random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(15))
        self.key = f"JLS-{random_part}"
        return self.key
    
    def reset_daily_if_needed(self):
        if self.last_reset.date() < datetime.utcnow().date():
            self.daily_used = 0
            self.last_reset = datetime.utcnow()
            db.session.commit()
            return True
        return False
    
    def check_validity(self):
        if not self.is_active:
            return False, "API Key is deactivated"
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False, "API Key has expired"
        if self.like_limit > 0 and self.credits_used >= self.like_limit:
            return False, "Like limit exceeded"
        self.reset_daily_if_needed()
        if self.daily_used >= self.daily_limit:
            return False, "Daily limit reached"
        return True, "Valid"
    
    def use_credit(self):
        self.credits_used += 1
        self.daily_used += 1
        db.session.commit()
    
    def renew(self, extra_likes):
        self.like_limit += extra_likes
        self.is_active = True
        self.is_auto_deactivated = False
        self.expires_at = datetime.utcnow() + timedelta(days=self.validity_days)
        db.session.commit()

class APILog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    api_key_id = db.Column(db.Integer, db.ForeignKey('api_key.id'), nullable=False)
    uid = db.Column(db.String(50))
    server = db.Column(db.String(10))
    likes_given = db.Column(db.Integer, default=0)
    likes_before = db.Column(db.Integer, default=0)
    likes_after = db.Column(db.Integer, default=0)
    player_name = db.Column(db.String(100))
    status = db.Column(db.String(20))
    response_data = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class MainAPISetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), default='Main API')
    url = db.Column(db.String(500))
    use_api_key = db.Column(db.Boolean, default=False)
    api_key = db.Column(db.String(100), default='')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AdminSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admin_key = db.Column(db.String(50), unique=True, nullable=False, default='JLS-ADMIN123456789')
    maintenance_mode = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

ADMIN_KEY = 'JLS-ADMIN123456789'

# ======================== ROUTES ========================

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        session.pop('_flashes', None)
    
    if request.method == 'POST':
        api_key = request.form.get('api_key', '').strip()
        
        if api_key == ADMIN_KEY:
            session['admin_logged_in'] = True
            flash('Welcome Admin!', 'success')
            return redirect(url_for('admin_panel'))
        
        user_key = APIKey.query.filter_by(key=api_key).first()
        if user_key:
            admin_setting = AdminSetting.query.first()
            if admin_setting and admin_setting.maintenance_mode:
                flash('⚠️ System is under maintenance.', 'danger')
                return render_template('login.html')
            
            session['user_logged_in'] = True
            session['user_key_id'] = user_key.id
            flash(f'Welcome {user_key.name}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid API Key!', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully', 'info')
    return redirect(url_for('landing'))

@app.route('/dashboard')
def dashboard():
    if not session.get('user_logged_in'):
        flash('Please login first!', 'danger')
        return redirect(url_for('login'))
    
    user_key = APIKey.query.get(session['user_key_id'])
    if not user_key:
        session.clear()
        flash('Session expired!', 'danger')
        return redirect(url_for('login'))
    
    if user_key.credits_used >= user_key.like_limit:
        if user_key.deactivated_at and (datetime.utcnow() - user_key.deactivated_at).days >= 3:
            user_key.is_active = False
            user_key.is_auto_deactivated = True
            db.session.commit()
    
    logs = APILog.query.filter_by(api_key_id=user_key.id).order_by(APILog.created_at.desc()).limit(50).all()
    remaining = user_key.like_limit - user_key.credits_used if user_key.like_limit > 0 else 0
    remaining_daily = user_key.daily_limit - user_key.daily_used if user_key.daily_limit > 0 else 0
    percentage = (user_key.credits_used / user_key.like_limit * 100) if user_key.like_limit > 0 else 0
    
    return render_template('dashboard.html', 
                         user=user_key, 
                         logs=logs, 
                         remaining=remaining,
                         remaining_daily=remaining_daily,
                         percentage=percentage)

@app.route('/jubayer/api/like', methods=['GET'])
def api_like():
    uid = request.args.get('uid')
    server_name = request.args.get('server_name', 'A')
    api_key = request.args.get('key')
    
    if not uid:
        return jsonify({'error': 'UID is required'}), 400
    
    if not api_key:
        return jsonify({'error': 'API Key is required'}), 401
    
    admin_setting = AdminSetting.query.first()
    if admin_setting and admin_setting.maintenance_mode:
        return jsonify({'error': 'System is under maintenance'}), 503
    
    user_key = APIKey.query.filter_by(key=api_key).first()
    if not user_key:
        return jsonify({'error': 'Invalid API Key'}), 401
    
    is_valid, message = user_key.check_validity()
    if not is_valid:
        return jsonify({'error': message}), 403
    
    main_api = MainAPISetting.query.filter_by(is_active=True).first()
    
    if main_api:
        if main_api.use_api_key and main_api.api_key:
            main_url = f"{main_api.url}?uid={uid}&server_name={server_name}&key={main_api.api_key}"
        else:
            main_url = f"{main_api.url}?uid={uid}&server_name={server_name}"
    else:
        main_url = f"https://like-api-jubayer.vercel.app/like?uid={uid}&server_name={server_name}"
    
    try:
        response = requests.get(main_url, timeout=30)
        data = response.json()
        
        log = APILog(
            api_key_id=user_key.id,
            uid=uid,
            server=server_name,
            likes_given=data.get('LikesGivenByAPI', 0),
            likes_before=data.get('LikesbeforeCommand', 0),
            likes_after=data.get('LikesafterCommand', 0),
            player_name=data.get('PlayerNickname', 'Unknown'),
            status='Success',
            response_data=json.dumps(data)
        )
        
        if data.get('LikesGivenByAPI', 0) >= user_key.min_like_usage:
            user_key.use_credit()
        
        db.session.add(log)
        db.session.commit()
        
        return jsonify(data)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ======================== ADMIN PANEL ========================

@app.route('/admin/panel')
def admin_panel():
    if not session.get('admin_logged_in'):
        flash('Please login first!', 'danger')
        return redirect(url_for('login'))
    
    api_keys = APIKey.query.all()
    main_apis = MainAPISetting.query.all()
    admin_setting = AdminSetting.query.first()
    
    return render_template('admin.html', 
                         api_keys=api_keys, 
                         main_apis=main_apis,
                         admin_setting=admin_setting)

@app.route('/admin/create_key', methods=['POST'])
def create_api_key():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    name = request.form.get('name')
    like_limit = int(request.form.get('like_limit', 0))
    daily_limit = int(request.form.get('daily_limit', 10))
    validity_days = int(request.form.get('validity_days', 30))
    min_like_usage = int(request.form.get('min_like_usage', 1))
    
    new_key = APIKey(
        name=name,
        like_limit=like_limit,
        daily_limit=daily_limit,
        validity_days=validity_days,
        min_like_usage=min_like_usage,
        expires_at=datetime.utcnow() + timedelta(days=validity_days)
    )
    new_key.generate_key()
    
    db.session.add(new_key)
    db.session.commit()
    
    flash(f'✅ Key created: {new_key.key}', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/toggle_key/<int:key_id>')
def toggle_key(key_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    key = APIKey.query.get(key_id)
    if key:
        key.is_active = not key.is_active
        if not key.is_active:
            key.is_auto_deactivated = True
            key.deactivated_at = datetime.utcnow()
        db.session.commit()
        flash('✅ Key status updated!', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/renew_key/<int:key_id>', methods=['POST'])
def renew_key(key_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    key = APIKey.query.get(key_id)
    if key:
        extra_likes = int(request.form.get('extra_likes', 0))
        if extra_likes > 0:
            key.renew(extra_likes)
            flash(f'✅ Renewed! Added {extra_likes} credits', 'success')
        else:
            flash('❌ Please enter valid amount!', 'danger')
    return redirect(url_for('admin_panel'))

@app.route('/admin/delete_key/<int:key_id>')
def delete_key(key_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    key = APIKey.query.get(key_id)
    if key:
        db.session.delete(key)
        db.session.commit()
        flash('✅ Key deleted!', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/update_main_api', methods=['POST'])
def update_main_api():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    url = request.form.get('url')
    use_api_key = request.form.get('use_api_key') == 'on'
    api_key = request.form.get('api_key', '')
    
    MainAPISetting.query.update({MainAPISetting.is_active: False})
    db.session.commit()
    
    api = MainAPISetting(
        name='Main API',
        url=url,
        use_api_key=use_api_key,
        api_key=api_key if use_api_key else '',
        is_active=True
    )
    db.session.add(api)
    db.session.commit()
    flash('✅ Main API updated!', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/update_admin_key', methods=['POST'])
def update_admin_key():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    new_key = request.form.get('new_admin_key')
    
    admin_setting = AdminSetting.query.first()
    if not admin_setting:
        admin_setting = AdminSetting(admin_key='JLS-ADMIN123456789')
        db.session.add(admin_setting)
    
    admin_setting.admin_key = new_key
    admin_setting.updated_at = datetime.utcnow()
    db.session.commit()
    flash('✅ Admin key updated!', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/toggle_maintenance', methods=['POST'])
def toggle_maintenance():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    admin_setting = AdminSetting.query.first()
    if not admin_setting:
        admin_setting = AdminSetting(admin_key='JLS-ADMIN123456789')
        db.session.add(admin_setting)
    
    admin_setting.maintenance_mode = not admin_setting.maintenance_mode
    db.session.commit()
    status = 'ON' if admin_setting.maintenance_mode else 'OFF'
    flash(f'✅ Maintenance mode: {status}', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/remove_main_api/<int:api_id>')
def remove_main_api(api_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    api = MainAPISetting.query.get(api_id)
    if api:
        db.session.delete(api)
        db.session.commit()
        flash('✅ Main API removed!', 'success')
    return redirect(url_for('admin_panel'))

# ======================== SETUP ========================

@app.route('/setup')
def setup():
    admin_setting = AdminSetting.query.first()
    if not admin_setting:
        admin_setting = AdminSetting(admin_key='JLS-ADMIN123456789')
        db.session.add(admin_setting)
        db.session.commit()
    
    main_api = MainAPISetting.query.first()
    if not main_api:
        main_api = MainAPISetting(
            name='Main API',
            url='https://like-api-jubayer.vercel.app/like',
            use_api_key=False,
            api_key='',
            is_active=True
        )
        db.session.add(main_api)
        db.session.commit()
    
    return '''
    <h1>✅ Setup Complete!</h1>
    <p><strong>Admin Key:</strong> JLS-ADMIN123456789</p>
    <p><strong>Main API:</strong> https://like-api-jubayer.vercel.app/like</p>
    <p><a href="/">Home</a> | <a href="/login">Login</a> | <a href="/admin/panel">Admin</a></p>
    '''

# ======================== RUN APP ========================

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)