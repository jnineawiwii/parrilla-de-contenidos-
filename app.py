# app.py - Aplicación principal Flask para el Sistema de Gestión de Contenidos RTP

import os
import json
import io
import csv
import logging
import re
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, Response
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import func, extract, and_, or_, text

load_dotenv()

# Configuración de la aplicación
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-rtp-2025')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://postgres:janine123@localhost:5433/rtp_parrilla')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

# Crear directorio de uploads si no existe
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ======================== CONFIGURACIÓN DE GEMINI ========================

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')

def generate_with_gemini(prompt):
    """Genera texto usando la API de Gemini directamente con requests"""
    if not GEMINI_API_KEY:
        return None
    
    # Lista de modelos a probar (en orden de preferencia)
    models = ['gemini-1.5-pro', 'gemini-pro', 'gemini-1.0-pro']
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key={GEMINI_API_KEY}"
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 600
            }
        }
        
        try:
            response = requests.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                data = response.json()
                result = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                if result:
                    return result
            elif response.status_code == 404:
                # El modelo no existe, probar el siguiente
                continue
            else:
                logger.warning(f"Error en Gemini ({model}): {response.status_code}")
        except Exception as e:
            logger.warning(f"Error con modelo {model}: {e}")
            continue
    
    return None

# ======================== INICIALIZAR EXTENSIONES ========================

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Por favor, inicia sesión para acceder a esta página.'
login_manager.login_message_category = 'warning'

# ======================== MODELOS DE BASE DE DATOS ========================

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='consultor')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)

    # Relaciones
    publications_responsible = db.relationship('Publication', foreign_keys='Publication.responsible_user_id', backref='responsible_user')
    publications_production = db.relationship('Publication', foreign_keys='Publication.production_user_id', backref='production_user')
    publications_postproduction = db.relationship('Publication', foreign_keys='Publication.postproduction_user_id', backref='postproduction_user')
    notifications = db.relationship('Notification', backref='user', lazy=True)
    sent_messages = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy=True)
    received_messages = db.relationship('Message', foreign_keys='Message.receiver_id', backref='receiver', lazy=True)

    def get_id(self):
        return str(self.user_id)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'

    def is_editor(self):
        return self.role == 'editor'

    def is_consultor(self):
        return self.role == 'consultor'

    def can_manage_users(self):
        return self.is_admin()

    def can_view_analytics(self):
        return self.is_admin()

    def can_access_messages(self):
        return self.is_admin() or self.is_editor() or self.is_consultor()

    def can_access_notifications(self):
        return self.is_admin() or self.is_editor()

    def can_edit_publications(self):
        return self.is_admin() or self.is_editor()

    def can_view_campaigns(self):
        return self.is_admin()

    def can_edit(self):
        return self.can_edit_publications()

    def is_supervisor(self):
        return self.is_admin()

    def __repr__(self):
        return f'<User {self.username}>'


class StaffRole(db.Model):
    __tablename__ = 'staff_roles'
    
    staff_role_id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.Text)
    
    def __repr__(self):
        return f'<StaffRole {self.role_name}>'


class Campaign(db.Model):
    __tablename__ = 'campaigns'
    
    campaign_id = db.Column(db.Integer, primary_key=True)
    campaign_name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    objective = db.Column(db.Text)
    target_audience = db.Column(db.Text)
    government_program = db.Column(db.String(200))
    government_axis = db.Column(db.String(200))
    color = db.Column(db.String(7), default='#28a745')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    themes = db.relationship('Theme', backref='campaign', lazy=True, cascade='all, delete-orphan')
    publications = db.relationship('Publication', backref='campaign', lazy=True)
    static_campaigns = db.relationship('StaticCampaign', backref='campaign', lazy=True)
    
    def __repr__(self):
        return f'<Campaign {self.campaign_name}>'


class Theme(db.Model):
    __tablename__ = 'themes'
    
    theme_id = db.Column(db.Integer, primary_key=True)
    theme_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.campaign_id', ondelete='CASCADE'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    subthemes = db.relationship('Subtheme', backref='theme', lazy=True, cascade='all, delete-orphan')
    publications = db.relationship('Publication', backref='theme', lazy=True)
    
    def __repr__(self):
        return f'<Theme {self.theme_name}>'


