from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import aiohttp
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
import os
import base64
import a2s
import datetime
import asyncpraw
import praw

load_dotenv()


class Helper(commands.Cog, name="helper"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="form", 
        description="Get a link to the battalion-wide form and responses.",
    )
    async def form(self, context: Context) -> None:
        """
        Get a link to the battalion-wide form and responses.

        :param context: The hybrid command context.
        """
        buttons = discord.ui.View()
        buttons.add_item(discord.ui.Button(label="Battlaion Form", style=discord.ButtonStyle.link, url = 'https://docs.google.com/forms/d/e/1FAIpQLSdykee7E-75ldEvuQ4SYBT1H1dG2v4sFCG5j85dDJYcPVDgUA/viewform'))
        buttons.add_item(discord.ui.Button(label="Responses", style=discord.ButtonStyle.link, url = 'https://docs.google.com/spreadsheets/d/1tkhHPKfpW2JxpQZq4vGv0gbVgtAIVgTiJkBoMc33d9E/edit?gid=1029717932#gid=1029717932'))
        
        embed = discord.Embed(title="""Battalion Form""", description="""Check out the form below for any suggestions, badge requests and feedback. The following options are available:""", color=0xBEBEFE)
        embed.add_field(name=f"""Battalion Suggestions""", value=f"""Send in your complaints, ideas and improvements here for BATCOM to review and address.""", inline=True)
        embed.add_field(name=f"""Newsletter""", value=f"""Got any ideas/submissions for the newsletter? Send in your snazzy recipes or posts here.""", inline=True)
        embed.add_field(name=f"""Monthly Meeting""", value=f"""Have any topics you want to discuss wider within the battalion? Post your topics here to be picked up at the montly battalion-wide meetings.""", inline=True)
        embed.add_field(name=f"""Mission Development (MISDEV)""", value=f"""Send in your feedback and ideas for missions so we can continue to improve the missions we play as a battalion.""", inline=True)
        embed.add_field(name=f"""Combat Training Centre (CTC)""", value=f"""Request for specific badges and training so that you are ready on the field.""", inline=True)
        embed.add_field(name=f"""Server Admin Office (SAO)""", value=f"""Send in your mod change requests and or any bug reports here so we can continue to improve the server.""", inline=True)
        embed.add_field(name=f"""Position Interest""", value=f"""Are you interested in any positions within the unit? Register your interest here even if the position is filled.""", inline=True)
        embed.add_field(name=f"""Arsenal Requests""", value=f"""Request changes to the in-game arsenal here if there's anything you feel is missing or should be removed.""", inline=True)
        embed.add_field(name=f"", value=f"", inline=True)
        
        await context.send(embed=embed, view=buttons, ephemeral=True)



    @commands.hybrid_command(
        name="social", 
        description="Get a link to all the battalion's social pages",
    )
    async def social(self, context: Context, post: bool=False) -> None:
        """
        Get a link to all the battalion's social pages

        :param context: The hybrid command context.
        """
        buttons = discord.ui.View()
        buttons.add_item(discord.ui.Button(label="Twitch", style=discord.ButtonStyle.link, url = 'https://www.twitch.tv/tawarmadivision'))
        buttons.add_item(discord.ui.Button(label="Reddit", style=discord.ButtonStyle.link, url = 'https://www.reddit.com/user/tawarmadivision/'))
        buttons.add_item(discord.ui.Button(label="Twitter", style=discord.ButtonStyle.link, url = 'https://x.com/TheArmaUnit'))
        buttons.add_item(discord.ui.Button(label="Instagram", style=discord.ButtonStyle.link, url = 'https://www.instagram.com/taw.armadivision/'))
        buttons.add_item(discord.ui.Button(label="Facebook", style=discord.ButtonStyle.link, url = 'https://www.facebook.com/tawarma'))
        buttons.add_item(discord.ui.Button(label="TikTok", style=discord.ButtonStyle.link, url = 'https://www.tiktok.com/@arma.division.taw?_t=8nqYd9WmCub&_r=1'))
        latest_reddit_url = await pull_recent_reddit_post_url("tawarmadivision")
        embed = discord.Embed(title="Social Links", description=f"Check out all of our social pages, be sure to give a follow and a like on our recent posts!", color=0xBEBEFE)
        embed.set_image(url="https://i.imgur.com/LOaHCV2.png")
        
        if post:
            await context.channel.send(content=f"**Be sure to like, share and follow our social media accounts!**\n Latest Reddit Post: {latest_reddit_url}\n", view=buttons)
            await context.send(content=f"Posted message to channel for everyone to see..", ephemeral=True)
        else:
            await context.send(content=f"**Be sure to like, share and follow our social media accounts!**\n Latest Reddit Post: {latest_reddit_url}\n", view=buttons, ephemeral=True)






async def setup(bot) -> None:
    await bot.add_cog(Helper(bot))


async def pull_recent_reddit_post_url(username: str):
    reddit = asyncpraw.Reddit(
        client_id=os.getenv("REDDIT_ID"),
        client_secret=os.getenv("REDDIT_SECRET"),
        user_agent="lookup latest submission by u/hbturpin"
    )

    user = await reddit.redditor(username)
    submissions = user.submissions.new(limit=1)

    async for link in submissions:
        return "https://www.reddit.com"+ link.permalink

