from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import aiohttp
from bs4 import BeautifulSoup
import asyncpraw
from datetime import datetime, timedelta, timezone
import pytz


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


    @commands.hybrid_command(
        name="donate",
        description="Get a link to the division's donation page",
    )
    @app_commands.describe(post="Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.")
    async def donate(self, context: Context, post: bool = False) -> None:
        """
        Get a link to all the division's donation pages.

        :param context: The hybrid command context.
        :param post: Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.
        """

        buttons = discord.ui.View()
        buttons.add_item(discord.ui.Button(label="Donate", style=discord.ButtonStyle.link, url='https://donations.taw.net/'))

        embed = discord.Embed(title="Arma Division Server Donation", description="""Every donation helps keep the community running, supports infrastructure costs, and allows us to continue improving the experience for everyone.""", color=0xBEBEFE)

        await context.send(embed=embed, view=buttons, ephemeral=(not post))


    @commands.hybrid_command(
        name="info",
        description="Get information related to a specific battalion.",
    )
    @app_commands.describe(post="Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.")
    @app_commands.choices(battalion=[discord.app_commands.Choice(name="1st Battalion (Reforger EU)", value="am1"), discord.app_commands.Choice(name="2nd Battalion (Arma 3 EU)", value="am2"),
                                     discord.app_commands.Choice(name="3rd Battalion (Arma 3 NA)", value="am3")])
    async def donate(self, context: Context, battalion: str = "am2", post: bool = False) -> None:
        """
        Get a link to all the division's donation pages.

        :param context: The hybrid command context.
        :param battalion: The battalion the user is interested in joining.
        :param post: Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.
        """

        if battalion == "am2":
            am2_non_cdlc_owner_link_direct = "https://drive.google.com/uc?export=download&id=15JpbnAF-R0yej2MILvozztJW76kRz77V"
            am2_cdlc_owner_link_direct = "https://drive.google.com/uc?export=download&id=1ZlPBnLEzuc42KZoEJwX83yQeekrRvWpJ"

            events = discord.Embed(title="Events", description="", color=0xBEBEFE)
            events.add_field(name="Operation Times", value=f"- Thursday @ {await get_next_event_timestamp(4,19,30, timeonly=True)}\n- Sunday @ {await get_next_event_timestamp(7,19,30, timeonly=True)}", inline=False)

            mods = discord.Embed(title="Mods", description="In order to join our events, you need to have downloaded the latest modpacks for that specific events. By default all our events will be using the `am2` .html presets which can be downloaded with the links below.", color=0xBEBEFE)
            mods.add_field(name="Required Mods", value=f"""You can download the our latest modpack here, selecting the Non-CDLC Owner preset if you do not own Western Sahara CDLC:
- [AM2 CDLC Owner Preset]({am2_cdlc_owner_link_direct})
- [AM2 Non-CDLC Owner Preset]({am2_non_cdlc_owner_link_direct})
Once downloaded, import it into your launcher and subscribe to all the dependencies.""", inline=False)
            mods.add_field(name="Clientside Mods", value=f"""You can also download additional clientside mods from our [Clientside Mods List](https://discord.com/channels/1001559795310010539/1322642580827148370), each of these mods can be loaded alongside the required mod presets.""", inline=False)
            mods.add_field(name="Common In-game Issues", value=f"""If you have any issues with joining the server, first check out this forum thread: [Common In-game Issues](https://discord.com/channels/1001559795310010539/1342204767568400454)""", inline=False)

            servers = discord.Embed(title="Servers", description="If you have installed and loaded our mods, you should be able to see all the servers directly from the main menu. There is no need to connect via direct connect anymore.", color=0xBEBEFE)

            documents = discord.Embed(title="Documentation", description="", color=0xBEBEFE)
            documents.add_field(name="Standard Operating Procedures", value="https://docs.google.com/document/d/1SF_UiHsLKDSrglceA-tWz4vIFMJdua_JsawbxBcQflk/edit?tab=t.pe95mvkty91z#heading=h.e8qc4qnlz1f3", inline=False)

            if post:
                await context.channel.send(content="# 2nd Battalion | Arma 3 | EU (AM2)", embeds=[events, mods, servers, documents])
                await context.send(content=f"Posted message to channel for everyone to see..", ephemeral=True)
            else:
                await context.send(content="# 2nd Battalion | Arma 3 | EU (AM2)", embeds=[events, mods, servers, documents], ephemeral=True)


    @commands.hybrid_command(name="signup", description="Get information on how to sign up to the unit.")
    @app_commands.describe(post="Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.")
    @app_commands.choices(battalion=[discord.app_commands.Choice(name="1st Battalion (Reforger EU)", value="am1"), discord.app_commands.Choice(name="2nd Battalion (Arma 3 EU)", value="am2"),
                                     discord.app_commands.Choice(name="3rd Battalion (Arma 3 NA)", value="am3")])
    async def signup(self, context: Context, battalion: str = None, post: bool = False) -> None:
        """
        Get information on how to sign up to the unit.

        :param context: The hybrid command context.
        :param battalion: The battalion the user is interested in joining.
        :param post: Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.
        """

        taw_join_info = discord.Embed(title="""Joining TAW""", description="""To begin your journey with TAW, visit [www.taw.net](https://taw.net/) and register for the Arma division. During the registration process, you will have the option to choose the battalion that best suits your preferences and schedule.\n
        Once you have completed the registration, it is crucial to reach out to the drill instructor of the selected battalion to initiate your onboarding process. They will guide you through the necessary steps and provide you with all the information you need to get started.\n
        Additionally, please ensure that you have downloaded and installed any required mods and TeamSpeak software in advance, as these are essential for participating in our operations. Each battalion may have specific requirements, so be sure to check the details provided during registration or contact your drill instructor for further assistance. Use `/signup [battalion]` to get more information on the battalion you are interested in joining.""",
        color=0xBEBEFE)

        operations = discord.Embed(title="""Operation Times""", description="""Check out the operations schedule for each battalion below, its recommended to be there atleast 15 minutes before:""", color=0xBEBEFE)
        operations.add_field(name="1st Battalion | Arma Reforger | EU (AM1)", value=f"- Tuesday @ {await get_next_event_timestamp(2,19,30)}\n- Thursday @ {await get_next_event_timestamp(4,19,30)}\n- Sunday (Optional) @ {await get_next_event_timestamp(7,19,30)}", inline=False)
        operations.add_field(name="2nd Battalion | Arma 3 | EU (AM2)", value=f"- Thursday @ {await get_next_event_timestamp(4,19,30)}\n- Sunday @ {await get_next_event_timestamp(7,19,30)}", inline=False)

        if post:
            await context.channel.send(embeds=[taw_join_info, operations])
            await context.send(content=f"Posted message to channel for everyone to see..", ephemeral=True)
        else:
            await context.send(embeds=[taw_join_info, operations], ephemeral=True)


    @commands.hybrid_command(name="form", description="Get a link to the battalion-wide form and responses.", )
    async def form(self, context: Context) -> None:
        """
        Get a link to the battalion-wide form and responses.

        :param context: The hybrid command context.
        """
        buttons = discord.ui.View()
        buttons.add_item(discord.ui.Button(label="Battalion Form", style=discord.ButtonStyle.link, url='https://docs.google.com/forms/d/e/1FAIpQLSdykee7E-75ldEvuQ4SYBT1H1dG2v4sFCG5j85dDJYcPVDgUA/viewform'))
        buttons.add_item(discord.ui.Button(label="Responses", style=discord.ButtonStyle.link, url='https://docs.google.com/spreadsheets/d/1tkhHPKfpW2JxpQZq4vGv0gbVgtAIVgTiJkBoMc33d9E/edit?gid=1029717932#gid=1029717932'))

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


    @commands.hybrid_command(name="social", description="Get a link to all the battalion's social pages", )
    async def social(self, context: Context, post: bool = False) -> None:
        """
        Get a link to all the battalion's social pages

        :param context: The hybrid command context.
        :param post: Whether to post the message in the channel for everyone to see, or just send it as an ephemeral message to the user.
        """
        latest_reddit_url = await pull_recent_reddit_post_url(self, "tawarmadivision")

        buttons = discord.ui.View()
        buttons.add_item(discord.ui.Button(label="Twitch", style=discord.ButtonStyle.link, url='https://www.twitch.tv/tawarmadivision'))
        buttons.add_item(discord.ui.Button(label="Reddit", style=discord.ButtonStyle.link, url=latest_reddit_url))
        buttons.add_item(discord.ui.Button(label="Twitter", style=discord.ButtonStyle.link, url='https://x.com/TheArmaUnit'))
        buttons.add_item(discord.ui.Button(label="Instagram", style=discord.ButtonStyle.link, url='https://www.instagram.com/taw.armadivision/'))
        buttons.add_item(discord.ui.Button(label="Facebook", style=discord.ButtonStyle.link, url='https://www.facebook.com/tawarma'))
        buttons.add_item(discord.ui.Button(label="TikTok", style=discord.ButtonStyle.link, url='https://www.tiktok.com/@arma.division.taw?_t=8nqYd9WmCub&_r=1'))

        embed = discord.Embed(title="Social Links", description=f"Check out all of our social pages, be sure to give a follow and a like on our recent posts!", color=0xBEBEFE)
        embed.set_image(url="https://i.imgur.com/LOaHCV2.png")

        if post:
            await context.channel.send(content=f"**Be sure to like, share and follow our social media accounts!**\n Latest Reddit Post: {latest_reddit_url}\n", view=buttons)
            await context.send(content=f"Posted message to channel for everyone to see..", ephemeral=True)
        else:
            await context.send(content=f"**Be sure to like, share and follow our social media accounts!**\n Latest Reddit Post: {latest_reddit_url}\n", view=buttons, ephemeral=True)



