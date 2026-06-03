import os
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, BackgroundTasks, status
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# =====================================================================
# 1. LOGGING & ENVIRONMENT CONFIGURATION
# =====================================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Fetch and clean environment variables
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
BASE_URL = os.environ.get("WEBHOOK_URL")

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is missing!")
if not BASE_URL:
    raise ValueError("WEBHOOK_URL environment variable is missing!")

# Strip trailing slash to guarantee flawless route concatenation
WEBHOOK_URL = f"{BASE_URL.rstrip('/')}/telegram"


# =====================================================================
# 2. BOT CORE LOGIC / HANDLERS (USING HTML TO PREVENT PARSING ERRORS)
# =====================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command with safe HTML formatting."""
    welcome_text = (
        "🤖 <b>Welcome to Y_Summarizerbot!</b>\n\n"
        "I am your personal AI-powered reading assistant. Send or forward any long "
        "text wall or article here, and I will condense it into sharp, readable bullet points!\n\n"
        "📥 <i>Just paste your text below to get started.</i>"
    )
    if update.effective_message:
        await update.effective_message.reply_text(text=welcome_text, parse_mode="HTML")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catches text, replies safely with HTML, and edits without crashing."""
    if not update.effective_message or not update.effective_message.text:
        return

    # 1. Send immediate placeholder response using HTML tags to keep UX snappy
    placeholder_msg = await update.effective_message.reply_text(
        text="⚡ <b>Processing your text... Please wait.</b>", parse_mode="HTML"
    )

    user_text = update.effective_message.text

    try:
        # 2. Simulate background delay / summarization engine processing
        await asyncio.sleep(3) 
        
        words_count = len(user_text.split())
        
        # 3. Use HTML tags here instead of Markdown characters to isolate special characters
        summary_result = (
            f"📝 <b>Summary of your {words_count}-word text:</b>\n\n"
            f"• <b>Core Theme:</b> User shared custom text insights.\n"
            f"• <b>Key Takeaway:</b> This is a simulated summarization result.\n"
            f"• <b>Actionable Item:</b> Integration with an LLM layer can happen here seamlessly."
        )

        # 4. Edit the placeholder message safely using HTML
        await context.bot.edit_message_text(
            chat_id=placeholder_msg.chat_id,
            message_id=placeholder_msg.message_id,
            text=summary_result,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error during summarization process: {e}")
        await context.bot.edit_message_text(
            chat_id=placeholder_msg.chat_id,
            message_id=placeholder_msg.message_id,
            text="❌ <i>Sorry, an error occurred while processing your summary.</i>",
            parse_mode="HTML"
        )


# =====================================================================
# 3. LIFESPAN MANAGEMENT (WEBHOOK SETUP & TEARDOWN)
# =====================================================================
# Global reference to pass the application state across FastAPI requests safely
ptb_app: Application = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles the setup and teardown of the Telegram Bot's Webhook workflow."""
    global ptb_app
    
    # Initialize python-telegram-bot Application
    ptb_app = Application.builder().token(BOT_TOKEN).build()
    
    # Register core handlers
    ptb_app.add_handler(CommandHandler("start", start_command))
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # Initialize underlying components
    await ptb_app.initialize()
    
    # Set the remote Telegram Webhook pointing to Render URL (drops pending updates on start)
    logger.info(f"Setting Telegram webhook to: {WEBHOOK_URL}")
    await ptb_app.bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
    
    # Yield control back to FastAPI to begin serving requests
    yield
    
    # Graceful shutdown routines
    logger.info("Server stopping: Removing Telegram webhook and shutting down bot...")
    try:
        await ptb_app.bot.delete_webhook()
        await ptb_app.shutdown()
        # FIXED: Removed the non-existent ptb_app.uninitialize() call completely!
    except Exception as shutdown_error:
        logger.error(f"Error during graceful shutdown sequence: {shutdown_error}")


# Initialize FastAPI app bound to our async lifespan manager
app = FastAPI(lifespan=lifespan)


# =====================================================================
# 4. ENDPOINTS / ROUTING
# =====================================================================
@app.get("/", status_code=status.HTTP_200_OK)
async def health_check():
    """Root health check endpoint. Essential for Render or uptime cron pingers."""
    return JSONResponse(
        content={
            "status": "healthy",
            "bot_name": "Y_Summarizerbot",
            "message": "Web server running smoothly."
        }
    )


async def process_update_background(update_dict: dict) -> None:
    """Background runner that safely executes update payloads outside the web loop."""
    try:
        update = Update.de_json(update_dict, ptb_app.bot)
        # Feed the update straight into PTB's execution queue
        await ptb_app.process_update(update)
    except Exception as e:
        logger.error(f"Failed processing background update: {e}")


@app.post("/telegram", status_code=status.HTTP_200_OK)
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Accepts the webhook payload from Telegram, offloads execution instantly 
    to a BackgroundTask, and returns an immediate 200 OK response.
    """
    try:
        update_json = await request.json()
        
        # Immediately delegate to the background task executor (takes 1-2 milliseconds max)
        background_tasks.add_task(process_update_background, update_json)
        
        # Instantly reply to Telegram to keep it perfectly happy and mitigate timeouts
        return Response(status_code=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error received in webhook endpoint: {e}")
        # Return a 200 anyway to prevent Telegram from retrying bad payloads repeatedly
        return Response(status_code=status.HTTP_200_OK)
