import discord
from discord.ext import commands
import os
import asyncio
import time
import logging
from collections import defaultdict
from dotenv import load_dotenv

# Modern API imports
try:
    from openai import OpenAI
except ImportError:
    print("❌ Please install: pip install openai>=1.0.0")
    exit(1)

try:
    import google.generativeai as genai
except ImportError:
    print("❌ Please install: pip install google-generativeai")
    exit(1)

# Load environment variables
load_dotenv()

# Configuration
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Validate required environment variables
if not TOKEN:
    print("❌ DISCORD_BOT_TOKEN not found in environment variables")
    exit(1)

# Setup logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize AI clients
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    logger.info("✅ OpenAI client initialized")
else:
    logger.warning("⚠️ OpenAI API key not found")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    logger.info("✅ Gemini client initialized")
else:
    logger.warning("⚠️ Gemini API key not found")

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix='/', intents=intents)

# Anti-spam configuration
user_message_times = defaultdict(list)
SPAM_TIME_WINDOW = 10  # seconds
SPAM_MESSAGE_LIMIT = 5  # messages allowed per window

# Rate limiting for AI commands
user_ai_times = defaultdict(list)
AI_TIME_WINDOW = 60  # seconds
AI_MESSAGE_LIMIT = 3  # AI requests per minute

async def get_or_create_logs_channel(guild):
    """Get existing logs channel or create one"""
    log_channel = discord.utils.get(guild.text_channels, name="logs")
    
    if not log_channel:
        try:
            # Create logs channel with restricted permissions
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(send_messages=False),
                guild.me: discord.PermissionOverwrite(send_messages=True)
            }
            log_channel = await guild.create_text_channel(
                'logs', 
                topic='Bot message logs',
                overwrites=overwrites
            )
            logger.info(f"✅ Created logs channel in {guild.name}")
        except discord.Forbidden:
            logger.error(f"❌ No permission to create logs channel in {guild.name}")
        except Exception as e:
            logger.error(f"❌ Error creating logs channel: {e}")
    
    return log_channel

def check_spam(user_id, message_times_dict, time_window, message_limit):
    """Check if user is spamming"""
    now = time.time()
    user_times = message_times_dict[user_id]
    # Remove old timestamps
    user_times[:] = [t for t in user_times if now - t < time_window]
    user_times.append(now)
    return len(user_times) > message_limit

