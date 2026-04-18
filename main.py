import os
import sys
import html
import re
import logging
import asyncio
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from database import DatabaseManager

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Initialize database
db = DatabaseManager()

def get_subject_display(subject_key):
    """Returns the formatted subject name with its icon."""
    subjects = db.get_subjects()
    icon = subjects.get(subject_key.lower(), "📚")
    return f"{icon} {subject_key.title()}"

def check_correct(opt, correct):
    """Normalizes and compares the option with the correct answer."""
    o = re.sub(r'^[A-F][\.\)]\s*', '', str(opt)).strip().lower()
    c = re.sub(r'^[A-F][\.\)]\s*', '', str(correct)).strip().lower()
    return o == c

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greets the user and explains how to use the bot."""
    welcome_message = (
        "<b>Hello! 👋 I am your Quiz Bot.</b>\n\n"
        "Send /quiz to begin a new session.\n"
        "Send /leaderboard to view top scores.\n"
        "Send /history to view your past quizzes."
    )
    await update.message.reply_text(welcome_message, parse_mode='HTML')

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the subject selection menu or handles session resumption."""
    subjects = db.get_subjects()
    if not subjects:
        await update.message.reply_text("Sorry, I don't have any subjects right now.")
        return

    # Check if a quiz is already in progress
    if 'quiz' in context.user_data:
        keyboard = [
            [
                InlineKeyboardButton("▶️ Resume Active Quiz", callback_data="nav_resume"),
                InlineKeyboardButton("🔄 Restart / New Quiz", callback_data="action_restart")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "⚠️ You have a quiz session in progress. What would you like to do?",
            reply_markup=reply_markup
        )
        return

    keyboard = []
    for subject in subjects.keys():
        display_name = get_subject_display(subject)
        keyboard.append([InlineKeyboardButton(display_name, callback_data=f"subj_{subject}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "<b>Please select a subject to start:</b>",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

# --- Handlers for Creation Flow ---

async def handle_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subj = query.data.split("_", 1)[1]
    
    chapters = db.get_chapters_by_subject(subj)
    if not chapters:
        # Check if it has any questions at all
        q = db.get_questions_by_subject(subj)
        if not q:
            await query.edit_message_text("No questions found for this subject.")
            return
        
        # If no chapters but questions exist, skip to quiz directly
        await query.edit_message_text("Loading...", reply_markup=None)
        
        for q_item in q:
            random.shuffle(q_item['options'])
            
        context.user_data['quiz'] = {
            'subject': subj,
            'chapter': 'All',
            'mode': 'practice',
            'questions': q,
            'answers': {},
            'current_q': 0,
            'chat_id': update.effective_chat.id
        }

        text, markup = get_question_message(context.user_data['quiz'])
        await query.edit_message_text(text=text, reply_markup=markup, parse_mode='HTML')
        return

    keyboard = []
    for ch in chapters:
        cb_data = f"chap_{subj}|{ch}"
        if len(cb_data.encode('utf-8')) > 64: cb_data = cb_data[:64]
        keyboard.append([InlineKeyboardButton(ch, callback_data=cb_data)])
        
    keyboard.append([InlineKeyboardButton("All Chapters (Full Quiz)", callback_data=f"chap_{subj}|All")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Subjects", callback_data="action_restart")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"📚 <b>{subj.title()}</b>\nSelect a chapter:", reply_markup=reply_markup, parse_mode='HTML')

async def handle_chapter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    payload = query.data.split("_", 1)[1]
    
    try:
        subj, chapter = payload.split("|")
    except ValueError:
        subj = payload.split("|")[0]
        chapter = "All"
        
    if chapter != "All":
        all_chapters = db.get_chapters_by_subject(subj)
        for true_ch in all_chapters:
            if true_ch.startswith(chapter):
                chapter = true_ch
                break

    all_questions = db.get_questions_by_subject(subj, chapter=(chapter if chapter != "All" else None))
    
    if not all_questions:
        await query.edit_message_text("No questions found.")
        return

    for q in all_questions:
        # If Gemini only returned "A" or "B" as the answer, map it to the string BEFORE shuffling
        correct_raw = str(q['correct']).strip()
        c_clean = re.sub(r'[\.\)]\s*', '', correct_raw).upper()
        letters = ['A', 'B', 'C', 'D', 'E', 'F']
        
        if c_clean in letters:
            idx = letters.index(c_clean)
            if idx < len(q['options']):
                q['correct'] = q['options'][idx]
                
        random.shuffle(q['options'])
        
    context.user_data['quiz'] = {
        'subject': subj,
        'chapter': chapter,
        'mode': 'practice',
        'questions': all_questions,
        'answers': {},
        'current_q': 0,
        'chat_id': update.effective_chat.id
    }

    text, markup = get_question_message(context.user_data['quiz'])
    await query.edit_message_text(text=text, reply_markup=markup, parse_mode='HTML')

# --- Quiz Core Logic UI ---

def get_question_message(quiz_data, q_idx=None, hide_nav=False):
    if q_idx is None:
        q_idx = quiz_data['current_q']
        
    q = quiz_data['questions'][q_idx]
    mode = quiz_data['mode']
    total = len(quiz_data['questions'])
    
    # Header
    filled = int(((q_idx) / total) * 10)
    bar = "🟩" * filled + "⬜" * (10 - filled)
    display_title = get_subject_display(quiz_data['subject'])
    
    text = f"<b>{display_title}</b> | <i>{mode.title()} Mode</i>\n"
    text += f"📊 <b>Progress:</b> {bar} ({q_idx + 1}/{total})\n\n"
    
    # Question text safely escaped
    q_text = html.escape(q['question'])
    text += f"<blockquote><b>❓ {q_text}</b></blockquote>\n\n"
    
    letters = ['A', 'B', 'C', 'D', 'E', 'F']
    selected_ans = quiz_data['answers'].get(str(q['id']))
    
    keyboard = []
    
    # Practice Mode View
    if selected_ans is not None:
        correct_idx = -1
        for i, opt in enumerate(q['options']):
            if check_correct(opt, q['correct']):
                correct_idx = i
                break
                
        is_correct = (selected_ans == correct_idx)
        status_text = "✅ Correct!" if is_correct else f"❌ Incorrect. Correct Answer: {letters[correct_idx]}"
        
        text += f"\n<tg-spoiler><b>Feedback:</b> {status_text}\n<b>💡 Explanation:</b> {html.escape(q['explanation'])}</tg-spoiler>\n"
        
        for i, opt in enumerate(q['options']):
            mark = "🔘 " if i == selected_ans else ""
            btn_text = f"{mark}{letters[i]}. {opt}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data="ignore")])
            
    # Unanswered View
    else:
        for i, opt in enumerate(q['options']):
            btn_text = f"{letters[i]}. {opt}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"ans_{q['id']}_{i}")])

    # Navigation Buttons
    nav_row = []
    if not hide_nav:
        if q_idx < total - 1:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"nav_next_{q_idx}"))
        else:
            nav_row.append(InlineKeyboardButton("🏁 Submit", callback_data="action_submit"))
    
    if nav_row:
        keyboard.append(nav_row)
        
    return text, InlineKeyboardMarkup(keyboard)

# --- Active Quiz Handlers ---

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if 'quiz' not in context.user_data: return
    
    _, q_id, opt_idx = query.data.split("_")
    opt_idx = int(opt_idx)
    quiz_data = context.user_data['quiz']
    
    if quiz_data['mode'] == 'practice' and str(q_id) in quiz_data['answers']:
        return # Can't change answer in practice mode
        
    quiz_data['answers'][str(q_id)] = opt_idx
    
    # Find exact targeted q_idx
    q_idx = -1
    for i, q in enumerate(quiz_data['questions']):
        if str(q['id']) == str(q_id):
            q_idx = i
            break
            
    hide_nav = (q_idx != quiz_data['current_q'])
    
    text, markup = get_question_message(quiz_data, q_idx=q_idx, hide_nav=hide_nav)
    await query.edit_message_text(text=text, reply_markup=markup, parse_mode='HTML')

async def handle_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if 'quiz' not in context.user_data: 
        await query.edit_message_text("Session expired.")
        return
        
    quiz_data = context.user_data['quiz']
    payload = query.data.split("_")
    direction = payload[1]
    
    if direction == 'next':
        clicked_q_idx = int(payload[2])
        if clicked_q_idx == quiz_data['current_q'] and quiz_data['current_q'] < len(quiz_data['questions']) - 1:
            quiz_data['current_q'] += 1
            
            # Edit old message to hide nav buttons
            old_text, old_markup = get_question_message(quiz_data, q_idx=clicked_q_idx, hide_nav=True)
            await query.edit_message_text(text=old_text, reply_markup=old_markup, parse_mode='HTML')
            
            # Send new message to create chat history
            new_text, new_markup = get_question_message(quiz_data, q_idx=quiz_data['current_q'], hide_nav=False)
            await context.bot.send_message(chat_id=query.message.chat_id, text=new_text, reply_markup=new_markup, parse_mode='HTML')
            
    elif direction == 'resume':
        text, markup = get_question_message(quiz_data, q_idx=quiz_data['current_q'], hide_nav=False)
        await query.edit_message_text(text="Resuming...", reply_markup=None)
        await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=markup, parse_mode='HTML')

async def handle_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split("_")[1]
    user_id = update.effective_user.id
    
    if action == "restart":
        if 'quiz' in context.user_data:
            remove_timer_if_exists(user_id, context)
            del context.user_data['quiz']
            
        subjects = db.get_subjects()
        keyboard = [[InlineKeyboardButton(get_subject_display(subj), callback_data=f"subj_{subj}")] for subj in subjects.keys()]
        await query.edit_message_text("<b>Please select a subject to start:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    elif action == "submit":
        if 'quiz' not in context.user_data: return
        await finish_quiz_manual(user_id, query.message.chat_id, context, update=update, query=query)
        
    elif action == "viewexp":
        questions = context.user_data.get('last_quiz_questions')
        answers = context.user_data.get('last_quiz_answers')
        if not questions:
            await query.answer("Explanations expired.", show_alert=True)
            return

        await query.edit_message_text(text=query.message.text + "\n\n<i>(Explanations sent below 👇)</i>", parse_mode='HTML')
        
        chunk_size = 5
        for i in range(0, len(questions), chunk_size):
            chunk = questions[i:i + chunk_size]
            msg = f"💡 <b>Explanations (Part {i//chunk_size + 1})</b>\n\n"
            for q in chunk:
                ans_idx = answers.get(str(q['id']))
                user_ans = q['options'][ans_idx] if ans_idx is not None else "Skipped"
                is_correct = (user_ans == q['correct'])
                
                status = "✅" if is_correct else "❌"
                msg += f"{status} <b>Q:</b> {html.escape(q['question'])}\n💡 <i>Exp:</i> <tg-spoiler>{html.escape(q['explanation'])}</tg-spoiler>\n\n"
            
            await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='HTML')
            await asyncio.sleep(0.5)

# --- Backend Processing ---

def remove_timer_if_exists(user_id, context):
    user_data = context.application.user_data.get(user_id)
    if user_data and 'quiz' in user_data and 'timer_task' in user_data['quiz']:
        user_data['quiz']['timer_task'].cancel()

async def timer_countdown(seconds, user_id, chat_id, context):
    await asyncio.sleep(seconds)
    user_data = context.application.user_data.get(user_id)
    if user_data and 'quiz' in user_data:
        await finish_quiz_manual(user_id, chat_id, context, auto=True)

async def finish_quiz_manual(user_id, chat_id, context, auto=False, update=None, query=None):
    user_data = context.application.user_data.get(user_id)
    if not user_data or 'quiz' not in user_data: return

    quiz_data = user_data['quiz']
    score = 0
    total = len(quiz_data['questions'])
    
    for q in quiz_data['questions']:
        ans_idx = quiz_data['answers'].get(str(q['id']))
        if ans_idx is not None:
            if check_correct(q['options'][ans_idx], q['correct']):
                score += 1
            
    display_title = get_subject_display(quiz_data['subject'])
    prefix = "⏰ <b>Time's up!</b>\n\n" if auto else ""
    text = f"{prefix}🏁 <b>Quiz Finished!</b>\n\n"
    text += f"<b>Subject:</b> {display_title}\n"
    text += f"🏆 <b>FINAL SCORE:</b> {score} / {total}\n\n"
    
    keyboard = []
        
    keyboard.append([InlineKeyboardButton("🔄 Start New Quiz", callback_data="action_restart")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Save for explanations handler
    context.user_data['last_quiz_questions'] = quiz_data['questions']
    context.user_data['last_quiz_answers'] = quiz_data['answers']
    
    base_subject = quiz_data['subject']
    chapter = quiz_data.get('chapter', 'All')
    logged_subject = f"{base_subject} - {chapter.split(':')[0]}" if chapter != "All" else base_subject
        
    username = update.effective_user.username or update.effective_user.first_name if update else "User"
    db.save_score(user_id, username, logged_subject, score, total)
    
    remove_timer_if_exists(user_id, context)
    del user_data['quiz']
    
    if query:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='HTML')

# --- Base Commands ---

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subject = " ".join(context.args).lower() if context.args else None
    rows = db.get_leaderboard(subject_key=subject) if subject else db.get_leaderboard()
    title = f"🏆 <b>{subject.title()} Leaderboard</b>" if subject else "🏆 <b>Global Leaderboard</b>"

    if not rows:
        await update.message.reply_text("No scores recorded yet. Be the first!")
        return

    text = f"{title}\n\n"
    for i, row in enumerate(rows):
        username, score, *extra = row
        medal = ["🥇", "🥈", "🥉", "👤", "👤"][i] if i < 5 else "👤"
        if subject:
            text += f"{medal} {username}: <b>{score}/{extra[0]}</b>\n"
        else:
            text += f"{medal} {username}: <b>{score} pts</b>\n"
            
    await update.message.reply_text(text, parse_mode='HTML')

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = db.get_user_history(user_id)
    
    if not rows:
        await update.message.reply_text("You haven't taken any quizzes yet!")
        return
        
    text = "📜 <b>Your Quiz History</b>\n\n"
    for i, row in enumerate(rows):
        subj_key, score, total, timestamp = row
        fmt_time = timestamp[:16] if timestamp else "Unknown time"
        text += f"{i+1}. <b>{subj_key}</b> - Score: {score}/{total} (<i>{fmt_time}</i>)\n"
        
    await update.message.reply_text(text, parse_mode='HTML')

async def ignore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if not TOKEN or TOKEN == "YOUR_TOKEN_HERE":
        print("Please set your TELEGRAM_BOT_TOKEN in the .env file")
        exit(1)

    app = ApplicationBuilder().token(TOKEN).job_queue(None).build()

    # Handlers Registration
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("history", history))
    
    # Callback Handlers (Routed by Pattern)
    app.add_handler(CallbackQueryHandler(handle_subject, pattern="^subj_"))
    app.add_handler(CallbackQueryHandler(handle_chapter, pattern="^chap_"))
    app.add_handler(CallbackQueryHandler(handle_nav, pattern="^nav_"))
    app.add_handler(CallbackQueryHandler(handle_answer, pattern="^ans_"))
    app.add_handler(CallbackQueryHandler(handle_actions, pattern="^action_"))
    app.add_handler(CallbackQueryHandler(ignore_callback, pattern="^ignore$"))

    print("Bot is running...")
    app.run_polling()
