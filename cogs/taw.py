from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import aiohttp
from bs4 import BeautifulSoup

class TAW(commands.Cog, name="taw"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="taw_link", 
        description="Link your TAW account to your discord.",
    )
    async def taw_link(self, context: Context, username: str) -> None:

        embed = discord.Embed(title="TAW Account Linking", description=f"You currently cannot link your TAW account to your discord, this is being developed and will be available soon.", color=0xFF2B2B)
        await context.send(embed=embed, ephemeral=True)
        return

        
        account_linked = False
        if account_linked:
            linked_username = "TESTCHANGELATER"
            embed = discord.Embed(title="TAW Account Linking", description=f"You have already linked your account to {linked_username}, you can unlink your account using `/taw_unlink`. If you have any issues please contact the Server Administrator.", color=0xBEBEFE)
        else:
            embed = discord.Embed(title="TAW Account Linking", description=f"In order to link your TAW account `{username}` to your this discord, you need to add the following authentication key to your taw biography.\n\nThis has been private messaged to you with steps on how to do it.", color=0xBEBEFE)

        # bio = await scrape_bio_text(username)
        #print(bio)
        
        await context.send(embed=embed, ephemeral=True)



async def setup(bot) -> None:
    await bot.add_cog(TAW(bot))

async def scrape_bio_text(taw_username):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://taw.net/member/{taw_username}.aspx") as response:
            if response.status != 200:
                raise ValueError(f"Failed to fetch the page, status code: {response.status}")
            
            # Parse the HTML content
            page_content = await response.text()
            soup = BeautifulSoup(page_content, "html.parser")
            
            
            # Locate the <div> containing the bio
            bio_div = soup.find("div", id="dossierbio")
            print("bio_div:",bio_div)
            if not bio_div:
                return None
            
            # Locate the <h2> and ensure it contains "Bio:"
            bio_header = bio_div.find("h2", text="Bio:")
            print("bio_header:",bio_header)
            if not bio_header:
                return None
            
            # Locate the <p> tag and the <code> tag inside it
            bio_paragraph = bio_div.find("p")
            print("bio_para:",bio_paragraph)
            if not bio_paragraph:
                return None
            
            code_tag = bio_paragraph.find("code", class_="inline")
            print("code_tag:",code_tag)
            if not code_tag:
                return None
            
            # Return the text within the <code> tag
            return code_tag.get_text(strip=True)

