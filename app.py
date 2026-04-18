import os
import sqlite3
import json
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import zipfile
import re
from PyPDF2 import PdfReader
import asyncio
from telegram import Update
from main import setup_application
from database import DatabaseManager

# Absolute path for PythonAnywhere
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Load environment using absolute path
load_dotenv(os.path.join(BASE_DIR, '.env'))
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Initialize Telegram application for webhooks
tg_app = setup_application(TOKEN) if TOKEN else None

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Absolute path for PythonAnywhere
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

# --- Document Processing Helpers ---

def extract_text_from_pdf(pdf_path):
    try:
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        return text
    except Exception as e:
        return f"Error extracting PDF: {e}"

def extract_text_from_pptx(pptx_path):
    try:
        text_content = []
        with zipfile.ZipFile(pptx_path, 'r') as zip_ref:
            xml_files = [f for f in zip_ref.namelist() if f.startswith('ppt/slides/slide') and f.endswith('.xml')]
            xml_files.sort(key=lambda x: int(re.search(r'slide(\d+)\.xml', x).group(1)))
            
            for xml_file in xml_files:
                with zip_ref.open(xml_file) as f:
                    content = f.read().decode('utf-8')
                    text_elements = re.findall(r'<a:t>(.*?)</a:t>', content)
                    if text_elements:
                        text_content.append(" ".join(text_elements))
        return "\n".join(text_content)
    except Exception as e:
        return f"Error extracting PPTX: {e}"

# --- AI Integration (Direct HTTP for Gemini) ---

def generate_questions_with_ai(text, num_questions=10):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "No Gemini API Key found in .env"}

    # List of models to try in order of preference
    models_to_try = [
        "gemini-flash-lite-latest",
        "gemini-1.5-flash", 
        "gemini-2.0-flash",
        "gemini-pro"
    ]
    
    last_error = ""
    
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        
        prompt = f"""
        You are an expert academic examiner. Your task is to generate {num_questions} high-quality, professional multiple-choice questions (MCQs) based ONLY on the provided text.
        
        CRITICAL RULES:
        1. SOURCE: Use ONLY the information provided in the TEXT below.
        2. STYLE: Questions must feel natural and formal (standard exam style).
        3. NO META-REFERENCES: Do NOT use phrases like "according to the text", "based on the passage", "as mentioned in the PDF", or "in the document".
        4. SELF-CONTAINED: Do NOT mention the PDF, document, or text explicitly in the questions. The questions should stand alone as if they were from a general exam on the subject.
        
        FORMAT REQUIREMENTS:
        1. Output MUST be ONLY a valid JSON array of objects.
        2. Each object MUST have: "question", "options" (array of 4 strings), "answer" (A, B, C, or D), and "explanation".
        3. The explanation should be clear and concise.
        
        TEXT:
        {text[:30000]}
        """

        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        
        try:
            print(f"DEBUG: Trying AI Generation with model: {model_name}...")
            response = requests.post(url, json=payload, timeout=60)
            
            if response.status_code == 429:
                last_error = f"Quota exceeded for {model_name}. "
                continue
            
            if response.status_code != 200:
                resp_data = response.json()
                last_error = resp_data.get('error', {}).get('message', f"Error {response.status_code}")
                print(f"DEBUG: {model_name} failed: {last_error}")
                continue
                
            response_json = response.json()
            if 'candidates' not in response_json:
                last_error = "No candidates in response."
                continue

            raw_output = response_json['candidates'][0]['content']['parts'][0]['text']
            clean_json = re.sub(r'```json\n?|\n?```', '', raw_output).strip()
            return json.loads(clean_json)
            
        except Exception as e:
            last_error = str(e)
            print(f"DEBUG: Exception with {model_name}: {e}")
            continue
            
    return {"error": f"All AI models failed or busy. Last error: {last_error}"}

# --- Routes ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        target_username = os.getenv("ADMIN_USERNAME", "awoke123")
        target_password = os.getenv("ADMIN_PASSWORD", "Awok10$@")
        
        if username == target_username and password == target_password:
            user = User(1)
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    db = DatabaseManager()
    with sqlite3.connect(db.db_name) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, icon FROM subjects")
        subjects = cursor.fetchall()
        
        cursor.execute("SELECT count(*) FROM questions")
        total_q = cursor.fetchone()[0]
        
    return render_template('index.html', subjects=subjects, total_questions=total_q)

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    subject_selection = request.form.get('subject_selection')
    new_subject_name = request.form.get('new_subject_name')
    
    # Determine the final subject name
    if subject_selection == '_new':
        subject_name = new_subject_name
    else:
        subject_name = subject_selection
        
    num_q = int(request.form.get('num_questions', 10))
    chapter_name = request.form.get('chapter_name', '').strip()
    
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # 1. Extract Text
        if filename.endswith('.pdf'):
            text = extract_text_from_pdf(filepath)
        elif filename.endswith('.pptx'):
            text = extract_text_from_pptx(filepath)
        else:
            return jsonify({"error": "Unsupported file format"}), 400
        
        if not text or len(text) < 50:
            return jsonify({"error": "Could not extract enough text from the document."}), 400
            
        # 2. Call AI
        ai_questions = generate_questions_with_ai(text, num_q)
        
        if isinstance(ai_questions, dict) and "error" in ai_questions:
            return jsonify({"error": f"AI Generation Failed: {ai_questions['error']}"}), 500
            
        # 3. Inject to DB
        db = DatabaseManager()
        db.add_subject(subject_name, "🤖")  # Default AI icon
        
        count = 0
        for q in ai_questions:
            db.add_question(
                subject_name, 
                q['question'], 
                q['options'], 
                q['answer'], 
                q['explanation'], 
                chapter_name
            )
            count += 1
            
        return jsonify({"message": f"Success! {count} questions generated and added to '{subject_name}' (Chapter: {chapter_name})."})

@app.route('/api/subjects')
@login_required
def get_admin_subjects():
    db = DatabaseManager()
    subjects_dict = db.get_subjects()
    result = []
    for name, icon in subjects_dict.items():
        chapters = db.get_chapters_by_subject(name)
        result.append({
            "name": name,
            "icon": icon,
            "chapters": chapters
        })
    return jsonify(result)

@app.route('/api/questions/<subject>/<chapter>')
@login_required
def get_chapter_questions_admin(subject, chapter):
    db = DatabaseManager()
    questions = db.get_questions_by_subject(subject, chapter)
    return jsonify(questions)

@app.route('/api/subject/delete/<subject>', methods=['DELETE'])
@login_required
def delete_subject_api(subject):
    db = DatabaseManager()
    if db.delete_subject(subject):
        return jsonify({"message": f"Subject '{subject}' and all its questions deleted successfully."})
    return jsonify({"error": "Subject not found"}), 404

@app.route('/api/chapter/delete/<subject>/<chapter>', methods=['DELETE'])
@login_required
def delete_chapter_api(subject, chapter):
    db = DatabaseManager()
    if db.delete_chapter(subject, chapter):
        return jsonify({"message": f"Chapter '{chapter}' deleted successfully."})
    return jsonify({"error": "Chapter not found"}), 404

@app.route('/webhook/' + (TOKEN if TOKEN else 'error'), methods=['POST'])
def webhook():
    """Endpoint for Telegram Webhook updates."""
    if not tg_app:
        return "Bot not initialized", 500
        
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, tg_app.bot)
        
        # Process the update asynchronously
        async def process():
            async with tg_app:
                await tg_app.process_update(update)
                
        asyncio.run(process())
        return "OK", 200
    except Exception as e:
        print(f"Webhook error: {e}")
        return "Internal Error", 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
