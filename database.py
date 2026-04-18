import sqlite3
import json
import logging
import os

class DatabaseManager:
    def __init__(self, db_name="quiz.db"):
        # Ensure we always interact with the db in the precise script directory!
        base_dir = os.path.abspath(os.path.dirname(__file__))
        self.db_name = os.path.join(base_dir, db_name)
        self.init_db()

    def init_db(self):
        try:
            with sqlite3.connect(self.db_name) as conn:
                cursor = conn.cursor()
                # Create subjects table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS subjects (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE,
                        icon TEXT
                    )
                ''')
                # Create questions table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS questions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        subject_id INTEGER,
                        question_text TEXT,
                        options_json TEXT,
                        correct_answer TEXT,
                        explanation TEXT,
                        chapter_name TEXT,
                        FOREIGN KEY (subject_id) REFERENCES subjects(id)
                    )
                ''')
                # Migration: Add chapter_name if it doesn't exist
                try:
                    cursor.execute("ALTER TABLE questions ADD COLUMN chapter_name TEXT")
                except sqlite3.OperationalError:
                    pass # Already exists
                
                # Create user_scores table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_scores (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        username TEXT,
                        subject_key TEXT,
                        score INTEGER,
                        total INTEGER,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                conn.commit()
        except sqlite3.Error as e:
            logging.error(f"Database Initialization Error: {e}")

    def add_subject(self, name, icon):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO subjects (name, icon) VALUES (?, ?)', (name.lower(), icon))
            conn.commit()

    def add_question(self, subject_name, question_text, options, correct_answer, explanation="No explanation provided.", chapter_name=None):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM subjects WHERE name = ?', (subject_name.lower(),))
            subject_id = cursor.fetchone()
            if subject_id:
                cursor.execute('''
                    INSERT INTO questions (subject_id, question_text, options_json, correct_answer, explanation, chapter_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (subject_id[0], question_text, json.dumps(options), correct_answer, explanation, chapter_name))
                conn.commit()

    def get_subjects(self):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT name, icon FROM subjects')
            return {name: icon for name, icon in cursor.fetchall()}

    def get_chapters_by_subject(self, subject_name):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT q.chapter_name
                FROM questions q
                JOIN subjects s ON q.subject_id = s.id
                WHERE s.name = ? AND q.chapter_name IS NOT NULL
                ORDER BY q.chapter_name ASC
            ''', (subject_name.lower(),))
            rows = cursor.fetchall()
            return [row[0] for row in rows]

    def get_questions_by_subject(self, subject_name, chapter=None):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            
            if chapter and chapter != "All":
                cursor.execute('''
                    SELECT q.id, q.question_text, q.options_json, q.correct_answer, q.explanation 
                    FROM questions q
                    JOIN subjects s ON q.subject_id = s.id
                    WHERE s.name = ? AND q.chapter_name = ?
                ''', (subject_name.lower(), chapter))
            else:
                cursor.execute('''
                    SELECT q.id, q.question_text, q.options_json, q.correct_answer, q.explanation 
                    FROM questions q
                    JOIN subjects s ON q.subject_id = s.id
                    WHERE s.name = ?
                ''', (subject_name.lower(),))
                
            rows = cursor.fetchall()
            questions = []
            for row in rows:
                questions.append({
                    "id": row[0],
                    "question": row[1],
                    "options": json.loads(row[2]),
                    "correct": row[3],
                    "explanation": row[4]
                })
            return questions

    def save_score(self, user_id, username, subject_key, score, total):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO user_scores (user_id, username, subject_key, score, total)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, username, subject_key.lower(), score, total))
            conn.commit()

    def get_leaderboard(self, subject_key=None, limit=5):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            if subject_key:
                cursor.execute('''
                    SELECT username, MAX(score) as max_score, total
                    FROM user_scores
                    WHERE subject_key = ?
                    GROUP BY user_id
                    ORDER BY max_score DESC
                    LIMIT ?
                ''', (subject_key.lower(), limit))
            else:
                 cursor.execute('''
                    SELECT username, SUM(score) as total_score
                    FROM user_scores
                    GROUP BY user_id
                    ORDER BY total_score DESC
                    LIMIT ?
                ''', (limit,))
            return cursor.fetchall()
            
    def get_user_history(self, user_id):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT subject_key, score, total, timestamp
                FROM user_scores
                WHERE user_id = ?
                ORDER BY timestamp ASC
            ''', (user_id,))
            return cursor.fetchall()

    def delete_subject(self, name):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM subjects WHERE name = ?', (name.lower(),))
            subject_id = cursor.fetchone()
            if subject_id:
                cursor.execute('DELETE FROM questions WHERE subject_id = ?', (subject_id[0],))
                cursor.execute('DELETE FROM subjects WHERE id = ?', (subject_id[0],))
                conn.commit()
                return True
        return False

    def delete_chapter(self, subject_name, chapter_name):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM subjects WHERE name = ?', (subject_name.lower(),))
            subject_id = cursor.fetchone()
            if subject_id:
                cursor.execute('DELETE FROM questions WHERE subject_id = ? AND chapter_name = ?', (subject_id[0], chapter_name))
                conn.commit()
                return True
        return False