class Subtheme(db.Model):
    __tablename__ = 'subthemes'
    
    subtheme_id = db.Column(db.Integer, primary_key=True)
    subtheme_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    theme_id = db.Column(db.Integer, db.ForeignKey('themes.theme_id', ondelete='CASCADE'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    publications = db.relationship('Publication', backref='subtheme', lazy=True)
    
    def __repr__(self):
        return f'<Subtheme {self.subtheme_name}>'


class ContentFormat(db.Model):
    __tablename__ = 'content_formats'
    
    format_id = db.Column(db.Integer, primary_key=True)
    format_name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.Text)
    icon = db.Column(db.String(50))
    
    publications = db.relationship('Publication', backref='content_format', lazy=True)
    
    def __repr__(self):
        return f'<ContentFormat {self.format_name}>'


class Platform(db.Model):
    __tablename__ = 'platforms'
    
    platform_id = db.Column(db.Integer, primary_key=True)
    platform_name = db.Column(db.String(20), unique=True, nullable=False)
    icon = db.Column(db.String(50))
    color = db.Column(db.String(7), default='#6c757d')
    is_active = db.Column(db.Boolean, default=True)
    
    publication_platforms = db.relationship('PublicationPlatform', backref='platform', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Platform {self.platform_name}>'


class Publication(db.Model):
    __tablename__ = 'publications'
    
    publication_id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    copy_text = db.Column(db.Text)
    publication_date = db.Column(db.DateTime, nullable=False)
    day_of_week = db.Column(db.String(20))
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.campaign_id'))
    theme_id = db.Column(db.Integer, db.ForeignKey('themes.theme_id'))
    subtheme_id = db.Column(db.Integer, db.ForeignKey('subthemes.subtheme_id'))
    content_format_id = db.Column(db.Integer, db.ForeignKey('content_formats.format_id'))
    responsible_user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'))
    production_user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'))
    postproduction_user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'))
    status = db.Column(db.String(20), default='Draft')
    notes = db.Column(db.Text)
    file_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    published_at = db.Column(db.DateTime)
    
    publication_platforms = db.relationship('PublicationPlatform', backref='publication', lazy=True, cascade='all, delete-orphan')
    notifications = db.relationship('Notification', backref='publication', lazy=True, cascade='all, delete-orphan')
    
    def get_platforms(self):
        return [pp.platform for pp in self.publication_platforms]
    
    def get_total_interaction(self):
        total = 0
        for pp in self.publication_platforms:
            total += (pp.fb_interaction or 0) + (pp.ig_interaction or 0) + (pp.tw_interaction or 0) + (pp.yt_interaction or 0)
        return total
    
    def get_total_reach(self):
        total = 0
        for pp in self.publication_platforms:
            total += (pp.fb_reach or 0) + (pp.ig_reach or 0) + (pp.tw_impressions or 0)
        return total
    
    def __repr__(self):
        return f'<Publication {self.title[:50]}>'


class PublicationPlatform(db.Model):
    __tablename__ = 'publication_platforms'
    
    publication_platform_id = db.Column(db.Integer, primary_key=True)
    publication_id = db.Column(db.Integer, db.ForeignKey('publications.publication_id', ondelete='CASCADE'))
    platform_id = db.Column(db.Integer, db.ForeignKey('platforms.platform_id', ondelete='CASCADE'))
    link = db.Column(db.Text)
    status = db.Column(db.String(20), default='Pendiente')
    scheduled_time = db.Column(db.DateTime)
    published_time = db.Column(db.DateTime)
    
    tw_impressions = db.Column(db.Integer, default=0)
    tw_likes = db.Column(db.Integer, default=0)
    tw_responses = db.Column(db.Integer, default=0)
    tw_retweets = db.Column(db.Integer, default=0)
    tw_interaction = db.Column(db.Integer, default=0)
    tw_video_views = db.Column(db.Integer, default=0)
    
    ig_reach = db.Column(db.Integer, default=0)
    ig_hearts = db.Column(db.Integer, default=0)
    ig_comments = db.Column(db.Integer, default=0)
    ig_shares = db.Column(db.Integer, default=0)
    ig_interaction = db.Column(db.Integer, default=0)
    ig_video_views = db.Column(db.Integer, default=0)
    
    fb_reach = db.Column(db.Integer, default=0)
    fb_likes = db.Column(db.Integer, default=0)
    fb_comments = db.Column(db.Integer, default=0)
    fb_shares = db.Column(db.Integer, default=0)
    fb_interaction = db.Column(db.Integer, default=0)
    fb_video_views = db.Column(db.Integer, default=0)
    
    yt_views = db.Column(db.Integer, default=0)
    yt_likes = db.Column(db.Integer, default=0)
    yt_comments = db.Column(db.Integer, default=0)
    yt_shares = db.Column(db.Integer, default=0)
    yt_interaction = db.Column(db.Integer, default=0)
    yt_watch_time = db.Column(db.Integer, default=0)
    
    def __repr__(self):
        return f'<PublicationPlatform {self.publication_id}-{self.platform_id}>'


class Notification(db.Model):
    __tablename__ = 'notifications'
    
    notification_id = db.Column(db.Integer, primary_key=True)
    publication_id = db.Column(db.Integer, db.ForeignKey('publications.publication_id', ondelete='CASCADE'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))
    message = db.Column(db.Text, nullable=False)
    notification_type = db.Column(db.String(50), default='deadline')
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Notification {self.notification_id}>'


class Message(db.Model):
    __tablename__ = 'messages'
    
    message_id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.user_id', ondelete='CASCADE'))
    subject = db.Column(db.String(200))
    content = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    parent_message_id = db.Column(db.Integer, db.ForeignKey('messages.message_id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    replies = db.relationship('Message', backref=db.backref('parent', remote_side=[message_id]))
    
    def __repr__(self):
        return f'<Message {self.message_id}: {self.subject[:30]}>'


class StaticCampaign(db.Model):
    __tablename__ = 'static_campaigns'
    
    static_campaign_id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.campaign_id', ondelete='CASCADE'))
    recurrence_type = db.Column(db.String(20))
    day_of_month = db.Column(db.Integer)
    day_of_week = db.Column(db.String(20))
    time_of_day = db.Column(db.Time)
    status = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<StaticCampaign {self.static_campaign_id}>'


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    
    log_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'))
    action = db.Column(db.String(100), nullable=False)
    table_name = db.Column(db.String(50))
    record_id = db.Column(db.Integer)
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<AuditLog {self.action}>'


# ======================== FUNCIONES DE CARGA DE USUARIO ========================
@app.route('/settings')
@login_required
def settings():
    """Página de configuración y accesibilidad"""
    return render_template('settings.html', now=datetime.now())

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ======================== DECORADORES PERSONALIZADOS ========================
# ======================== DECORADORES PERSONALIZADOS ========================

def admin_required(f):
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('Acceso denegado. Se requieren permisos de administrador.', 'danger')
            return redirect(url_for('publications'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function


def editor_required(f):
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_edit_publications():
            flash('Acceso denegado. Se requieren permisos de editor.', 'danger')
            return redirect(url_for('publications'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function


def consultor_required(f):
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_consultor():
            flash('Acceso denegado. Se requieren permisos de consultor.', 'danger')
            return redirect(url_for('publications'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function


def editor_or_admin_required(f):
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_edit_publications():
            flash('Acceso denegado. Se requieren permisos de editor o administrador.', 'danger')
            return redirect(url_for('publications'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function


def supervisor_required(f):
    return admin_required(f)


# ======================== GENERACIÓN DE COPY CON GEMINI ========================

@app.route('/api/generate_copy', methods=['POST'])
@login_required
def generate_copy():
    """Genera un copy usando Gemini AI a partir de una descripción"""
    try:
        if not GEMINI_API_KEY:
            return jsonify({'success': False, 'message': 'API Key de Gemini no configurada'}), 400
        
        data = request.get_json()
        description = data.get('description', '').strip()
        campaign = data.get('campaign', '')
        tone = data.get('tone', 'profesional y cercano')
        
        if not description:
            return jsonify({'success': False, 'message': 'La descripción es obligatoria'}), 400
        
        # Construir el prompt para Gemini
        prompt = f"""
        Eres un redactor especializado en contenido para redes sociales de la Red de Transporte de Pasajeros (RTP) de la Ciudad de México.
        
        Debes generar un copy (texto de publicación) para redes sociales basado en la siguiente descripción:
        
        DESCRIPCIÓN: {description}
        
        CAMPAÑA: {campaign if campaign else 'General'}
        
        TONO: {tone}
        
        REGLAS:
        1. El copy debe ser atractivo, conciso y en español de México
        2. Debe incluir hashtags relevantes (#RTP, #MovilidadCDMX, etc.)
        3. Longitud sugerida: 80-200 caracteres para Twitter/X, 150-300 para Facebook/Instagram
        4. Si menciona una fecha o evento, incluirla de manera destacada
        5. No incluir información falsa o no verificada
        6. Mantener un tono positivo y de servicio público
        
        Genera 3 versiones diferentes del copy:
        - Versión 1: Corta y directa (para Twitter/X)
        - Versión 2: Más descriptiva (para Facebook/Instagram)
        - Versión 3: Creativa y con emojis (para Instagram/Reels)
        
        Formato de respuesta:
        VERSIÓN 1 (Twitter):
        [copy]
        
        VERSIÓN 2 (Facebook/Instagram):
        [copy]
        
        VERSIÓN 3 (Instagram/Reels):
        [copy]
        
        SOLO DEVUELVE LOS COPYS, SIN COMENTARIOS ADICIONALES.
        """
        
        # Generar el copy con Gemini
        generated_text = generate_with_gemini(prompt)
        
        if not generated_text:
            return jsonify({'success': False, 'message': 'No se pudo generar el copy. Verifica tu API Key.'}), 500
        
        # Parsear las versiones
        versions = {
            'version1': '',
            'version2': '',
            'version3': ''
        }
        
        # Extraer las versiones del texto generado
        parts = re.split(r'VERSIÓN \d+ \((?:Twitter|Facebook\/Instagram|Instagram\/Reels)\):', generated_text)
        if len(parts) >= 4:
            versions['version1'] = parts[1].strip()
            versions['version2'] = parts[2].strip()
            versions['version3'] = parts[3].strip()
        else:
            versions['version1'] = generated_text.strip()
            versions['version2'] = generated_text.strip()
            versions['version3'] = generated_text.strip()
        
        return jsonify({
            'success': True,
            'copys': versions
        })
        
    except Exception as e:
        logger.error(f"Error al generar copy: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/generate_copy_simple', methods=['POST'])
@login_required
def generate_copy_simple():
    """Genera un copy simple con Gemini"""
    try:
        if not GEMINI_API_KEY:
            return jsonify({'success': False, 'message': 'API Key no configurada'}), 400
        
        data = request.get_json()
        description = data.get('description', '').strip()
        
        if not description:
            return jsonify({'success': False, 'message': 'La descripción es obligatoria'}), 400
        
        prompt = f"""
        Genera un copy profesional para redes sociales de RTP (Red de Transporte de Pasajeros CDMX) basado en:
        
        DESCRIPCIÓN: {description}
        
        Requisitos:
        - En español de México
        - Incluir hashtags (#RTP, #MovilidadCDMX)
        - Tono: Servicio público, positivo, cercano
        - Longitud: 100-250 caracteres
        - Incluir llamado a la acción (CTA)
        
        SOLO DEVUELVE EL COPY, SIN COMENTARIOS.
        """
        
        generated_text = generate_with_gemini(prompt)
        
        if not generated_text:
            return jsonify({'success': False, 'message': 'No se pudo generar el copy.'}), 500
        
        return jsonify({
            'success': True,
            'copy': generated_text.strip()
        })
        
    except Exception as e:
        logger.error(f"Error al generar copy: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


# ======================== RUTAS PRINCIPALES ========================

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.is_admin():
            return redirect(url_for('dashboard'))
        return redirect(url_for('publications'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember', False)
        
        if not username or not password:
            flash('Por favor, ingresa usuario y contraseña.', 'warning')
            return render_template('login.html')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user, remember=bool(remember))
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            if user.is_admin():
                return redirect(url_for('dashboard'))
            return redirect(url_for('publications'))
        else:
            flash('Usuario o contraseña incorrectos.', 'danger')
    
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Has cerrado sesión correctamente.', 'info')
    return redirect(url_for('login'))


# ======================== DASHBOARD ========================

@app.route('/dashboard')
@login_required
@admin_required
def dashboard():
    try:
        total_publications = Publication.query.count() or 0
        published_count = Publication.query.filter_by(status='Published').count() or 0
        draft_count = Publication.query.filter_by(status='Draft').count() or 0
        in_review_count = Publication.query.filter_by(status='In_Review').count() or 0
        approved_count = Publication.query.filter_by(status='Approved').count() or 0
        
        now = datetime.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_publications = Publication.query.filter(
            Publication.publication_date >= start_of_month
        ).count() or 0
        
        start_of_week = now - timedelta(days=now.weekday())
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        week_publications = Publication.query.filter(
            Publication.publication_date >= start_of_week
        ).count() or 0
        
        deadline_soon = Publication.query.filter(
            Publication.publication_date <= now + timedelta(days=3),
            Publication.publication_date >= now,
            Publication.status.in_(['Draft', 'In_Review'])
        ).count() or 0
        
        total_campaigns = Campaign.query.filter_by(is_active=True).count() or 0
        
        thirty_days_ago = now - timedelta(days=30)
        campaign_stats = db.session.query(
            Campaign.campaign_name,
            func.count(Publication.publication_id).label('count')
        ).join(Publication, Publication.campaign_id == Campaign.campaign_id)\
         .filter(Publication.publication_date >= thirty_days_ago)\
         .group_by(Campaign.campaign_name)\
         .order_by(func.count(Publication.publication_id).desc())\
         .limit(6).all()
        
        recent_publications = Publication.query.order_by(
            Publication.created_at.desc()
        ).limit(10).all() or []
        
        unread_notifications = Notification.query.filter_by(
            user_id=current_user.user_id,
            is_read=False
        ).order_by(Notification.created_at.desc()).limit(5).all() or []
        
        unread_messages = Message.query.filter_by(
            receiver_id=current_user.user_id,
            is_read=False
        ).count() or 0
        
        day_stats = db.session.query(
            Publication.day_of_week,
            func.count(Publication.publication_id).label('count')
        ).filter(Publication.publication_date >= thirty_days_ago)\
         .group_by(Publication.day_of_week)\
         .order_by(Publication.day_of_week).all()
        
        days_order = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
        day_count_map = {day: 0 for day in days_order}
        
        for stat in day_stats:
            day_name = stat[0]
            if day_name and day_name in day_count_map:
                day_count_map[day_name] = stat[1] or 0
        
        day_labels = list(day_count_map.keys())
        day_values = list(day_count_map.values())
        
        campaign_data = []
        for campaign in campaign_stats:
            campaign_data.append([campaign[0] or 'Sin campaña', campaign[1] or 0])
        
        return render_template('dashboard.html',
                               total_publications=total_publications,
                               published_count=published_count,
                               draft_count=draft_count,
                               in_review_count=in_review_count,
                               approved_count=approved_count,
                               month_publications=month_publications,
                               week_publications=week_publications,
                               deadline_soon=deadline_soon,
                               total_campaigns=total_campaigns,
                               campaign_stats=campaign_data,
                               recent_publications=recent_publications,
                               unread_notifications=unread_notifications,
                               unread_messages=unread_messages,
                               day_labels=day_labels,
                               day_values=day_values,
                               now=now)
    except Exception as e:
        logger.error(f"Error en dashboard: {str(e)}")
        flash('Error al cargar el dashboard.', 'danger')
        return render_template('dashboard.html',
                               total_publications=0,
                               published_count=0,
                               draft_count=0,
                               in_review_count=0,
                               approved_count=0,
                               month_publications=0,
                               week_publications=0,
                               deadline_soon=0,
                               total_campaigns=0,
                               campaign_stats=[],
                               recent_publications=[],
                               unread_notifications=[],
                               unread_messages=0,
                               day_labels=['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'],
                               day_values=[0, 0, 0, 0, 0, 0, 0],
                               now=datetime.now())


# ======================== GESTIÓN DE PUBLICACIONES ========================

@app.route('/publications')
@login_required
def publications():
    if current_user.is_consultor():
        flash('Tu rol solo permite ver la parrilla.', 'info')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    month = request.args.get('month')
    campaign_id = request.args.get('campaign_id', type=int)
    status = request.args.get('status')
    search = request.args.get('search', '')
    
    query = Publication.query
    
    if month:
        try:
            year, month_num = map(int, month.split('-'))
            query = query.filter(
                extract('year', Publication.publication_date) == year,
                extract('month', Publication.publication_date) == month_num
            )
        except:
            pass
    
    if campaign_id:
        query = query.filter_by(campaign_id=campaign_id)
    
    if status:
        query = query.filter_by(status=status)
    
    if search:
        search_term = f'%{search}%'
        query = query.filter(
            or_(
                Publication.title.ilike(search_term),
                Publication.description.ilike(search_term),
                Publication.copy_text.ilike(search_term)
            )
        )
    
    query = query.order_by(Publication.publication_date.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    campaigns = Campaign.query.filter_by(is_active=True).order_by(Campaign.campaign_name).all()
    statuses = ['Draft', 'In_Review', 'Approved', 'Published', 'Cancelled']
    
    return render_template('publications.html',
                           publications=pagination.items,
                           pagination=pagination,
                           campaigns=campaigns,
                           statuses=statuses,
                           current_campaign=campaign_id,
                           current_status=status,
                           current_month=month,
                           current_search=search)


@app.route('/publication/new', methods=['GET', 'POST'])
@login_required
def new_publication():
    if not current_user.can_edit_publications():
        flash('No tienes permiso para crear publicaciones.', 'danger')
        return redirect(url_for('publications'))
    if request.method == 'POST':
        return save_publication()
    
    campaigns = Campaign.query.filter_by(is_active=True).order_by(Campaign.campaign_name).all()
    themes = Theme.query.all()
    subthemes = Subtheme.query.all()
    formats = ContentFormat.query.all()
    platforms = Platform.query.filter_by(is_active=True).all()
    users = User.query.filter_by(is_active=True).order_by(User.full_name).all()
    
    return render_template('publication_form.html',
                           publication=None,
                           campaigns=campaigns,
                           themes=themes,
                           subthemes=subthemes,
                           formats=formats,
                           platforms=platforms,
                           users=users,
                           is_edit=False)


@app.route('/publication/<int:pub_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_publication(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    
    if not current_user.can_edit_publications() and current_user.user_id != publication.responsible_user_id:
        flash('No tienes permiso para editar esta publicación.', 'danger')
        return redirect(url_for('publications'))
    
    if request.method == 'POST':
        return save_publication(pub_id)
    
    campaigns = Campaign.query.filter_by(is_active=True).order_by(Campaign.campaign_name).all()
    themes = Theme.query.all()
    subthemes = Subtheme.query.all()
    formats = ContentFormat.query.all()
    platforms = Platform.query.filter_by(is_active=True).all()
    users = User.query.filter_by(is_active=True).order_by(User.full_name).all()
    
    selected_platforms = [pp.platform_id for pp in publication.publication_platforms]
    
    return render_template('publication_form.html',
                           publication=publication,
                           campaigns=campaigns,
                           themes=themes,
                           subthemes=subthemes,
                           formats=formats,
                           platforms=platforms,
                           users=users,
                           selected_platforms=selected_platforms,
                           is_edit=True)


def save_publication(pub_id=None):
    try:
        if not current_user.can_edit_publications():
            flash('No tienes permiso para guardar publicaciones.', 'danger')
            return redirect(url_for('publications'))
        if pub_id:
            publication = Publication.query.get_or_404(pub_id)
        else:
            publication = Publication()
        
        title = request.form.get('title', '').strip()
        if not title:
            flash('El título es obligatorio.', 'danger')
            return redirect(request.referrer or url_for('publications'))
        
        publication.title = title
        publication.description = request.form.get('description', '').strip()
        publication.copy_text = request.form.get('copy_text', '').strip()
        publication.notes = request.form.get('notes', '').strip()
        
        date_str = request.form.get('publication_date')
        if date_str:
            try:
                pub_date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M')
                publication.publication_date = pub_date
                days = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
                publication.day_of_week = days[pub_date.weekday()]
            except ValueError:
                flash('Formato de fecha inválido.', 'danger')
                return redirect(request.referrer or url_for('publications'))
        else:
            flash('La fecha de publicación es obligatoria.', 'danger')
            return redirect(request.referrer or url_for('publications'))
        
        publication.campaign_id = request.form.get('campaign_id', type=int) or None
        publication.theme_id = request.form.get('theme_id', type=int) or None
        publication.subtheme_id = request.form.get('subtheme_id', type=int) or None
        publication.content_format_id = request.form.get('content_format_id', type=int) or None
        publication.responsible_user_id = request.form.get('responsible_user_id', type=int) or None
        publication.production_user_id = request.form.get('production_user_id', type=int) or None
        publication.postproduction_user_id = request.form.get('postproduction_user_id', type=int) or None
        
        new_status = request.form.get('status', 'Draft')
        if new_status != publication.status and new_status == 'Published':
            publication.published_at = datetime.utcnow()
        publication.status = new_status
        
        if 'file_attachment' in request.files:
            file = request.files['file_attachment']
            if file and file.filename:
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                unique_filename = f"{timestamp}_{filename}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(file_path)
                publication.file_path = f"uploads/{unique_filename}"
        
        if not pub_id:
            db.session.add(publication)
            db.session.flush()
        
        platform_ids = request.form.getlist('platforms')
        if platform_ids:
            if pub_id:
                PublicationPlatform.query.filter_by(publication_id=publication.publication_id).delete()
            
            for platform_id in platform_ids:
                platform = Platform.query.get(platform_id)
                if platform:
                    pp = PublicationPlatform(
                        publication_id=publication.publication_id,
                        platform_id=platform_id,
                        status='Pendiente'
                    )
                    db.session.add(pp)
        
        db.session.commit()
        
        flash(f'Publicación {"creada" if not pub_id else "actualizada"} con éxito.', 'success')
        
        if request.form.get('save_and_publish'):
            return redirect(url_for('publication_platforms', pub_id=publication.publication_id))
        return redirect(url_for('publications'))
        
    except Exception as e:
        logger.error(f"Error al guardar publicación: {str(e)}")
        db.session.rollback()
        flash(f'Error al guardar la publicación: {str(e)}', 'danger')
        return redirect(request.referrer or url_for('publications'))


@app.route('/publication/<int:pub_id>/delete', methods=['POST'])
@login_required
def delete_publication(pub_id):
    if not current_user.can_edit_publications():
        return jsonify({'success': False, 'message': 'No tienes permisos para eliminar publicaciones.'}), 403
    
    publication = Publication.query.get_or_404(pub_id)
    
    try:
        db.session.delete(publication)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Publicación eliminada con éxito.'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error al eliminar publicación: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500


@app.route('/publication/<int:pub_id>/duplicate', methods=['POST'])
@login_required
def duplicate_publication(pub_id):
    if not current_user.can_edit_publications():
        return jsonify({'success': False, 'message': 'No tienes permisos para duplicar publicaciones.'}), 403
    original = Publication.query.get_or_404(pub_id)
    
    try:
        new_pub = Publication(
            title=f"{original.title} (Copia)",
            description=original.description,
            copy_text=original.copy_text,
            publication_date=original.publication_date + timedelta(days=7),
            campaign_id=original.campaign_id,
            theme_id=original.theme_id,
            subtheme_id=original.subtheme_id,
            content_format_id=original.content_format_id,
            status='Draft'
        )
        db.session.add(new_pub)
        db.session.flush()
        
        for pp in original.publication_platforms:
            new_pp = PublicationPlatform(
                publication_id=new_pub.publication_id,
                platform_id=pp.platform_id,
                status='Pendiente'
            )
            db.session.add(new_pp)
        
        db.session.commit()
        
        flash('Publicación duplicada con éxito.', 'success')
        return jsonify({'success': True, 'new_id': new_pub.publication_id})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error al duplicar publicación: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500


# ======================== GESTIÓN DE PLATAFORMAS Y ESTADÍSTICAS ========================

@app.route('/publication/<int:pub_id>/platforms', methods=['GET', 'POST'])
@login_required
def publication_platforms(pub_id):
    publication = Publication.query.get_or_404(pub_id)
    
    if request.method == 'POST':
        try:
            platform_id = request.form.get('platform_id', type=int)
            if platform_id:
                pp = PublicationPlatform.query.filter_by(
                    publication_id=pub_id,
                    platform_id=platform_id
                ).first()
                
                if pp:
                    pp.link = request.form.get('link', '').strip()
                    pp.status = request.form.get('status', 'Publicado')
                    if pp.status == 'Publicado' and not pp.published_time:
                        pp.published_time = datetime.utcnow()
                    
                    platform = Platform.query.get(platform_id)
                    if platform:
                        if platform.platform_name == 'Twitter':
                            pp.tw_impressions = request.form.get('tw_impressions', 0, type=int)
                            pp.tw_likes = request.form.get('tw_likes', 0, type=int)
                            pp.tw_responses = request.form.get('tw_responses', 0, type=int)
                            pp.tw_retweets = request.form.get('tw_retweets', 0, type=int)
                            pp.tw_interaction = request.form.get('tw_interaction', 0, type=int)
                        elif platform.platform_name == 'Instagram':
                            pp.ig_reach = request.form.get('ig_reach', 0, type=int)
                            pp.ig_hearts = request.form.get('ig_hearts', 0, type=int)
                            pp.ig_comments = request.form.get('ig_comments', 0, type=int)
                            pp.ig_shares = request.form.get('ig_shares', 0, type=int)
                            pp.ig_interaction = request.form.get('ig_interaction', 0, type=int)
                        elif platform.platform_name == 'Facebook':
                            pp.fb_reach = request.form.get('fb_reach', 0, type=int)
                            pp.fb_likes = request.form.get('fb_likes', 0, type=int)
                            pp.fb_comments = request.form.get('fb_comments', 0, type=int)
                            pp.fb_shares = request.form.get('fb_shares', 0, type=int)
                            pp.fb_interaction = request.form.get('fb_interaction', 0, type=int)
                        elif platform.platform_name == 'YouTube':
                            pp.yt_views = request.form.get('yt_views', 0, type=int)
                            pp.yt_likes = request.form.get('yt_likes', 0, type=int)
                            pp.yt_comments = request.form.get('yt_comments', 0, type=int)
                            pp.yt_shares = request.form.get('yt_shares', 0, type=int)
                            pp.yt_interaction = request.form.get('yt_interaction', 0, type=int)
                    
                    db.session.commit()
                    flash('Estadísticas actualizadas con éxito.', 'success')
            
            return redirect(url_for('publication_platforms', pub_id=pub_id))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al actualizar estadísticas: {str(e)}")
            flash(f'Error al actualizar estadísticas: {str(e)}', 'danger')
    
    platforms_with_stats = []
    for pp in publication.publication_platforms:
        platform = Platform.query.get(pp.platform_id)
        if platform:
            platforms_with_stats.append({
                'platform': platform,
                'data': pp
            })
    
    return render_template('publication_platforms.html',
                           publication=publication,
                           platforms_with_stats=platforms_with_stats)


# ======================== GESTIÓN DE CAMPAÑAS ========================

@app.route('/campaigns')
@login_required
@admin_required
def campaigns():
    campaigns_list = Campaign.query.order_by(Campaign.campaign_name).all()
    return render_template('campaigns.html', campaigns=campaigns_list)


@app.route('/campaign/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_campaign():
    if request.method == 'POST':
        try:
            campaign = Campaign(
                campaign_name=request.form.get('campaign_name', '').strip(),
                description=request.form.get('description', '').strip(),
                objective=request.form.get('objective', '').strip(),
                target_audience=request.form.get('target_audience', '').strip(),
                government_program=request.form.get('government_program', '').strip(),
                government_axis=request.form.get('government_axis', '').strip(),
                color=request.form.get('color', '#28a745')
            )
            
            if not campaign.campaign_name:
                flash('El nombre de la campaña es obligatorio.', 'danger')
                return redirect(url_for('new_campaign'))
            
            db.session.add(campaign)
            db.session.commit()
            
            flash('Campaña creada con éxito.', 'success')
            return redirect(url_for('campaigns'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al crear campaña: {str(e)}")
            flash(f'Error al crear la campaña: {str(e)}', 'danger')
    
    return render_template('campaign_form.html', campaign=None)


@app.route('/campaign/<int:campaign_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    
    if request.method == 'POST':
        try:
            campaign.campaign_name = request.form.get('campaign_name', '').strip()
            campaign.description = request.form.get('description', '').strip()
            campaign.objective = request.form.get('objective', '').strip()
            campaign.target_audience = request.form.get('target_audience', '').strip()
            campaign.government_program = request.form.get('government_program', '').strip()
            campaign.government_axis = request.form.get('government_axis', '').strip()
            campaign.color = request.form.get('color', '#28a745')
            campaign.is_active = request.form.get('is_active') == 'on'
            
            if not campaign.campaign_name:
                flash('El nombre de la campaña es obligatorio.', 'danger')
                return redirect(url_for('edit_campaign', campaign_id=campaign_id))
            
            db.session.commit()
            flash('Campaña actualizada con éxito.', 'success')
            return redirect(url_for('campaigns'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al actualizar campaña: {str(e)}")
            flash(f'Error al actualizar la campaña: {str(e)}', 'danger')
    
    return render_template('campaign_form.html', campaign=campaign)


@app.route('/campaign/<int:campaign_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    
    try:
        pub_count = Publication.query.filter_by(campaign_id=campaign_id).count()
        if pub_count > 0:
            return jsonify({
                'success': False, 
                'message': f'No se puede eliminar la campaña porque tiene {pub_count} publicaciones asociadas.'
            }), 400
        
        db.session.delete(campaign)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Campaña eliminada con éxito.'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error al eliminar campaña: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500


# ======================== API PARA AJAX ========================

@app.route('/api/themes/by_campaign/<int:campaign_id>')
@login_required
def get_themes_by_campaign(campaign_id):
    themes = Theme.query.filter_by(campaign_id=campaign_id).order_by(Theme.theme_name).all()
    return jsonify([{'id': t.theme_id, 'name': t.theme_name} for t in themes])


@app.route('/api/subthemes/by_theme/<int:theme_id>')
@login_required
def get_subthemes_by_theme(theme_id):
    subthemes = Subtheme.query.filter_by(theme_id=theme_id).order_by(Subtheme.subtheme_name).all()
    return jsonify([{'id': s.subtheme_id, 'name': s.subtheme_name} for s in subthemes])


# ======================== NOTIFICACIONES ========================

@app.route('/notifications')
@login_required
def notifications():
    notifs = Notification.query.filter_by(user_id=current_user.user_id)\
        .order_by(Notification.created_at.desc()).all()
    
    for notif in notifs:
        if not notif.is_read:
            notif.is_read = True
    db.session.commit()
    
    return render_template('notifications.html', notifications=notifs)


@app.route('/notifications/mark-read/<int:notif_id>', methods=['POST'])
@login_required
def mark_notification_read(notif_id):
    if not current_user.can_access_notifications():
        return jsonify({'success': False, 'message': 'No tienes permiso.'}), 403
    notification = Notification.query.get_or_404(notif_id)
    
    if notification.user_id != current_user.user_id:
        return jsonify({'success': False, 'message': 'No tienes permiso.'}), 403
    
    notification.is_read = True
    db.session.commit()
    return jsonify({'success': True})


@app.route('/notifications/count-unread')
@login_required
def get_unread_notifications_count():
    if not current_user.can_access_notifications():
        return jsonify({'count': 0})
    count = Notification.query.filter_by(user_id=current_user.user_id, is_read=False).count()
    return jsonify({'count': count})


# ======================== MENSAJERÍA ENTRE USUARIOS ========================

@app.route('/messages')
@login_required
def messages():
    if not current_user.can_access_messages():
        flash('No tienes permiso para ver mensajes.', 'danger')
        return redirect(url_for('publications'))
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 15, type=int)
    tab = request.args.get('tab', 'inbox')
    
    unread_count = Message.query.filter_by(receiver_id=current_user.user_id, is_read=False).count()
    
    users = User.query.filter(
        User.user_id != current_user.user_id,
        User.is_active == True
    ).order_by(User.full_name).all()
    
    if tab == 'sent':
        messages_query = Message.query.filter_by(sender_id=current_user.user_id)\
            .order_by(Message.created_at.desc())
        pagination = messages_query.paginate(page=page, per_page=per_page, error_out=False)
        title = 'Mensajes Enviados'
    else:
        messages_query = Message.query.filter_by(receiver_id=current_user.user_id)\
            .order_by(Message.created_at.desc())
        pagination = messages_query.paginate(page=page, per_page=per_page, error_out=False)
        title = 'Bandeja de Entrada'
    
    return render_template('messages.html',
                           messages=pagination.items,
                           pagination=pagination,
                           unread_count=unread_count,
                           users=users,
                           current_tab=tab,
                           title=title,
                           current_user=current_user)


@app.route('/messages/send', methods=['GET', 'POST'])
@login_required
def send_message():
    if not current_user.can_access_messages():
        flash('No tienes permiso para enviar mensajes.', 'danger')
        return redirect(url_for('publications'))
    if request.method == 'POST':
        try:
            receiver_id = request.form.get('receiver_id', type=int)
            subject = request.form.get('subject', '').strip()
            content = request.form.get('content', '').strip()
            parent_id = request.form.get('parent_id', type=int) or None
            
            if not receiver_id:
                flash('Debes seleccionar un destinatario.', 'danger')
                return redirect(url_for('messages'))
            
            if not content:
                flash('El contenido del mensaje es obligatorio.', 'danger')
                return redirect(url_for('messages'))
            
            receiver = User.query.get(receiver_id)
            if not receiver:
                flash('El destinatario no existe.', 'danger')
                return redirect(url_for('messages'))
            
            message = Message(
                sender_id=current_user.user_id,
                receiver_id=receiver_id,
                subject=subject or 'Sin asunto',
                content=content,
                parent_message_id=parent_id,
                is_read=False
            )
            
            db.session.add(message)
            db.session.flush()
            
            notification = Notification(
                publication_id=None,
                user_id=receiver_id,
                message=f'Nuevo mensaje de {current_user.full_name}: {subject or "Sin asunto"}',
                notification_type='message'
            )
            db.session.add(notification)
            
            db.session.commit()
            
            flash(f'Mensaje enviado a {receiver.full_name} correctamente.', 'success')
            
            if request.form.get('from_conversation'):
                return redirect(url_for('conversation', user_id=receiver_id))
            return redirect(url_for('messages'))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al enviar mensaje: {str(e)}")
            flash(f'Error al enviar mensaje: {str(e)}', 'danger')
    
    users = User.query.filter(
        User.user_id != current_user.user_id,
        User.is_active == True
    ).order_by(User.full_name).all()
    
    return render_template('send_message.html', users=users)


@app.route('/messages/conversation/<int:user_id>')
@login_required
def conversation(user_id):
    if not current_user.can_access_messages():
        flash('No tienes permiso para ver conversaciones.', 'danger')
        return redirect(url_for('publications'))
    other_user = User.query.get_or_404(user_id)
    
    messages = Message.query.filter(
        or_(
            and_(Message.sender_id == current_user.user_id, Message.receiver_id == user_id),
            and_(Message.sender_id == user_id, Message.receiver_id == current_user.user_id)
        )
    ).order_by(Message.created_at.asc()).all()
    
    unread = Message.query.filter_by(
        receiver_id=current_user.user_id,
        sender_id=user_id,
        is_read=False
    ).all()
    
    for msg in unread:
        msg.is_read = True
    db.session.commit()
    
    users = User.query.filter(
        User.user_id != current_user.user_id,
        User.is_active == True
    ).order_by(User.full_name).all()
    
    return render_template('conversation.html',
                           other_user=other_user,
                           messages=messages,
                           users=users,
                           current_user=current_user)


@app.route('/messages/read/<int:message_id>', methods=['POST'])
@login_required
def mark_message_read(message_id):
    if not current_user.can_access_messages():
        return jsonify({'success': False, 'message': 'No tienes permiso.'}), 403
    message = Message.query.get_or_404(message_id)
    
    if message.receiver_id != current_user.user_id:
        return jsonify({'success': False, 'message': 'No tienes permiso.'}), 403
    
    message.is_read = True
    db.session.commit()
    return jsonify({'success': True})


@app.route('/messages/delete/<int:message_id>', methods=['POST'])
@login_required
def delete_message(message_id):
    if not current_user.can_access_messages():
        return jsonify({'success': False, 'message': 'No tienes permiso.'}), 403
    message = Message.query.get_or_404(message_id)
    
    if message.sender_id != current_user.user_id and message.receiver_id != current_user.user_id:
        return jsonify({'success': False, 'message': 'No tienes permiso.'}), 403
    
    try:
        db.session.delete(message)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Mensaje eliminado.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/messages/unread-count')
@login_required
def get_unread_messages_count():
    if not current_user.can_access_messages():
        return jsonify({'count': 0})
    count = Message.query.filter_by(receiver_id=current_user.user_id, is_read=False).count()
    return jsonify({'count': count})


# ======================== ANALÍTICAS Y REPORTES ========================

@app.route('/analytics')
@login_required
@admin_required
def analytics():
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    campaign_id = request.args.get('campaign_id', type=int)
    
    query = Publication.query
    
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(Publication.publication_date >= date_from_obj)
        except:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(Publication.publication_date < date_to_obj)
        except:
            pass
    
    if campaign_id:
        query = query.filter_by(campaign_id=campaign_id)
    
    query = query.filter_by(status='Published')
    
    publications = query.all()
    
    total_reach = 0
    total_interaction = 0
    platform_stats = {}
    campaign_stats = {}
    
    for pub in publications:
        total_reach += pub.get_total_reach()
        total_interaction += pub.get_total_interaction()
        
        for pp in pub.publication_platforms:
            platform = Platform.query.get(pp.platform_id)
            if platform:
                platform_name = platform.platform_name
                if platform_name not in platform_stats:
                    platform_stats[platform_name] = {'reach': 0, 'interaction': 0}
                platform_stats[platform_name]['reach'] += (pp.fb_reach or 0) + (pp.ig_reach or 0) + (pp.tw_impressions or 0)
                platform_stats[platform_name]['interaction'] += (pp.fb_interaction or 0) + (pp.ig_interaction or 0) + (pp.tw_interaction or 0) + (pp.yt_interaction or 0)
        
        if pub.campaign:
            campaign_name = pub.campaign.campaign_name
            if campaign_name not in campaign_stats:
                campaign_stats[campaign_name] = {'reach': 0, 'interaction': 0, 'count': 0}
            campaign_stats[campaign_name]['reach'] += pub.get_total_reach()
            campaign_stats[campaign_name]['interaction'] += pub.get_total_interaction()
            campaign_stats[campaign_name]['count'] += 1
    
    platform_labels = list(platform_stats.keys())
    platform_reach = [platform_stats[p]['reach'] for p in platform_labels]
    platform_interaction = [platform_stats[p]['interaction'] for p in platform_labels]
    
    campaign_labels = list(campaign_stats.keys())
    campaign_reach = [campaign_stats[c]['reach'] for c in campaign_labels]
    campaign_interaction = [campaign_stats[c]['interaction'] for c in campaign_labels]
    
    campaigns = Campaign.query.filter_by(is_active=True).all()
    
    return render_template('analytics.html',
                           publications=publications,
                           total_reach=total_reach,
                           total_interaction=total_interaction,
                           total_posts=len(publications),
                           platform_labels=platform_labels,
                           platform_reach=platform_reach,
                           platform_interaction=platform_interaction,
                           campaign_labels=campaign_labels,
                           campaign_reach=campaign_reach,
                           campaign_interaction=campaign_interaction,
                           campaigns=campaigns,
                           date_from=date_from,
                           date_to=date_to,
                           selected_campaign=campaign_id)


@app.route('/analytics/export/<int:campaign_id>')
@login_required
@supervisor_required
def export_analytics(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    publications = Publication.query.filter_by(
        campaign_id=campaign_id,
        status='Published'
    ).order_by(Publication.publication_date).all()
    
    si = io.StringIO()
    writer = csv.writer(si)
    
    writer.writerow([
        'Fecha', 'Título', 'Plataforma', 'Link', 'Alcance', 'Interacción', 'Likes', 'Comentarios', 'Compartidos'
    ])
    
    for pub in publications:
        for pp in pub.publication_platforms:
            platform = Platform.query.get(pp.platform_id)
            if platform:
                reach = 0
                interaction = 0
                likes = 0
                comments = 0
                shares = 0
                
                if platform.platform_name == 'Facebook':
                    reach = pp.fb_reach or 0
                    interaction = pp.fb_interaction or 0
                    likes = pp.fb_likes or 0
                    comments = pp.fb_comments or 0
                    shares = pp.fb_shares or 0
                elif platform.platform_name == 'Instagram':
                    reach = pp.ig_reach or 0
                    interaction = pp.ig_interaction or 0
                    likes = pp.ig_hearts or 0
                    comments = pp.ig_comments or 0
                    shares = pp.ig_shares or 0
                elif platform.platform_name == 'Twitter':
                    reach = pp.tw_impressions or 0
                    interaction = pp.tw_interaction or 0
                    likes = pp.tw_likes or 0
                    comments = pp.tw_responses or 0
                    shares = pp.tw_retweets or 0
                elif platform.platform_name == 'YouTube':
                    interaction = pp.yt_interaction or 0
                    likes = pp.yt_likes or 0
                    comments = pp.yt_comments or 0
                    shares = pp.yt_shares or 0
                
                writer.writerow([
                    pub.publication_date.strftime('%Y-%m-%d'),
                    pub.title,
                    platform.platform_name,
                    pp.link or '',
                    reach,
                    interaction,
                    likes,
                    comments,
                    shares
                ])
    
    output = si.getvalue()
    si.close()
    
    return Response(
        output,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={campaign.campaign_name}_reporte.csv'}
    )


# ======================== GESTIÓN DE USUARIOS ========================

@app.route('/users')
@login_required
@admin_required
def users():
    users_list = User.query.order_by(User.full_name).all()
    return render_template('users.html', users=users_list)


@app.route('/user/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_user():
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            full_name = request.form.get('full_name', '').strip()
            role = request.form.get('role', 'consultor')
            password = request.form.get('password', '')
            
            if not all([username, email, full_name, password]):
                flash('Todos los campos son obligatorios.', 'danger')
                return redirect(url_for('new_user'))
            
            if len(password) < 6:
                flash('La contraseña debe tener al menos 6 caracteres.', 'danger')
                return redirect(url_for('new_user'))
            
            if User.query.filter_by(username=username).first():
                flash('El nombre de usuario ya está en uso.', 'danger')
                return redirect(url_for('new_user'))
            
            if User.query.filter_by(email=email).first():
                flash('El correo electrónico ya está registrado.', 'danger')
                return redirect(url_for('new_user'))
            
            user = User(
                username=username,
                email=email,
                full_name=full_name,
                role=role
            )
            user.set_password(password)
            
            db.session.add(user)
            db.session.commit()
            
            flash('Usuario creado con éxito.', 'success')
            return redirect(url_for('users'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al crear usuario: {str(e)}")
            flash(f'Error al crear usuario: {str(e)}', 'danger')
    
    roles = ['admin', 'editor', 'consultor']
    return render_template('user_form.html', user=None, roles=roles)


@app.route('/user/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        try:
            user.username = request.form.get('username', '').strip()
            user.email = request.form.get('email', '').strip()
            user.full_name = request.form.get('full_name', '').strip()
            user.role = request.form.get('role', 'consultor')
            user.is_active = request.form.get('is_active') == 'on'
            
            new_password = request.form.get('new_password', '')
            if new_password:
                if len(new_password) < 6:
                    flash('La contraseña debe tener al menos 6 caracteres.', 'danger')
                    return redirect(url_for('edit_user', user_id=user_id))
                user.set_password(new_password)
            
            db.session.commit()
            flash('Usuario actualizado con éxito.', 'success')
            return redirect(url_for('users'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al actualizar usuario: {str(e)}")
            flash(f'Error al actualizar usuario: {str(e)}', 'danger')
    
    roles = ['admin', 'editor', 'consultor']
    return render_template('user_form.html', user=user, roles=roles)


@app.route('/user/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if user.user_id == current_user.user_id:
        return jsonify({'success': False, 'message': 'No puedes eliminar tu propio usuario.'}), 400
    
    try:
        db.session.delete(user)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Usuario eliminado con éxito.'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error al eliminar usuario: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500


# ======================== ERROR HANDLERS ========================

@app.errorhandler(404)
def not_found_error(error):
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('errors/500.html'), 500


# ======================== COMANDOS PARA INICIALIZAR DATOS ========================

@app.cli.command('init-db')
def init_db_command():
    """Inicializa la base de datos con datos básicos."""
    from sqlalchemy.exc import IntegrityError
    
    try:
        db.create_all()
        
        if User.query.first():
            print('La base de datos ya tiene datos.')
            return
        
        admin = User(
            username='admin',
            email='admin@rtp.local',
            full_name='Administrador del Sistema',
            role='admin',
            is_active=True
        )
        admin.set_password('admin123')
        db.session.add(admin)
        
        roles = [
            StaffRole(role_name='Producción', description='Crea el contenido gráfico o audiovisual.'),
            StaffRole(role_name='Postproducción', description='Edita y finaliza el contenido.'),
            StaffRole(role_name='Encargado', description='Responsable de la publicación.'),
            StaffRole(role_name='Servicio Social', description='Apoyo en la creación de contenido.')
        ]
        for role in roles:
            db.session.add(role)
        
        platforms = [
            Platform(platform_name='Facebook', icon='bi-facebook', color='#1877f2'),
            Platform(platform_name='Twitter', icon='bi-twitter', color='#000000'),
            Platform(platform_name='Instagram', icon='bi-instagram', color='#e4405f'),
            Platform(platform_name='YouTube', icon='bi-youtube', color='#ff0000'),
            Platform(platform_name='TikTok', icon='bi-music-note', color='#000000')
        ]
        for platform in platforms:
            db.session.add(platform)
        
        formats = [
            ContentFormat(format_name='Composición', icon='bi-layers'),
            ContentFormat(format_name='Fotografía', icon='bi-camera'),
            ContentFormat(format_name='Video', icon='bi-play-btn'),
            ContentFormat(format_name='Infografía', icon='bi-bar-chart'),
            ContentFormat(format_name='Ilustración', icon='bi-palette'),
            ContentFormat(format_name='Animación', icon='bi-film'),
            ContentFormat(format_name='Mapa', icon='bi-map'),
            ContentFormat(format_name='PDF', icon='bi-file-pdf'),
            ContentFormat(format_name='Boletín / Tarjeta informativa', icon='bi-newspaper'),
            ContentFormat(format_name='En vivo', icon='bi-broadcast'),
            ContentFormat(format_name='Reel', icon='bi-play-circle')
        ]
        for fmt in formats:
            db.session.add(fmt)
        
        campaigns = [
            Campaign(
                campaign_name='A_su_servicio',
                description='Brindar información oportuna del servicio como rutas, modalidades, servicios especiales y demás opciones que ofrece RTP.',
                color='#17a2b8'
            ),
            Campaign(
                campaign_name='RTP_avanza',
                description='Difundir contenido acerca de los avances tecnológicos y de innovación que hacen de RTP un Organismo de calidad y vanguardia tecnológica.',
                color='#28a745'
            ),
            Campaign(
                campaign_name='RTP_siempre_contigo',
                description='Persuadir la participación positiva de los seguidores en redes sociales, incrementando su conversación e interacción a través de contenido divertido, curioso y relevante.',
                color='#ffc107'
            ),
            Campaign(
                campaign_name='Cultura_en_el_transporte',
                description='Concientizar a las personas usuarias de RTP sobre los valores cívicos y la sensibilización de temas de carácter social.',
                color='#dc3545'
            ),
            Campaign(
                campaign_name='RTP_en_directo',
                description='Proporcionar información directa, clara y precisa para las personas usuarias de la red sobre información relevante.',
                color='#6f42c1'
            ),
            Campaign(
                campaign_name='Movilidad_Integrada_MI',
                description='Promover las acciones de gobierno del sector movilidad.',
                color='#20c997'
            )
        ]
        for campaign in campaigns:
            db.session.add(campaign)
        
        db.session.commit()
        print('Base de datos inicializada con éxito!')
        print('Usuario administrador: admin / contraseña: admin123')
        
    except IntegrityError as e:
        db.session.rollback()
        print(f'Error de integridad: {e}')
    except Exception as e:
        db.session.rollback()
        print(f'Error al inicializar la base de datos: {e}')


@app.cli.command('create-user')
def create_user_command():
    """Crear un nuevo usuario desde la línea de comandos."""
    import click
    username = click.prompt('Nombre de usuario')
    email = click.prompt('Correo electrónico')
    full_name = click.prompt('Nombre completo')
    role = click.prompt('Rol', default='consultor', type=click.Choice(['admin', 'editor', 'consultor'], case_sensitive=False))
    password = click.prompt('Contraseña', hide_input=True, confirmation_prompt=True)
    
    if User.query.filter_by(username=username).first():
        print(f'Error: El usuario "{username}" ya existe.')
        return
    
    if User.query.filter_by(email=email).first():
        print(f'Error: El email "{email}" ya está registrado.')
        return
    
    user = User(username=username, email=email, full_name=full_name, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    
    print(f'Usuario "{username}" creado con éxito.')


# ======================== VERIFICACIÓN DE BASE DE DATOS ========================

with app.app_context():
    try:
        print("=" * 50)
        print("🔍 Verificando estructura de la base de datos...")
        print("=" * 50)
        
        # Verificar columnas de users
        result = db.session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'users' 
            AND column_name IN ('last_login', 'is_active')
        """))
        existing_columns = [row[0] for row in result.fetchall()]
        
        if 'last_login' not in existing_columns:
            print("⚠️ Agregando columna last_login a users...")
            db.session.execute(text("ALTER TABLE users ADD COLUMN last_login TIMESTAMP"))
            db.session.commit()
            print("✅ Columna last_login agregada")
        else:
            print("✅ Columna last_login ya existe")
        
        if 'is_active' not in existing_columns:
            print("⚠️ Agregando columna is_active a users...")
            db.session.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
            db.session.commit()
            print("✅ Columna is_active agregada")
        else:
            print("✅ Columna is_active ya existe")
        
        # Verificar columnas de campaigns
        result = db.session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'campaigns' 
            AND column_name IN ('color', 'is_active')
        """))
        existing_columns = [row[0] for row in result.fetchall()]
        
        if 'color' not in existing_columns:
            print("⚠️ Agregando columna color a campaigns...")
            db.session.execute(text("ALTER TABLE campaigns ADD COLUMN color VARCHAR(7) DEFAULT '#28a745'"))
            db.session.commit()
            print("✅ Columna color agregada")
        else:
            print("✅ Columna color ya existe")
        
        if 'is_active' not in existing_columns:
            print("⚠️ Agregando columna is_active a campaigns...")
            db.session.execute(text("ALTER TABLE campaigns ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
            db.session.commit()
            print("✅ Columna is_active agregada")
        else:
            print("✅ Columna is_active ya existe")
        
        # Verificar columnas de publications
        result = db.session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'publications' 
            AND column_name IN ('file_path', 'updated_at', 'published_at')
        """))
        existing_columns = [row[0] for row in result.fetchall()]
        
        if 'file_path' not in existing_columns:
            print("⚠️ Agregando columna file_path a publications...")
            db.session.execute(text("ALTER TABLE publications ADD COLUMN file_path VARCHAR(500)"))
            db.session.commit()
            print("✅ Columna file_path agregada")
        else:
            print("✅ Columna file_path ya existe")
        
        if 'updated_at' not in existing_columns:
            print("⚠️ Agregando columna updated_at a publications...")
            db.session.execute(text("ALTER TABLE publications ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
            db.session.commit()
            print("✅ Columna updated_at agregada")
        else:
            print("✅ Columna updated_at ya existe")
        
        if 'published_at' not in existing_columns:
            print("⚠️ Agregando columna published_at a publications...")
            db.session.execute(text("ALTER TABLE publications ADD COLUMN published_at TIMESTAMP"))
            db.session.commit()
            print("✅ Columna published_at agregada")
        else:
            print("✅ Columna published_at ya existe")
        
        # Verificar columnas de notifications
        result = db.session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'notifications' 
            AND column_name = 'notification_type'
        """))
        
        if not result.fetchone():
            print("⚠️ Agregando columna notification_type a notifications...")
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN notification_type VARCHAR(50) DEFAULT 'deadline'"))
            db.session.commit()
            print("✅ Columna notification_type agregada")
        else:
            print("✅ Columna notification_type ya existe")
        
        # Crear tabla de mensajes si no existe
        result = db.session.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'messages'
            )
        """))
        
        if not result.fetchone()[0]:
            print("⚠️ Creando tabla messages...")
            db.session.execute(text("""
                CREATE TABLE messages (
                    message_id SERIAL PRIMARY KEY,
                    sender_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
                    receiver_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
                    subject VARCHAR(200),
                    content TEXT NOT NULL,
                    is_read BOOLEAN DEFAULT FALSE,
                    parent_message_id INTEGER REFERENCES messages(message_id) ON DELETE SET NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.commit()
            print("✅ Tabla messages creada")
        else:
            print("✅ Tabla messages ya existe")
        
        # Verificar si existe usuario admin
        result = db.session.execute(text("SELECT * FROM users WHERE username = 'admin'"))
        admin_exists = result.fetchone()
        
        if not admin_exists:
            print("⚠️ Creando usuario admin...")
            admin = User(
                username='admin',
                email='admin@rtp.local',
                full_name='Administrador del Sistema',
                role='admin',
                is_active=True
            )
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("✅ Usuario admin creado")
        else:
            print("✅ Usuario admin ya existe")
        
        print("=" * 50)
        print("✅ Base de datos lista para usar")
        print("=" * 50)
        print("Credenciales de acceso:")
        print("  Usuario: admin")
        print("  Contraseña: admin123")
        print("=" * 50)
        
    except Exception as e:
        print(f"❌ Error al verificar base de datos: {e}")
        db.session.rollback()


# ======================== EJECUCIÓN DE LA APLICACIÓN ========================

if __name__ == '__main__':
    os.makedirs(os.path.join('static', 'uploads'), exist_ok=True)
    app.run(debug=True, host='0.0.0.0', port=5000)