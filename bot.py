import json
import logging
import os
import platform
import random
import sys
import math
import time
import aiosqlite
import discord
from discord import ChannelType
from discord.ext import commands, tasks
from discord.ext.commands import Context
from dotenv import load_dotenv
import datetime
import a2s
import asyncio
import aiofiles as aiof
import logging
import sys
from arma_server_web_admin import ArmaServerDatabaseManager
from arma_server_web_admin import ArmaServerWebAdmin

if not os.path.isfile(f"{os.path.realpath(os.path.dirname(__file__))}/config.json"):
    sys.exit("'config.json' not found! Please add it and try again.")
else:
    with open(f"{os.path.realpath(os.path.dirname(__file__))}/config.json") as file:
        config = json.load(file)

"""	
Setup bot intents (events restrictions)
For more information about intents, please go to the following websites:
https://discordpy.readthedocs.io/en/latest/intents.html
https://discordpy.readthedocs.io/en/latest/intents.html#privileged-intents


Default Intents:
intents.bans = True
intents.dm_messages = True
intents.dm_reactions = True
intents.dm_typing = True
intents.emojis = True
intents.emojis_and_stickers = True
intents.guild_messages = True
intents.guild_reactions = True
intents.guild_scheduled_events = True
intents.guild_typing = True
intents.guilds = True
intents.integrations = True
intents.invites = True
intents.messages = True # `message_content` is required to get the content of the messages
intents.reactions = True
intents.typing = True
intents.voice_states = True
intents.webhooks = True

Privileged Intents (Needs to be enabled on developer portal of Discord), please use them only if you need them:
intents.members = True
intents.message_content = True
intents.presences = True
"""

intents = discord.Intents.default()

"""
Uncomment this if you want to use prefix (normal) commands.
It is recommended to use slash commands and therefore not use prefix commands.

If you want to use prefix commands, make sure to also enable the intent below in the Discord developer portal.
"""
# intents.message_content = True

# Setup both of the loggers


class LoggingFormatter(logging.Formatter):
    # Colors
    black = "\x1b[30m"
    red = "\x1b[31m"
    green = "\x1b[32m"
    yellow = "\x1b[33m"
    blue = "\x1b[34m"
    gray = "\x1b[38m"
    # Styles
    reset = "\x1b[0m"
    bold = "\x1b[1m"

    COLORS = {
        logging.DEBUG: gray + bold,
        logging.INFO: blue + bold,
        logging.WARNING: yellow + bold,
        logging.ERROR: red,
        logging.CRITICAL: red + bold,
    }

    def format(self, record):
        log_color = self.COLORS[record.levelno]
        format = "(black){asctime}(reset) (levelcolor){levelname:<8}(reset) (green){name}(reset) {message}"
        format = format.replace("(black)", self.black + self.bold)
        format = format.replace("(reset)", self.reset)
        format = format.replace("(levelcolor)", log_color)
        format = format.replace("(green)", self.green + self.bold)
        formatter = logging.Formatter(format, "%Y-%m-%d %H:%M:%S", style="{")
        return formatter.format(record)


logger = logging.getLogger("discord_bot")
logger.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(LoggingFormatter())
# File handler
file_handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")
file_handler_formatter = logging.Formatter(
    "[{asctime}] [{levelname:<8}] {name}: {message}", "%Y-%m-%d %H:%M:%S", style="{"
)
file_handler.setFormatter(file_handler_formatter)

# Add the handlers
logger.addHandler(console_handler)
logger.addHandler(file_handler)