async def setup(bot) -> None:
    await bot.add_cog(TAW(bot))


async def get_next_event_timestamp(day: int = 4, hour: int = 19, minute: int = 0, timeonly: bool = False) -> str:

    time_now = datetime.now(pytz.timezone('Europe/London'))
    time_event = time_now + (timedelta(days=((day-1) - time_now.weekday()) % 7))
    time_event = time_event.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if time_event < time_now:
        time_event += timedelta(days=7)

    if timeonly:
        timestamp = f"<t:{round(time_event.timestamp())}:t>"
    else:
        timestamp = f"<t:{round(time_event.timestamp())}:f> (<t:{round(time_event.timestamp())}:R>)"

    return timestamp


async def pull_recent_reddit_post_url(self, username: str):
    reddit = asyncpraw.Reddit(client_id=self.bot.config['reddit']['id'], client_secret=self.bot.config['reddit']['secret'], user_agent="lookup latest submission by u/hbturpin")

    user = await reddit.redditor(username)
    submissions = user.submissions.new(limit=1)

    async for link in submissions:
        return "https://www.reddit.com" + link.permalink
    else:
        return 'https://www.reddit.com/user/tawarmadivision/'


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
                await session.close()
                return None
            
            # Locate the <h2> and ensure it contains "Bio:"
            bio_header = bio_div.find("h2", text="Bio:")
            print("bio_header:",bio_header)
            if not bio_header:
                await session.close()
                return None
            
            # Locate the <p> tag and the <code> tag inside it
            bio_paragraph = bio_div.find("p")
            print("bio_para:",bio_paragraph)
            if not bio_paragraph:
                await session.close()
                return None
            
            code_tag = bio_paragraph.find("code", class_="inline")
            print("code_tag:",code_tag)
            if not code_tag:
                await session.close()
                return None
            
            # Return the text within the <code> tag
            await session.close()
            return code_tag.get_text(strip=True)