@bot.event
async def on_ready():
    logger.info(f'✅ {bot.user} is online!')
    logger.info(f'Bot is in {len(bot.guilds)} guilds')
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        logger.info(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        logger.error(f"❌ Failed to sync slash commands: {e}")

@bot.event
async def on_guild_join(guild):
    """Create logs channel when bot joins a new guild"""
    await get_or_create_logs_channel(guild)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Anti-spam check
    if check_spam(message.author.id, user_message_times, SPAM_TIME_WINDOW, SPAM_MESSAGE_LIMIT):
        try:
            await message.reply("🚫 Slow down! You're sending messages too quickly.", delete_after=5)
        except discord.Forbidden:
            pass
        return

    # Message logging (only in guilds, not DMs)
    if message.guild:
        log_channel = await get_or_create_logs_channel(message.guild)
        if log_channel:
            try:
                # Create embed for better formatting
                embed = discord.Embed(
                    description=message.content[:1900] if message.content else "*[No text content]*",
                    color=0x3498db,
                    timestamp=message.created_at
                )
                embed.set_author(
                    name=f"{message.author.display_name} ({message.author})",
                    icon_url=message.author.display_avatar.url
                )
                embed.set_footer(text=f"#{message.channel.name} • {message.guild.name}")
                
                # Add attachment info if present
                if message.attachments:
                    embed.add_field(
                        name="Attachments",
                        value="\n".join([att.filename for att in message.attachments]),
                        inline=False
                    )
                
                await log_channel.send(embed=embed)
            except discord.Forbidden:
                logger.warning(f"No permission to send to logs channel in {message.guild.name}")
            except Exception as e:
                logger.error(f"Error logging message: {e}")

    await bot.process_commands(message)

@bot.hybrid_command(name="ai", description="Chat with AI (ChatGPT or Gemini)")
async def ai_command(ctx, ai_model: str, *, prompt: str):
    """
    Chat with AI models
    
    Parameters:
    ai_model: Choose 'chatgpt' or 'gemini'
    prompt: Your message to the AI
    """
    ai_model = ai_model.lower()
    
    if ai_model not in ['chatgpt', 'gemini']:
        await ctx.send("❌ Invalid AI model. Use `chatgpt` or `gemini`.", ephemeral=True)
        return
    
    # Check AI rate limiting
    if check_spam(ctx.author.id, user_ai_times, AI_TIME_WINDOW, AI_MESSAGE_LIMIT):
        await ctx.send("🚫 You're making too many AI requests. Please wait a minute.", ephemeral=True)
        return
    
    # Check if the AI service is available
    if ai_model == 'chatgpt' and not openai_client:
        await ctx.send("❌ ChatGPT is not available (API key missing).", ephemeral=True)
        return
    
    if ai_model == 'gemini' and not GEMINI_API_KEY:
        await ctx.send("❌ Gemini is not available (API key missing).", ephemeral=True)
        return
    
    # Defer the response since AI might take time
    await ctx.defer()
    
    try:
        if ai_model == 'chatgpt':
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: openai_client.chat.completions.create(
                    model="gpt-4o-mini",  # Free-tier model
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1000
                )
            )
            ai_response = response.choices[0].message.content
            
        elif ai_model == 'gemini':
            model = genai.GenerativeModel('gemini-1.5-flash')  # Free-tier model
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: model.generate_content(prompt)
            )
            ai_response = response.text
        
        # Split long responses
        if len(ai_response) > 2000:
            # Send in chunks
            for i in range(0, len(ai_response), 1900):
                chunk = ai_response[i:i+1900]
                if i == 0:
                    await ctx.followup.send(f"🤖 **{ai_model.title()}**: {chunk}")
                else:
                    await ctx.followup.send(chunk)
        else:
            await ctx.followup.send(f"🤖 **{ai_model.title()}**: {ai_response}")
            
    except Exception as e:
        logger.error(f"AI request error: {e}")
        await ctx.followup.send(f"⚠️ Error communicating with {ai_model.title()}: {str(e)[:100]}")

@bot.hybrid_command(name="logs", description="Create or get info about the logs channel")
@commands.has_permissions(manage_channels=True)
async def logs_command(ctx):
    """Create logs channel if it doesn't exist"""
    if not ctx.guild:
        await ctx.send("❌ This command can only be used in servers.", ephemeral=True)
        return
        
    log_channel = await get_or_create_logs_channel(ctx.guild)
    if log_channel:
        await ctx.send(f"✅ Logs channel: {log_channel.mention}", ephemeral=True)
    else:
        await ctx.send("❌ Could not create or access logs channel. Check bot permissions.", ephemeral=True)

@bot.hybrid_command(name="info", description="Show bot information")
async def info_command(ctx):
    """Display bot information"""
    embed = discord.Embed(
        title="🤖 Bot Information",
        color=0x00ff00,
        description="Discord bot with AI integration"
    )
    
    # AI availability
    ai_status = []
    if openai_client:
        ai_status.append("✅ ChatGPT")
    else:
        ai_status.append("❌ ChatGPT")
        
    if GEMINI_API_KEY:
        ai_status.append("✅ Gemini")
    else:
        ai_status.append("❌ Gemini")
    
    embed.add_field(name="AI Services", value="\n".join(ai_status), inline=True)
    embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Users", value=str(len(bot.users)), inline=True)
    
    embed.add_field(
        name="Rate Limits",
        value=f"Messages: {SPAM_MESSAGE_LIMIT}/{SPAM_TIME_WINDOW}s\nAI Requests: {AI_MESSAGE_LIMIT}/{AI_TIME_WINDOW}s",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors"""
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.", ephemeral=True)
    elif isinstance(error, commands.CommandNotFound):
        pass  # Ignore unknown commands
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing required argument: `{error.param}`", ephemeral=True)
    else:
        logger.error(f"Command error: {error}")
        await ctx.send("❌ An error occurred while processing the command.", ephemeral=True)

if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")