class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or(config["prefix"]),
            intents=intents,
            help_command=None,
        )
        """
        This creates custom bot variables so that we can access these variables in cogs more easily.

        For example, The config is available using the following code:
        - self.config # In this class
        - bot.config # In this file
        - self.bot.config # In cogs
        """
        self.logger = logger
        self.config = config
        self.database = None
        self.arma_server_web_admin = None

    async def load_cogs(self) -> None:
        """
        The code in this function is executed whenever the bot will start.
        """
        for file in os.listdir(f"{os.path.realpath(os.path.dirname(__file__))}/cogs"):
            if file.endswith(".py"):
                extension = file[:-3]
                try:
                    await self.load_extension(f"cogs.{extension}")
                    self.logger.info(f"Loaded extension '{extension}'")
                except Exception as e:
                    exception = f"{type(e).__name__}: {e}"
                    self.logger.error(
                        f"Failed to load extension {extension}\n{exception}"
                    )

    async def setup_hook(self) -> None:
        """
        This will just be executed when the bot starts the first time.
        """
        self.logger.info(f"Logged in as {self.user.name}")
        self.logger.info(f"discord.py API version: {discord.__version__}")
        self.logger.info(f"Python version: {platform.python_version()}")
        self.logger.info(
            f"Running on: {platform.system()} {platform.release()} ({os.name})"
        )
        self.logger.info("-------------------")
        
        await self.grab_channels(1001559795310010539)

        #Setup server status db and updates, along with web panel integration
        await self.init_server_db()
        self.database = ArmaServerDatabaseManager(
            connection=await aiosqlite.connect(
                f"{os.path.realpath(os.path.dirname(__file__))}/arma_server_web_admin/database.db"
            )
        )
        self.update_server_status.start()
        self.arma_server_web_admin = ArmaServerWebAdmin(config=self.config, database=self.database)

        self.logger.info("-------------------")

        await self.load_cogs()

    async def on_message(self, message: discord.Message) -> None:
        """
        The code in this event is executed every time someone sends a message, with or without the prefix

        :param message: The message that was sent.
        """
        if message.author == self.user or message.author.bot:
            return
        await self.process_commands(message)

    async def on_command_completion(self, context: Context) -> None:
        """
        The code in this event is executed every time a normal command has been *successfully* executed.

        :param context: The context of the command that has been executed.
        """
        full_command_name = context.command.qualified_name
        split = full_command_name.split(" ")
        executed_command = str(split[0])
        if context.guild is not None:
            self.logger.info(
                f"Executed {executed_command} command in {context.guild.name} (ID: {context.guild.id}) by {context.author} (ID: {context.author.id})"
            )
        else:
            self.logger.info(
                f"Executed {executed_command} command by {context.author} (ID: {context.author.id}) in DMs"
            )

    async def on_command_error(self, context: Context, error) -> None:
        """
        The code in this event is executed every time a normal valid command catches an error.

        :param context: The context of the normal command that failed executing.
        :param error: The error that has been faced.
        """
        if isinstance(error, commands.CommandOnCooldown):
            minutes, seconds = divmod(error.retry_after, 60)
            hours, minutes = divmod(minutes, 60)
            hours = hours % 24
            embed = discord.Embed(
                description=f"**Please slow down** - You can use this command again in {f'{round(hours)} hours' if round(hours) > 0 else ''} {f'{round(minutes)} minutes' if round(minutes) > 0 else ''} {f'{round(seconds)} seconds' if round(seconds) > 0 else ''}.",
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        elif isinstance(error, commands.NotOwner):
            embed = discord.Embed(
                description="You are not the owner of the bot!", color=0xE02B2B
            )
            await context.send(embed=embed)
            if context.guild:
                self.logger.warning(
                    f"{context.author} (ID: {context.author.id}) tried to execute an owner only command in the guild {context.guild.name} (ID: {context.guild.id}), but the user is not an owner of the bot."
                )
            else:
                self.logger.warning(
                    f"{context.author} (ID: {context.author.id}) tried to execute an owner only command in the bot's DMs, but the user is not an owner of the bot."
                )
        elif isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                description="You are missing the permission(s) `"
                + ", ".join(error.missing_permissions)
                + "` to execute this command!",
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        elif isinstance(error, commands.BotMissingPermissions):
            embed = discord.Embed(
                description="I am missing the permission(s) `"
                + ", ".join(error.missing_permissions)
                + "` to fully perform this command!",
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        elif isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title="Error!",
                # We need to capitalize because the command arguments have no capital letter in the code and they are the first word in the error message.
                description=str(error).capitalize(),
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        else:
            raise error

    async def init_server_db(self) -> None:
        async with aiosqlite.connect(
            f"{os.path.realpath(os.path.dirname(__file__))}/arma_server_web_admin/database.db"
        ) as db:
            with open(
                f"{os.path.realpath(os.path.dirname(__file__))}/arma_server_web_admin/schema.sql"
            ) as file:
                # print(file.read())
                await db.executescript(file.read())
            await db.commit()

    @tasks.loop(minutes=5.0)
    async def update_server_status(self) -> None:
        """
        Setup the game status task of the bot.
        """
        unique_status_messages = await self.database.get_server_status_unique_messages()
        for unique_status_message in unique_status_messages:
            message = bot.get_channel(int(unique_status_message[1])).get_partial_message(int(unique_status_message[2]))
            if message:
                statuses = await self.database.get_server_status_from_message_id(str(unique_status_message[2]))
                embeds = []
                for status in statuses:
                    status_embed = await self.server_status_embed(status[4],status[5],status[6],int(status[7]), status[8], status[9], status[10])
                    embeds.append(status_embed)
                try:
                    await message.edit(content=None, embeds=embeds, view=None)
                except discord.errors.NotFound:
                    await self.database.remove_server_status(unique_status_message[0],unique_status_message[1],unique_status_message[2])
            else: 
                await self.database.remove_server_status(unique_status_message[0],unique_status_message[1],unique_status_message[2])

    async def update_server_status_message(self, guild_id: str, channel_id: str, message_id: str) -> None:
        """
        Setup the game status task of the bot.
        """
        message = bot.get_channel(int(channel_id)).get_partial_message(int(message_id))
        if message:
            statuses = await self.database.get_server_status_from_message_id(message_id)
            embeds = []
            for status in statuses:
                status_embed = await self.server_status_embed(status[4],status[5],status[6],int(status[7]), status[8], status[9], status[10])
                embeds.append(status_embed)
            try:
                if len(embeds) == 1:
                    await message.edit(content=None, embeds=embeds)
                else:
                    await message.edit(content=None, embeds=embeds, view=None)
            except discord.errors.NotFound:
                await self.database.remove_server_status(guild_id,channel_id,message_id)
        else: 
                await self.database.remove_server_status(guild_id,channel_id,message_id)

    async def server_status_embed(self, server_id: str, server_type:str, server_ip: str, server_port: int, server_name: str, server_desc: str, server_modpack: str) -> None:
        status_info = None
        try:
            a2s_info = await a2s.ainfo((server_ip, int(server_port) + 1), timeout=2)
            status_info = dict(a2s_info)
        except Exception as e:
            pass

        a2s_players = []
        if status_info: #Online...
            embed = discord.Embed(title=status_info["server_name"], description=f"{server_desc}", color=0x2BE02B, timestamp=datetime.datetime.utcnow())
            embed.add_field(name="Map", value=f"```{status_info['map_name']}```", inline=True)
            embed.add_field(name="Mission", value=f"```{status_info['game']}```", inline=True)
            embed.add_field(name="Player Count", value=f"```{status_info['player_count']}/{status_info['max_players']}```", inline=True)
            players_description = ""
            score_description = ""
            time_played_description = ""
            try:
                a2s_players = await a2s.aplayers((server_ip, int(server_port) + 1), timeout=2)
                a2s_players = sorted(a2s_players, key=lambda x: int(dict(x)['score']), reverse=True)
                for i, player in enumerate(a2s_players):
                    if len(players_description) + len(score_description) + len(time_played_description) > 900:
                        players_description = players_description + f"\n and {int(status_info['player_count']) - i} more players..."
                        break 
                    player_dict = dict(player)
                    player_name = player_dict['name']
                    if len(player_name) >= 33:
                        player_name = player_name[:30] + "..."
                    players_description = players_description + f"`{player_name}` \n"
                    score_description = score_description + f"`{player_dict['score']}` \n"
                    time_played = math.floor(player_dict['duration']/60)
                    if time_played >= 60:
                        time_played = f"{round(player_dict['duration']/60/60, 1)} Hours"
                    else: 
                        time_played = f"{math.floor(player_dict['duration']/60)} Minutes"
                    time_played_description = time_played_description + f"`{time_played}` \n"
                
            except Exception as e:
                players_description = ""
                score_description = ""
                time_played_description = ""

            if len(a2s_players):
                embed.add_field(name=f"Players", value=players_description, inline=True)
                embed.add_field(name=f"Score", value=score_description, inline=True)
                embed.add_field(name=f"Time Played", value=time_played_description, inline=True)

            if server_modpack:
                pass

            embed.set_footer(text=f"Connect via {server_ip}:{server_port}")

        else: #Offline...
            embed = discord.Embed(title=server_name, description=f"Offline", color=0xE02B2B, timestamp=datetime.datetime.utcnow())
    
        return embed

    @update_server_status.before_loop
    async def before_update_server_status(self) -> None:
        """
        Cheeck if bot is ready before running the server status refreshes.
        """
        await self.wait_until_ready()





load_dotenv()

bot = DiscordBot()
bot.run(os.getenv("TOKEN"))
