from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import asyncpraw
from datetime import datetime, timedelta, timezone
import pytz

class Donations(commands.Cog, name="donations"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="donate",
        description="Get a link to the division's donation page",
    )
    @app_commands.describe(post="Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.")
    async def donate(self, context: Context, post: bool=False) -> None:
        """
        Get a link to all the division's donation pages.

        :param context: The hybrid command context.
        :param post: Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.
        """

        buttons = discord.ui.View()
        buttons.add_item(discord.ui.Button(label="Donate", style=discord.ButtonStyle.link, url='https://donations.taw.net/'))

        embed = discord.Embed(title="Arma Division Server Donation", description="""Every donation helps keep the community running, supports infrastructure costs, and allows us to continue improving the experience for everyone.""", color=0xBEBEFE)
        ##embed.add_field(name=f"""Battalion Suggestions""", value=f"""Send in your complaints, ideas and improvements here for BATCOM to review and address.""", inline=True)

        await context.send(embed=embed, view=buttons, ephemeral=(not post))


async def setup(bot) -> None:
    await bot.add_cog(Donations(bot))

